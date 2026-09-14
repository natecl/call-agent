import asyncio
import base64
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from call_agent.config import Settings
from call_agent.domain import (
    CLOSING_LINE,
    BookRequest,
    Call,
    CallEvent,
    ClientState,
    Codec,
    ConversationOpen,
    DialPlaced,
    DialPlacedEvent,
    DialSpec,
    E164,
    FirstName,
    LiveActive,
    LiveOutputAudioEvent,
    LiveOutputTranscriptEvent,
    LiveSessionId,
    MediaStreaming,
    Mode,
    Question,
    QuestionType,
    QuickAskRequest,
    RunId,
    Speaker,
    StreamId,
    TelnyxAnswered,
    Transcript,
    UserId,
)
from call_agent.gpt_live import StubGptLive, StubScenario
from call_agent.runtime import (
    CallHandle,
    Ports,
    RunDir,
    TelnyxFromConfigError,
    assert_live_dial_origins,
    normalized_public_host,
    public_url_host,
    resolve_telnyx_from,
    run_call,
    run_quick_ask,
    shared_telnyx_from,
)
from call_agent.telnyx import StubTelnyxHttp, parse_media_frame
from call_agent.artifacts import artifact_dir, artifact_slug
from call_agent.user_text import result_text

AUDIO = base64.b64encode(bytes(640)).decode()


class HeldMarks:
    def __init__(self, submit: Callable[[CallEvent], None]) -> None:
        self._submit = submit
        self.played: list[str] = []
        self.marks: list[str] = []

    async def play(self, payload_b64: str) -> None:
        self.played.append(payload_b64)

    async def mark(self, name: str) -> None:
        self.marks.append(name)

    async def close(self) -> None:
        return None

    def echo(self, name: str) -> None:
        self._submit(
            parse_media_frame(
                json.dumps(
                    {
                        "event": "mark",
                        "stream_id": "stream-1",
                        "sequence_number": "9",
                        "mark": {"name": name},
                    }
                )
            )
        )


def speaking_handle(settings: Settings) -> tuple[CallHandle, HeldMarks]:
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
            call_control_id="v3:abc",
            call_leg_id="leg-1",
            call_session_id="session-1",
        ),
        telnyx=TelnyxAnswered(),
        media=MediaStreaming(
            stream_id=StreamId("stream-1"),
            codec=Codec.L16,
            sample_rate=16000,
        ),
        live=LiveActive(
            session_id=LiveSessionId("live-1"),
            stall_check_pending=True,
            activity_seq=1,
        ),
        conversation=ConversationOpen(
            transcript=Transcript().append_delta(
                Speaker.BUSINESS, "We're open 9 to 5."
            )
        ),
    )
    queue: asyncio.Queue[CallEvent] = asyncio.Queue()
    media = HeldMarks(queue.put_nowait)
    handle = CallHandle(
        call=call,
        queue=queue,
        ports=Ports(
            telnyx_http=StubTelnyxHttp(submit=queue.put_nowait),
            telnyx_media=media,
            gpt_live=StubGptLive(submit=queue.put_nowait),
        ),
        run_dir=RunDir(settings.call_agent_artifacts_dir, "v3:abc"),
        settings=settings,
        dial_spec=DialSpec(
            connection_id="connection-1",
            to=request.to,
            from_did=call.from_did,
            stream_url="wss://calls.example.test/media-stream",
            webhook_url="https://calls.example.test/webhooks/telnyx",
            codec=Codec.L16,
            client_state=ClientState(
                run_id=call.run_id, user_id=request.user_id
            ),
        ),
    )
    return handle, media


def speaking_book_handle(settings: Settings) -> tuple[CallHandle, HeldMarks]:
    request = BookRequest(
        user_id=UserId("user-1"),
        to=E164("+14155552671"),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
        party_size=2,
        time_text="Friday 7:30pm",
    )
    call = Call(
        run_id=RunId("run-1"),
        request=request,
        from_did=E164("+15555550100"),
        dial=DialPlaced(
            call_control_id="v3:abc",
            call_leg_id="leg-1",
            call_session_id="session-1",
        ),
        telnyx=TelnyxAnswered(),
        media=MediaStreaming(
            stream_id=StreamId("stream-1"),
            codec=Codec.L16,
            sample_rate=16000,
        ),
        live=LiveActive(
            session_id=LiveSessionId("live-1"),
            stall_check_pending=True,
            activity_seq=1,
        ),
        conversation=ConversationOpen(
            transcript=Transcript().append_delta(
                Speaker.BUSINESS, "Yes, we can do that."
            )
        ),
    )
    queue: asyncio.Queue[CallEvent] = asyncio.Queue()
    media = HeldMarks(queue.put_nowait)
    handle = CallHandle(
        call=call,
        queue=queue,
        ports=Ports(
            telnyx_http=StubTelnyxHttp(submit=queue.put_nowait),
            telnyx_media=media,
            gpt_live=StubGptLive(submit=queue.put_nowait),
        ),
        run_dir=RunDir(settings.call_agent_artifacts_dir, "v3:abc"),
        settings=settings,
        dial_spec=DialSpec(
            connection_id="connection-1",
            to=request.to,
            from_did=call.from_did,
            stream_url="wss://calls.example.test/media-stream",
            webhook_url="https://calls.example.test/webhooks/telnyx",
            codec=Codec.L16,
            client_state=ClientState(
                run_id=call.run_id, user_id=request.user_id
            ),
        ),
    )
    return handle, media


async def drain_queue(handle: CallHandle) -> None:
    while not handle.queue.empty():
        await handle._process_event(handle.queue.get_nowait())


async def test_verdict_closes_live_and_neither_marks_nor_hangs_up_while_audio_is_in_flight(
    settings: Settings,
) -> None:
    handle, media = speaking_handle(settings)

    await handle._process_event(LiveOutputAudioEvent(delta_b64=AUDIO))
    await handle._process_event(LiveOutputTranscriptEvent(delta=CLOSING_LINE))

    assert not (handle.run_dir.path / "hangup.json").exists()
    assert media.played == [AUDIO]
    assert media.marks == []
    assert handle.call.hangup.model_dump(mode="json") == {
        "kind": "hangup_draining",
        "via": "trail",
    }
    assert handle.call.live.model_dump(mode="json") == {"kind": "live_closing"}


async def test_goodbye_audio_storm_hangs_up_through_one_final_mark_and_the_trail(
    settings: Settings,
) -> None:
    handle, media = speaking_handle(settings)
    hangup_json = handle.run_dir.path / "hangup.json"

    await handle._process_event(LiveOutputTranscriptEvent(delta=CLOSING_LINE))
    for _ in range(98):
        await handle._process_event(LiveOutputAudioEvent(delta_b64=AUDIO))
    await drain_queue(handle)
    marks_after_live_closed = list(media.marks)
    played_before_mark = len(media.played)
    media.echo("hangup")
    await handle._process_event(handle.queue.get_nowait())
    trail = await asyncio.wait_for(handle.queue.get(), 1)
    hung_up_before_trail = hangup_json.exists()
    await handle._process_event(trail)
    hangup = json.loads(hangup_json.read_text())
    while not handle.call.finished:
        await handle._process_event(handle.queue.get_nowait())
    result = json.loads((handle.run_dir.path / "result.json").read_text())
    rows = [
        json.loads(line)
        for line in (handle.run_dir.path / "events.jsonl")
        .read_text()
        .splitlines()
    ]
    kinds = [row.get("kind") for row in rows]
    report = json.loads(
        (handle.run_dir.path / "call_report.json").read_text()
    )

    assert marks_after_live_closed == ["hangup"]
    assert played_before_mark == 99
    assert media.marks == ["hangup"]
    assert trail.model_dump(mode="json") == {"kind": "hangup_trail_elapsed"}
    assert hung_up_before_trail is False
    assert hangup == {"call_control_id": "v3:abc"}
    assert "hangup_drain_timeout" not in kinds
    assert handle.call.hangup.model_dump(mode="json") == {
        "kind": "hangup_confirmed",
        "via": "trail",
    }
    assert result["status"] == "answered"
    assert result["answer_text"] == "We're open 9 to 5."
    assert [row["effect"] for row in rows if "effect" in row][-6:] == [
        "play_audio",
        "mark",
        "schedule",
        "hangup",
        "schedule",
        "finish",
    ]
    assert report["hangup"]["path"] == "trail"
    assert report["counts"]["marks_sent"] == 1
    assert report["counts"]["output_audio_chunks_after_verdict"] == 0
    assert report["timing_ms"]["final_mark_sent"] <= report["timing_ms"][
        "final_mark_echoed"
    ] <= report["timing_ms"]["hangup_sent"] <= report["timing_ms"]["call_ended"]


async def test_book_goodbye_without_booked_needle_hangs_up_through_the_trail(
    settings: Settings,
) -> None:
    handle, media = speaking_book_handle(settings)
    hangup_json = handle.run_dir.path / "hangup.json"

    await handle._process_event(LiveOutputTranscriptEvent(delta=CLOSING_LINE))
    await drain_queue(handle)
    marks_after_live_closed = list(media.marks)
    media.echo("hangup")
    await handle._process_event(handle.queue.get_nowait())
    trail = await asyncio.wait_for(handle.queue.get(), 1)
    await handle._process_event(trail)
    while not handle.call.finished:
        await handle._process_event(handle.queue.get_nowait())
    result = json.loads((handle.run_dir.path / "result.json").read_text())
    report = json.loads(
        (handle.run_dir.path / "call_report.json").read_text()
    )

    assert marks_after_live_closed == ["hangup"]
    assert hangup_json.is_file()
    assert trail.model_dump(mode="json") == {"kind": "hangup_trail_elapsed"}
    assert handle.call.hangup.model_dump(mode="json") == {
        "kind": "hangup_confirmed",
        "via": "trail",
    }
    assert result["status"] == "failed"
    assert report["hangup"]["path"] == "trail"
    assert report["counts"]["marks_sent"] == 1
    assert report["timing_ms"]["hangup_sent"] is not None
    assert report["timing_ms"].get("verdict") is None


async def test_drain_timeout_hangs_up_anyway_and_journals_the_timeout(
    workspace: SimpleNamespace,
) -> None:
    settings = Settings(
        _env_file=None,
        call_agent_db_path=workspace.db_path,
        call_agent_artifacts_dir=workspace.artifacts_dir,
        hangup_drain_timeout_s=0.0,
    )
    handle, media = speaking_handle(settings)
    hangup_json = handle.run_dir.path / "hangup.json"

    await handle._process_event(LiveOutputTranscriptEvent(delta=CLOSING_LINE))
    await drain_queue(handle)
    timeout = await asyncio.wait_for(handle.queue.get(), 1)
    hung_up_before_timeout = hangup_json.exists()
    await handle._process_event(timeout)
    kinds = [
        json.loads(line).get("kind")
        for line in (handle.run_dir.path / "events.jsonl")
        .read_text()
        .splitlines()
    ]

    assert hung_up_before_timeout is False
    assert timeout.model_dump(mode="json") == {"kind": "hangup_drain_timeout"}
    assert media.marks == ["hangup"]
    assert json.loads(hangup_json.read_text()) == {"call_control_id": "v3:abc"}
    assert [kind for kind in kinds if kind is not None] == [
        "live_output_transcript",
        "live_output_audio",
        "live_closed",
        "hangup_drain_timeout",
    ]
    assert handle.call.hangup.model_dump(mode="json") == {
        "kind": "hangup_requested",
        "via": "drain_timeout",
    }


async def test_dial_uses_worker_webhook_and_tunnel_media(
    settings: Settings,
) -> None:
    split = Settings(
        _env_file=None,
        call_agent_db_path=settings.call_agent_db_path,
        call_agent_artifacts_dir=settings.call_agent_artifacts_dir,
        public_base_url="https://tunnel.example.test",
        public_webhook_url=(
            "https://call-agent-webhooks.example.workers.dev"
        ),
        retain_call_artifacts=True,
    )
    request = QuickAskRequest(
        user_id=UserId("user-1"),
        to=E164("+14155552671"),
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
    )
    result = await run_quick_ask(
        request, split, from_did=E164("+15555550100")
    )
    dial = json.loads(
        (
            split.call_agent_artifacts_dir
            / result.call_control_id
            / "dial.json"
        ).read_text()
    )
    assert dial["webhook_url"] == (
        "https://call-agent-webhooks.example.workers.dev/webhooks/telnyx"
    )
    assert dial["stream_url"] == "wss://tunnel.example.test/media-stream"


def test_public_url_host_strips_path_and_keeps_port() -> None:
    assert public_url_host(
        "https://specs-publicity-singing-money.trycloudflare.com/webhooks/telnyx"
    ) == "specs-publicity-singing-money.trycloudflare.com"
    assert public_url_host("wss://tunnel.example.test:8443/media-stream") == (
        "tunnel.example.test:8443"
    )


class DialOnlyTelnyx:
    def __init__(self) -> None:
        self.hung_up = None

    def dial(self, spec: DialSpec) -> DialPlacedEvent:
        return DialPlacedEvent(
            call_control_id="v3:timeout",
            call_leg_id="leg-1",
            call_session_id="session-1",
        )

    def hangup(self, call_control_id: str) -> None:
        self.hung_up = call_control_id


class SilentGptLive:
    async def start(self, spec: object) -> None:
        return None

    async def speak_first(self, line: str) -> None:
        return None

    async def append_audio(self, payload_b64: str) -> None:
        return None

    async def reply_delegation(
        self, delegation_id: str, thinking: str, commentary: str
    ) -> None:
        return None

    async def send_tool_result(
        self, call_id: str, output: str, event_id: str
    ) -> None:
        return None

    async def continue_response(self, event_id: str) -> None:
        return None

    async def steer(self, instructions: str, event_id: str) -> None:
        return None

    async def close(self) -> None:
        return None


async def test_timeout_after_dial_journals_delivery_hosts(
    settings: Settings,
) -> None:
    timed = Settings(
        _env_file=None,
        call_agent_db_path=settings.call_agent_db_path,
        call_agent_artifacts_dir=settings.call_agent_artifacts_dir,
        event_timeout_s=0.05,
    )
    request = QuickAskRequest(
        user_id=UserId("user-1"),
        to=E164("+14155552671"),
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
    )
    call = Call(
        run_id=RunId("run-timeout"),
        request=request,
        from_did=E164("+15555550100"),
    )
    queue: asyncio.Queue[CallEvent] = asyncio.Queue()
    telnyx = DialOnlyTelnyx()
    handle = CallHandle(
        call=call,
        queue=queue,
        ports=Ports(
            telnyx_http=telnyx,
            telnyx_media=HeldMarks(queue.put_nowait),
            gpt_live=SilentGptLive(),
        ),
        run_dir=RunDir(timed.call_agent_artifacts_dir, "run-timeout"),
        settings=timed,
        dial_spec=DialSpec(
            connection_id="connection-1",
            to=request.to,
            from_did=call.from_did,
            stream_url="wss://specs-publicity-singing-money.trycloudflare.com/media-stream",
            webhook_url="https://specs-publicity-singing-money.trycloudflare.com/webhooks/telnyx",
            codec=Codec.L16,
            client_state=ClientState(
                run_id=call.run_id, user_id=request.user_id
            ),
        ),
    )

    result = await handle.run()
    rows = [
        json.loads(line)
        for line in (handle.run_dir.path / "events.jsonl")
        .read_text()
        .splitlines()
    ]
    timeout_row = next(row for row in rows if row.get("kind") == "timeout")

    assert result.status == "failed"
    assert "webhook/media delivery miss" in result.reason
    assert "specs-publicity-singing-money.trycloudflare.com" in result.reason
    assert timeout_row["webhook_host"] == (
        "specs-publicity-singing-money.trycloudflare.com"
    )
    assert timeout_row["stream_host"] == (
        "specs-publicity-singing-money.trycloudflare.com"
    )
    assert telnyx.hung_up == "v3:timeout"


def test_normalized_public_host_strips_default_https_and_wss_ports() -> None:
    assert (
        normalized_public_host(
            "https://tunnel.example.test/webhooks/telnyx"
        )
        == "tunnel.example.test"
    )
    assert (
        normalized_public_host("wss://tunnel.example.test:443/media-stream")
        == "tunnel.example.test"
    )
    assert (
        normalized_public_host("HTTPS://Tunnel.Example.Test")
        == "tunnel.example.test"
    )
    assert (
        normalized_public_host("wss://tunnel.example.test:8443/media-stream")
        == "tunnel.example.test:8443"
    )


def test_assert_live_dial_origins_requires_webhook_url() -> None:
    with pytest.raises(ValueError, match="PUBLIC_WEBHOOK_URL"):
        assert_live_dial_origins(
            Settings(
                _env_file=None,
                public_base_url="https://tunnel.example.test",
            )
        )
    with pytest.raises(ValueError, match="PUBLIC_WEBHOOK_URL"):
        assert_live_dial_origins(
            Settings(
                _env_file=None,
                public_base_url="https://tunnel.example.test",
                public_webhook_url="  ",
            )
        )


def test_assert_live_dial_origins_rejects_fly_media_and_stale_tunnel() -> None:
    with pytest.raises(ValueError, match="webhook host"):
        assert_live_dial_origins(
            Settings(
                _env_file=None,
                public_base_url="https://hidden-beacon-080.fly.dev",
                public_webhook_url="https://stale-tunnel.trycloudflare.com",
            )
        )


def test_assert_live_dial_origins_accepts_https_and_wss_same_host() -> None:
    assert_live_dial_origins(
        Settings(
            _env_file=None,
            public_base_url="https://tunnel.example.test",
            public_webhook_url="https://tunnel.example.test:443",
        )
    )


def test_settings_process_env_overrides_dotenv_webhook_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("PUBLIC_WEBHOOK_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PUBLIC_BASE_URL=https://tunnel.trycloudflare.com\n"
        "PUBLIC_WEBHOOK_URL=https://tunnel.trycloudflare.com\n"
    )
    monkeypatch.setenv(
        "PUBLIC_WEBHOOK_URL", "https://hidden-beacon-080.fly.dev"
    )
    settings = Settings(_env_file=env_file)
    assert settings.public_base_url == "https://tunnel.trycloudflare.com"
    assert settings.public_webhook_url == "https://hidden-beacon-080.fly.dev"
    with pytest.raises(ValueError, match="webhook host"):
        assert_live_dial_origins(settings)


def test_openai_responses_effort_defaults_light_to_low(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_RESPONSES_EFFORT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.openai_responses_effort == "low"


def test_openai_responses_effort_env_light_aliases_to_low(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_RESPONSES_EFFORT", "light")
    settings = Settings(_env_file=None)
    assert settings.openai_responses_effort == "low"


def test_openai_responses_effort_env_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_RESPONSES_EFFORT", "high")
    settings = Settings(_env_file=None)
    assert settings.openai_responses_effort == "high"


def test_openai_responses_effort_rejects_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_RESPONSES_EFFORT", "banana")
    with pytest.raises(ValidationError, match="OPENAI_RESPONSES_EFFORT"):
        Settings(_env_file=None)
    monkeypatch.delenv("TELNYX_FROM_NUMBER", raising=False)
    monkeypatch.setenv("TELNYX_PHONE_NUMBER", "+12135690899")
    settings = Settings(_env_file=None)
    assert shared_telnyx_from(settings) == E164("+12135690899")
    assert resolve_telnyx_from(settings) == E164("+12135690899")


def test_stub_without_telnyx_from_uses_stub_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELNYX_FROM_NUMBER", raising=False)
    monkeypatch.delenv("TELNYX_PHONE_NUMBER", raising=False)
    settings = Settings(_env_file=None)
    assert shared_telnyx_from(settings) is None
    assert resolve_telnyx_from(settings, live=False) == E164("+15555550100")
    with pytest.raises(TelnyxFromConfigError, match="TELNYX_FROM_NUMBER"):
        resolve_telnyx_from(settings, live=True)


async def test_run_call_live_fails_when_webhook_host_diverges(
    settings: Settings,
) -> None:
    split = Settings(
        _env_file=None,
        call_agent_db_path=settings.call_agent_db_path,
        call_agent_artifacts_dir=settings.call_agent_artifacts_dir,
        quick_ask_live=True,
        telnyx_api_key="test-key",
        telnyx_connection_id="connection-1",
        openai_api_key="test-openai",
        openai_responses_model="gpt-5.6-terra",
        telnyx_from_number="+12135690899",
        public_base_url="https://hidden-beacon-080.fly.dev",
        public_webhook_url="https://stale-tunnel.trycloudflare.com",
    )
    request = QuickAskRequest(
        user_id=UserId("user-1"),
        to=E164("+14155552671"),
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.LIVE,
    )
    with pytest.raises(ValueError, match="webhook host"):
        await run_quick_ask(
            request, split, from_did=E164("+15555550100")
        )


async def test_run_call_never_orders_a_number(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    retained = Settings(
        _env_file=None,
        call_agent_db_path=settings.call_agent_db_path,
        call_agent_artifacts_dir=settings.call_agent_artifacts_dir,
        retain_call_artifacts=True,
        hangup_trailing_silence_s=0,
    )

    def boom(self: object, connection_id: str) -> object:
        del self, connection_id
        raise AssertionError("search_and_order_number must not run on dial")

    monkeypatch.setattr(StubTelnyxHttp, "search_and_order_number", boom)
    from_did = E164("+15555550177")
    request = QuickAskRequest(
        user_id=UserId("user-1"),
        to=E164("+14155552671"),
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
    )
    result = await run_quick_ask(request, retained, from_did=from_did)
    dial = json.loads(
        (
            retained.call_agent_artifacts_dir
            / result.call_control_id
            / "dial.json"
        ).read_text()
    )
    from call_agent.ledger import open_ledger

    assert dial["from"] == str(from_did)
    assert open_ledger(retained).list() == []


def test_dial_path_source_has_no_provision() -> None:
    root = Path(__file__).resolve().parents[1] / "call_agent"
    for name in ("runtime.py", "kickoff.py", "server.py"):
        text = (root / name).read_text()
        assert "provision(" not in text, name
        assert "search_and_order_number" not in text, name
        assert "number_orders" not in text, name
        assert "open_ledger" not in text, name


class DroppingMedia:
    def __init__(self) -> None:
        self.played: list[str] = []
        self.marks: list[str] = []

    async def play(self, payload_b64: str) -> None:
        self.played.append(payload_b64)
        raise WebSocketDisconnect(code=1006)

    async def mark(self, name: str) -> None:
        self.marks.append(name)
        raise WebSocketDisconnect(code=1006)

    async def close(self) -> None:
        return None


async def test_play_into_dead_media_is_non_fatal_and_leaves_the_call_open(
    settings: Settings,
) -> None:
    handle, _ = speaking_handle(settings)
    handle.ports.telnyx_media = DroppingMedia()

    await handle._process_event(LiveOutputAudioEvent(delta_b64=AUDIO))
    rows = [
        json.loads(line)
        for line in (handle.run_dir.path / "events.jsonl")
        .read_text()
        .splitlines()
    ]

    assert handle.call.media.kind == "media_stopped"
    assert handle.call.finished is False
    assert any(row.get("kind") == "media_play_failed" for row in rows)
    assert any(row.get("error_type") == "WebSocketDisconnect" for row in rows)


class BoomOnStart(SilentGptLive):
    async def start(self, spec: object) -> None:
        raise WebSocketDisconnect(code=1006)


async def test_run_loop_empty_websocket_disconnect_fails_with_type_name(
    settings: Settings,
) -> None:
    retained = Settings(
        _env_file=None,
        call_agent_db_path=settings.call_agent_db_path,
        call_agent_artifacts_dir=settings.call_agent_artifacts_dir,
        retain_call_artifacts=True,
        hangup_trailing_silence_s=0,
        event_timeout_s=2,
    )
    request = QuickAskRequest(
        user_id=UserId("user-1"),
        to=E164("+14155552671"),
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
    )
    call = Call(
        run_id=RunId("run-boom"),
        request=request,
        from_did=E164("+15555550100"),
    )
    queue: asyncio.Queue[CallEvent] = asyncio.Queue()
    telnyx = StubTelnyxHttp(submit=queue.put_nowait)
    handle = CallHandle(
        call=call,
        queue=queue,
        ports=Ports(
            telnyx_http=telnyx,
            telnyx_media=HeldMarks(queue.put_nowait),
            gpt_live=BoomOnStart(),
        ),
        run_dir=RunDir(retained.call_agent_artifacts_dir, "run-boom"),
        settings=retained,
        dial_spec=DialSpec(
            connection_id="connection-1",
            to=request.to,
            from_did=call.from_did,
            stream_url="wss://calls.example.test/media-stream",
            webhook_url="https://calls.example.test/webhooks/telnyx",
            codec=Codec.L16,
            client_state=ClientState(
                run_id=call.run_id, user_id=request.user_id
            ),
        ),
    )

    result = await handle.run()
    rows = [
        json.loads(line)
        for line in (handle.run_dir.path / "events.jsonl")
        .read_text()
        .splitlines()
    ]

    assert result.status == "failed"
    assert result.reason == "WebSocketDisconnect"
    assert result.reason != ""
    assert any(row.get("kind") == "runtime_failure" for row in rows)


def _book_request() -> BookRequest:
    return BookRequest(
        user_id=UserId("user-1"),
        to=E164("+14155552671"),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
        party_size=2,
        time_text="Friday 7pm",
        constraints="outdoor if possible",
        retain_call_artifacts=True,
    )


def _fast_settings(settings: Settings) -> Settings:
    return Settings(
        _env_file=None,
        call_agent_db_path=settings.call_agent_db_path,
        call_agent_artifacts_dir=settings.call_agent_artifacts_dir,
        retain_call_artifacts=True,
        hangup_trailing_silence_s=0,
        farewell_idle_s=0,
        farewell_max_s=0.2,
    )


async def test_done_state_hangup_skips_end_call_path_is_tool(
    settings: Settings,
) -> None:
    result = await run_call(
        _book_request(),
        _fast_settings(settings),
        from_did=E164("+15555550100"),
        scenario=StubScenario(skips_end_call=True),
    )
    run_dir = artifact_dir(
        settings.call_agent_artifacts_dir, result.call_control_id
    )
    report = json.loads((run_dir / "call_report.json").read_text())
    assert result.status == "booked"
    assert report["hangup"]["path"] == "tool"
    assert report["verdict_source"] == "tool"


async def test_far_party_hangs_up_after_record_keeps_booked(
    settings: Settings,
) -> None:
    result = await run_call(
        _book_request(),
        _fast_settings(settings),
        from_did=E164("+15555550100"),
        scenario=StubScenario(far_party_hangs_up_after_record=True),
    )
    run_dir = artifact_dir(
        settings.call_agent_artifacts_dir, result.call_control_id
    )
    report = json.loads((run_dir / "call_report.json").read_text())
    assert result.status == "booked"
    assert result_text(result, E164("+14155552671")).startswith("Booked:")
    assert report["hangup"]["path"] == "far_party"
    assert report["verdict_source"] == "tool"


async def test_far_party_hangs_up_no_tools_books_from_transcript(
    settings: Settings,
) -> None:
    result = await run_call(
        _book_request(),
        _fast_settings(settings),
        from_did=E164("+15555550100"),
        scenario=StubScenario(far_party_hangs_up_no_tools=True),
    )
    run_dir = artifact_dir(
        settings.call_agent_artifacts_dir, result.call_control_id
    )
    report = json.loads((run_dir / "call_report.json").read_text())
    assert result.status == "booked"
    assert result.confirmation_text
    assert "booked" in result.confirmation_text.casefold()
    assert report["hangup"]["path"] == "far_party"
    assert report["verdict_source"] == "transcript"
    assert report["counts"]["tool_calls"] == 0
    assert result_text(result, E164("+14155552671")).startswith("Booked:")


async def test_artifact_key_slug_colon_ccid_keeps_raw_in_result(
    settings: Settings,
) -> None:
    ccid = "v3:stub_colon_0001"
    result = await run_call(
        _book_request(),
        _fast_settings(settings),
        from_did=E164("+15555550100"),
        scenario=StubScenario(placed_call_control_id=ccid),
    )
    prefix = artifact_slug(ccid)
    run_dir = settings.call_agent_artifacts_dir / prefix
    assert result.call_control_id == ccid
    assert prefix == "v3_stub_colon_0001"
    assert run_dir.is_dir()
    assert not (settings.call_agent_artifacts_dir / ccid).exists()
    assert result.transcript_ref == f"{prefix}/transcript.json"
    report = json.loads((run_dir / "call_report.json").read_text())
    assert report["call_control_id"] == ccid
    assert report["artifact_prefix"] == prefix
    result_json = json.loads((run_dir / "result.json").read_text())
    assert result_json["call_control_id"] == ccid


async def test_publish_failure_keeps_result(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    class RaisingStore:
        def put(
            self,
            key: str,
            body: bytes,
            content_type: str = "",
        ) -> None:
            del key, body, content_type
            raise RuntimeError("R2 put failed for key: HTTP 403")

        def get(self, key: str) -> bytes:
            raise FileNotFoundError(key)

    monkeypatch.setattr(
        "call_agent.runtime.open_artifact_store", lambda _settings: RaisingStore()
    )
    result = await run_call(
        _book_request(),
        _fast_settings(settings),
        from_did=E164("+15555550100"),
    )
    run_dir = artifact_dir(
        settings.call_agent_artifacts_dir, result.call_control_id
    )
    assert result.status == "booked"
    assert result_text(result, E164("+14155552671")).startswith("Booked:")
    payload = json.loads((run_dir / "publish_error.json").read_text())
    assert "R2 put failed" in payload["error"]
    assert "HTTP 403" in payload["error"]

