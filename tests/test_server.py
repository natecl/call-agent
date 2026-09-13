import asyncio
import base64
import json
import socket
import struct
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from call_agent.cli import _run_request
from call_agent.config import Settings
from call_agent.domain import (
    BookRequest,
    Call,
    CallControlId,
    CallEvent,
    ClientState,
    Codec,
    DialPlaced,
    DialSpec,
    E164,
    FirstName,
    LiveStarting,
    MediaStartEvent,
    MediaStopEvent,
    MediaStreaming,
    Mode,
    Question,
    QuestionType,
    QuickAskRequest,
    RunId,
    StreamId,
    TelnyxAnswered,
    UserId,
    WebhookEvent,
)
from call_agent.runtime import BOOK, CallHandle, RunDir, build_ports
from call_agent.server import (
    app,
    probe_live_ingress,
    serve_in_process,
    set_serve_token,
)

CALL_CONTROL_ID = CallControlId("v3:abc")


def unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(autouse=True)
def reset_serve_token() -> Iterator[None]:
    set_serve_token(None)
    yield
    set_serve_token(None)


def test_health_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.fixture
def answered_handle(settings: Settings) -> Iterator[CallHandle]:
    request = QuickAskRequest(
        user_id=UserId("user-1"),
        to=E164("+14155552671"),
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
    )
    call = Call(
        run_id=RunId("run-1"),
        request=request,
        from_did=E164("+15555550100"),
        dial=DialPlaced(
            call_control_id=CALL_CONTROL_ID,
            call_leg_id="leg-1",
            call_session_id="session-1",
        ),
        telnyx=TelnyxAnswered(),
    )
    queue: asyncio.Queue[CallEvent] = asyncio.Queue()
    run_dir = RunDir(settings.call_agent_artifacts_dir, str(CALL_CONTROL_ID))
    handle = CallHandle(
        call=call,
        queue=queue,
        ports=build_ports(Mode.STUB, settings, queue.put_nowait, run_dir),
        run_dir=run_dir,
        settings=settings,
        dial_spec=DialSpec(
            connection_id="connection-1",
            to=request.to,
            from_did=call.from_did,
            stream_url="wss://calls.example.test/media-stream",
            webhook_url="https://calls.example.test/webhooks/telnyx",
            codec=Codec.L16,
            client_state=ClientState(run_id=call.run_id, user_id=request.user_id),
        ),
    )
    BOOK.bind(CALL_CONTROL_ID, handle)
    yield handle
    BOOK.drop(CALL_CONTROL_ID)


def test_media_stream_opens_live_when_connected_precedes_start(
    answered_handle: CallHandle,
) -> None:
    client = TestClient(app)
    with client.websocket_connect("/media-stream") as websocket:
        websocket.send_text(
            json.dumps({"event": "connected", "version": "1.0.0"})
        )
        websocket.send_text(
            json.dumps(
                {
                    "event": "start",
                    "sequence_number": "1",
                    "stream_id": "stream-1",
                    "start": {
                        "call_control_id": str(CALL_CONTROL_ID),
                        "media_format": {
                            "encoding": "L16",
                            "sample_rate": 16000,
                            "channels": 1,
                        },
                    },
                }
            )
        )

    event = answered_handle.queue.get_nowait()
    asyncio.run(answered_handle._process_event(event))
    session_start = json.loads(
        (answered_handle.run_dir.path / "session_start.json").read_text()
    )

    assert event == MediaStartEvent(
        stream_id=StreamId("stream-1"),
        call_control_id=CALL_CONTROL_ID,
        codec=Codec.L16,
        sample_rate=16000,
    )
    assert answered_handle.call.live == LiveStarting()
    assert session_start["type"] == "session.start"
    assert session_start["session"]["model"] == "gpt-live-1"
    assert session_start["session"]["audio"] == {
        "format": {"type": "audio/pcm", "rate": 16000},
        "output": {"voice": "marin"},
    }


def test_media_stream_disconnect_without_stop_submits_media_stop(
    answered_handle: CallHandle,
) -> None:
    client = TestClient(app)
    with client.websocket_connect("/media-stream") as websocket:
        websocket.send_text(
            json.dumps({"event": "connected", "version": "1.0.0"})
        )
        websocket.send_text(
            json.dumps(
                {
                    "event": "start",
                    "sequence_number": "1",
                    "stream_id": "stream-1",
                    "start": {
                        "call_control_id": str(CALL_CONTROL_ID),
                        "media_format": {
                            "encoding": "L16",
                            "sample_rate": 16000,
                            "channels": 1,
                        },
                    },
                }
            )
        )

    start_event = answered_handle.queue.get_nowait()
    stop_event = answered_handle.queue.get_nowait()

    assert start_event == MediaStartEvent(
        stream_id=StreamId("stream-1"),
        call_control_id=CALL_CONTROL_ID,
        codec=Codec.L16,
        sample_rate=16000,
    )
    assert stop_event == MediaStopEvent(stream_id=StreamId("stream-1"))
    with pytest.raises(asyncio.QueueEmpty):
        answered_handle.queue.get_nowait()


def test_media_stream_accepts_8k_l16_start_and_opens_live_at_16k(
    answered_handle: CallHandle,
) -> None:
    client = TestClient(app)
    with client.websocket_connect("/media-stream") as websocket:
        websocket.send_text(
            json.dumps({"event": "connected", "version": "1.0.0"})
        )
        websocket.send_text(
            json.dumps(
                {
                    "event": "start",
                    "sequence_number": "1",
                    "stream_id": "stream-1",
                    "start": {
                        "call_control_id": str(CALL_CONTROL_ID),
                        "media_format": {
                            "encoding": "L16",
                            "sample_rate": 8000,
                            "channels": 1,
                        },
                    },
                }
            )
        )
        websocket.send_text(
            json.dumps(
                {
                    "event": "media",
                    "sequence_number": "2",
                    "stream_id": "stream-1",
                    "media": {
                        "payload": base64.b64encode(
                            struct.pack("<4h", 0, 100, -100, 50)
                        ).decode(),
                        "track": "inbound",
                        "chunk": 1,
                        "timestamp": "20",
                    },
                }
            )
        )

    start_event = answered_handle.queue.get_nowait()
    asyncio.run(answered_handle._process_event(start_event))
    chunk_event = answered_handle.queue.get_nowait()
    asyncio.run(answered_handle._process_event(chunk_event))
    session_start = json.loads(
        (answered_handle.run_dir.path / "session_start.json").read_text()
    )

    assert start_event == MediaStartEvent(
        stream_id=StreamId("stream-1"),
        call_control_id=CALL_CONTROL_ID,
        codec=Codec.L16,
        sample_rate=8000,
    )
    assert answered_handle.call.media == MediaStreaming(
        stream_id=StreamId("stream-1"),
        codec=Codec.L16,
        sample_rate=8000,
    )
    assert answered_handle.call.live == LiveStarting(
        buffered_audio=(
            base64.b64encode(
                struct.pack("<8h", 0, 50, 100, 0, -100, -25, 50, 50)
            ).decode(),
        )
    )
    assert session_start["session"]["audio"] == {
        "format": {"type": "audio/pcm", "rate": 16000},
        "output": {"voice": "marin"},
    }


def test_streaming_failed_webhook_hangs_up_and_fails_the_call(
    answered_handle: CallHandle,
) -> None:
    client = TestClient(app)
    response = client.post(
        "/webhooks/telnyx",
        json={
            "data": {
                "event_type": "streaming.failed",
                "id": "webhook-failed-1",
                "occurred_at": "2026-09-11T00:00:00Z",
                "record_type": "event",
                "payload": {
                    "call_control_id": str(CALL_CONTROL_ID),
                    "call_leg_id": "leg-1",
                    "call_session_id": "session-1",
                    "connection_id": "connection-1",
                    "client_state": "opaque",
                    "failure_reason": "connection_failed",
                    "stream_id": "stream-1",
                    "stream_type": "websocket",
                    "stream_params": {
                        "stream_url": "wss://calls.example.test/media-stream",
                        "track": "inbound_track",
                    },
                },
            }
        },
    )

    event = answered_handle.queue.get_nowait()
    asyncio.run(answered_handle._process_event(event))
    run_path = answered_handle.run_dir.path
    hangup = json.loads((run_path / "hangup.json").read_text())
    result = json.loads((run_path / "result.json").read_text())

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert event == WebhookEvent(
        webhook_id="webhook-failed-1",
        event_type="streaming.failed",
        call_control_id=CALL_CONTROL_ID,
        failure_reason="connection_failed",
    )
    assert hangup == {"call_control_id": "v3:abc"}
    assert result == {
        "status": "failed",
        "answer_text": None,
        "call_control_id": "v3:abc",
        "transcript_ref": "v3_abc/transcript.json",
        "audio_ref": "v3_abc/audio/inbound.wav",
        "reason": "Telnyx media streaming failed: connection_failed",
    }


def unknown_call_webhook() -> dict[str, object]:
    return {
        "data": {
            "event_type": "call.initiated",
            "id": "webhook-unknown-1",
            "occurred_at": "2026-09-12T00:00:00Z",
            "record_type": "event",
            "payload": {
                "call_control_id": "v3:unknown-call",
                "call_leg_id": "leg-unknown",
                "call_session_id": "session-unknown",
                "connection_id": "connection-1",
                "client_state": "opaque",
            },
        }
    }


def test_unknown_call_webhook_returns_404() -> None:
    client = TestClient(app)
    response = client.post("/webhooks/telnyx", json=unknown_call_webhook())

    assert response.status_code == 404
    assert response.json() == {"detail": "unknown call"}


def live_settings(
    workspace_settings: Settings, port: int, **overrides: object
) -> Settings:
    public_base = f"http://127.0.0.1:{port}"
    values: dict[str, object] = {
        "call_agent_db_path": workspace_settings.call_agent_db_path,
        "call_agent_artifacts_dir": workspace_settings.call_agent_artifacts_dir,
        "port": port,
        "quick_ask_live": True,
        "telnyx_api_key": "test-key",
        "telnyx_connection_id": "connection-1",
        "telnyx_from_number": "+12135690899",
        "openai_api_key": "test-openai",
        "openai_responses_model": "gpt-5.6-terra",
        "public_base_url": public_base,
        "public_webhook_url": public_base,
        "live_ingress_timeout_s": 0.4,
    }
    values.update(overrides)
    if "public_webhook_url" not in overrides:
        values["public_webhook_url"] = values["public_base_url"]
    return Settings(_env_file=None).model_copy(update=values)


async def test_serve_in_process_returns_only_after_health_accepts(
    settings: Settings,
) -> None:
    port = unused_port()
    running = await serve_in_process(live_settings(settings, port))
    try:
        assert running.server.started is True
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://127.0.0.1:{port}/health")
        payload = response.json()
        assert response.status_code == 200
        assert payload["ok"] is True
        assert payload["serve_token"] == running.serve_token
    finally:
        await running.stop()
    assert TestClient(app).get("/health").json() == {"ok": True}


async def test_probe_live_ingress_accepts_this_process_health_and_media(
    settings: Settings,
) -> None:
    port = unused_port()
    public_base = f"http://127.0.0.1:{port}"
    running = await serve_in_process(live_settings(settings, port))
    try:
        await probe_live_ingress(
            public_base,
            running.serve_token,
            timeout_s=2.0,
        )
    finally:
        await running.stop()


async def test_probe_live_ingress_rejects_ok_health_from_another_process(
    settings: Settings,
) -> None:
    port = unused_port()
    running = await serve_in_process(live_settings(settings, port))
    try:
        with pytest.raises(ValueError, match="serve_token does not match"):
            await probe_live_ingress(
                f"http://127.0.0.1:{port}",
                "not-this-process",
                timeout_s=0.4,
            )
    finally:
        await running.stop()


async def test_probe_live_ingress_retries_until_health_returns_token() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503, json={"ok": False})
        return httpx.Response(
            200, json={"ok": True, "serve_token": "settled-token"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await probe_live_ingress(
            "https://tunnel.example.test",
            "settled-token",
            timeout_s=2.0,
            client=client,
            probe_websocket=False,
        )
    finally:
        await client.aclose()
    assert attempts["n"] == 3


def _live_book_request() -> BookRequest:
    return BookRequest(
        user_id=UserId("user-1"),
        to=E164("+14155552671"),
        caller=FirstName("Nathan"),
        mode=Mode.LIVE,
        party_size=2,
        time_text="Friday 7pm",
    )


_LIVE_FROM_DID = E164("+15555550100")


def _port_is_closed(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.2)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            return True
    return False


async def test_live_request_fails_loud_when_public_base_misses_this_process(
    settings: Settings,
) -> None:
    port = unused_port()
    live = live_settings(
        settings,
        port,
        public_base_url="http://127.0.0.1:9",
        live_ingress_timeout_s=0.3,
    )
    with pytest.raises(ValueError, match="did not reach this process"):
        await _run_request(_live_book_request(), live, _LIVE_FROM_DID)


async def test_live_request_fails_loud_when_webhook_host_diverges(
    settings: Settings,
) -> None:
    port = unused_port()
    live = live_settings(
        settings,
        port,
        public_base_url="https://hidden-beacon-080.fly.dev",
        public_webhook_url="https://stale-tunnel.trycloudflare.com",
    )
    with pytest.raises(ValueError, match="webhook host"):
        await _run_request(_live_book_request(), live, _LIVE_FROM_DID)
    assert _port_is_closed(port)


async def test_live_request_fails_loud_when_webhook_url_unset(
    settings: Settings,
) -> None:
    port = unused_port()
    live = live_settings(settings, port, public_webhook_url=None)
    with pytest.raises(ValueError, match="PUBLIC_WEBHOOK_URL"):
        await _run_request(_live_book_request(), live, _LIVE_FROM_DID)
    assert _port_is_closed(port)


async def test_unknown_call_webhook_returns_404_on_in_process_listener(
    settings: Settings,
) -> None:
    port = unused_port()
    running = await serve_in_process(live_settings(settings, port))
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"http://127.0.0.1:{port}/webhooks/telnyx",
                json=unknown_call_webhook(),
            )
        assert response.status_code == 404
        assert response.json() == {"detail": "unknown call"}
    finally:
        await running.stop()
