import asyncio
import base64
import json
from collections.abc import Callable
from types import SimpleNamespace

from call_agent.config import Settings
from call_agent.domain import (
    CLOSING_LINE,
    Call,
    CallEvent,
    ClientState,
    Codec,
    ConversationOpen,
    DialPlaced,
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
from call_agent.gpt_live import StubGptLive
from call_agent.runtime import CallHandle, Ports, RunDir
from call_agent.telnyx import StubTelnyxHttp, parse_media_frame

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
        live=LiveActive(session_id=LiveSessionId("live-1")),
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
        "kind": "hangup_draining"
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
    kinds = [
        json.loads(line).get("kind")
        for line in (handle.run_dir.path / "events.jsonl")
        .read_text()
        .splitlines()
    ]

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
