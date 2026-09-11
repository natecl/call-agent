import asyncio
import base64
import json
import struct
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from call_agent.config import Settings
from call_agent.domain import (
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
from call_agent.server import app

CALL_CONTROL_ID = CallControlId("v3:abc")


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
        "transcript_ref": "v3:abc/transcript.json",
        "audio_ref": "v3:abc/audio/inbound.wav",
        "reason": "Telnyx media streaming failed: connection_failed",
    }
