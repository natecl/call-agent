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
            gpt_live=StubGptLive(),
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


async def test_verdict_does_not_hang_up_while_output_audio_is_in_flight(
    settings: Settings,
) -> None:
    handle, media = speaking_handle(settings)

    await handle._process_event(LiveOutputAudioEvent(delta_b64=AUDIO))
    await handle._process_event(LiveOutputTranscriptEvent(delta=CLOSING_LINE))

    assert not (handle.run_dir.path / "hangup.json").exists()
    assert media.played == [AUDIO]
    assert media.marks == ["hangup-1"]
    assert handle.call.hangup.model_dump(mode="json") == {
        "kind": "hangup_draining",
        "marks_sent": 1,
    }


async def test_hangup_waits_for_the_last_mark_echo_and_the_trailing_silence(
    settings: Settings,
) -> None:
    handle, media = speaking_handle(settings)
    hangup_json = handle.run_dir.path / "hangup.json"

    await handle._process_event(LiveOutputAudioEvent(delta_b64=AUDIO))
    await handle._process_event(LiveOutputTranscriptEvent(delta=CLOSING_LINE))
    media.echo("hangup-1")
    await handle._process_event(handle.queue.get_nowait())
    await handle._process_event(LiveOutputAudioEvent(delta_b64=AUDIO))
    stale_trail = await asyncio.wait_for(handle.queue.get(), 1)
    await handle._process_event(stale_trail)
    hung_up_after_stale_trail = hangup_json.exists()

    media.echo("hangup-2")
    await handle._process_event(handle.queue.get_nowait())
    trail = await asyncio.wait_for(handle.queue.get(), 1)
    hung_up_before_trail = hangup_json.exists()
    await handle._process_event(trail)
    hangup = json.loads(hangup_json.read_text())
    while not handle.call.finished:
        await handle._process_event(handle.queue.get_nowait())
    result = json.loads((handle.run_dir.path / "result.json").read_text())

    assert stale_trail.model_dump(mode="json") == {
        "kind": "hangup_trail_elapsed",
        "mark": "hangup-1",
    }
    assert hung_up_after_stale_trail is False
    assert media.played == [AUDIO, AUDIO]
    assert media.marks == ["hangup-1", "hangup-2"]
    assert trail.model_dump(mode="json") == {
        "kind": "hangup_trail_elapsed",
        "mark": "hangup-2",
    }
    assert hung_up_before_trail is False
    assert hangup == {"call_control_id": "v3:abc"}
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
    timeout = await asyncio.wait_for(handle.queue.get(), 1)
    hung_up_before_timeout = hangup_json.exists()
    await handle._process_event(timeout)
    kinds = [
        json.loads(line)["kind"]
        for line in (handle.run_dir.path / "events.jsonl")
        .read_text()
        .splitlines()
    ]

    assert hung_up_before_timeout is False
    assert timeout.model_dump(mode="json") == {"kind": "hangup_drain_timeout"}
    assert media.marks == ["hangup-1"]
    assert json.loads(hangup_json.read_text()) == {"call_control_id": "v3:abc"}
    assert kinds == ["live_output_transcript", "hangup_drain_timeout"]
