import base64
import json

import pytest
from pydantic import ValidationError

from call_agent.domain import (
    CLOSING_LINE,
    Answered,
    Call,
    CloseLiveEffect,
    Codec,
    ConversationOpen,
    ConversationTerminal,
    Declined,
    DialPlaced,
    DialPlacedEvent,
    E164,
    Failed,
    FinishEffect,
    FirstName,
    HangupDraining,
    HangupEffect,
    HangupMarked,
    HangupRequested,
    LiveActive,
    LiveClosed,
    LiveClosedEvent,
    LiveClosing,
    LiveDormant,
    LiveOutputAudioEvent,
    LiveOutputTranscriptEvent,
    LiveSessionId,
    LiveStarting,
    MarkEffect,
    MediaStartEvent,
    MediaStreaming,
    Mode,
    OpenLiveEffect,
    PlayAudioEffect,
    Question,
    QuestionType,
    QuickAskRequest,
    QUICK_ASK_RESULT_ADAPTER,
    ReduceSettings,
    RunId,
    Speaker,
    StreamId,
    TelnyxAnswered,
    Transcript,
    UserId,
    WebhookEvent,
    assess_transcript,
    build_instructions,
    reduce,
)
from call_agent.telnyx import parse_media_frame

AUDIO = base64.b64encode(bytes(640)).decode()


@pytest.fixture
def reduce_settings() -> ReduceSettings:
    return ReduceSettings(
        live_model="gpt-live-1",
        voice="marin",
        hangup_trailing_silence_s=0.4,
        hangup_drain_timeout_s=10.0,
    )


def make_call() -> Call:
    request = QuickAskRequest(
        user_id=UserId("user-1"),
        to=E164("+14155552671"),
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
    )
    return Call(
        run_id=RunId("run-1"),
        request=request,
        from_did=E164("+15555550100"),
        dial=DialPlaced(
            call_control_id="call-1",
            call_leg_id="leg-1",
            call_session_id="session-1",
        ),
    )


def speaking_call() -> Call:
    return make_call().model_copy(
        update={
            "telnyx": TelnyxAnswered(),
            "media": MediaStreaming(
                stream_id=StreamId("stream-1"),
                codec=Codec.L16,
                sample_rate=16000,
            ),
            "live": LiveActive(session_id=LiveSessionId("live-1")),
            "conversation": ConversationOpen(
                transcript=Transcript().append_delta(
                    Speaker.BUSINESS, "We're open 9 to 5."
                )
            ),
        }
    )


def mark_echo(name: str):
    return parse_media_frame(
        json.dumps(
            {
                "event": "mark",
                "stream_id": "stream-1",
                "sequence_number": "9",
                "mark": {"name": name},
            }
        )
    )


def test_verdict_closes_live_and_drains_without_a_mark_per_chunk(
    reduce_settings: ReduceSettings,
) -> None:
    call, audio_effects = reduce(
        speaking_call(),
        LiveOutputAudioEvent(delta_b64=AUDIO),
        reduce_settings,
    )
    call, verdict_effects = reduce(
        call,
        LiveOutputTranscriptEvent(delta=CLOSING_LINE),
        reduce_settings,
    )

    assert audio_effects == (PlayAudioEffect(delta_b64=AUDIO),)
    assert isinstance(call.conversation, ConversationTerminal)
    assert call.conversation.verdict == Answered(answer_text="We're open 9 to 5.")
    assert [effect.model_dump(mode="json") for effect in verdict_effects] == [
        {
            "kind": "schedule",
            "delay_s": 10.0,
            "event": {"kind": "hangup_drain_timeout"},
        },
        {"kind": "close_live"},
    ]
    assert call.hangup == HangupDraining()
    assert call.live == LiveClosing()


def test_goodbye_audio_storm_sends_one_final_mark_after_live_closes_and_hangs_up_once(
    reduce_settings: ReduceSettings,
) -> None:
    call, _ = reduce(
        speaking_call(),
        LiveOutputTranscriptEvent(delta=CLOSING_LINE),
        reduce_settings,
    )
    storm_effects: list = []
    for _ in range(98):
        call, effects = reduce(
            call, LiveOutputAudioEvent(delta_b64=AUDIO), reduce_settings
        )
        storm_effects.extend(effects)
    call, closed_effects = reduce(call, LiveClosedEvent(), reduce_settings)
    marked = call.hangup
    call, echo_effects = reduce(call, mark_echo("hangup"), reduce_settings)
    call, trail_effects = reduce(
        call, echo_effects[0].event, reduce_settings
    )
    call, repeat_effects = reduce(
        call, echo_effects[0].event, reduce_settings
    )
    call, late_echo_effects = reduce(call, mark_echo("hangup"), reduce_settings)

    assert storm_effects == [PlayAudioEffect(delta_b64=AUDIO)] * 98
    assert closed_effects == (MarkEffect(name="hangup"),)
    assert marked == HangupMarked()
    assert [effect.model_dump(mode="json") for effect in echo_effects] == [
        {
            "kind": "schedule",
            "delay_s": 0.4,
            "event": {"kind": "hangup_trail_elapsed"},
        }
    ]
    assert trail_effects == (HangupEffect(call_control_id="call-1"),)
    assert call.hangup == HangupRequested(via="trail")
    assert call.live == LiveClosed()
    assert repeat_effects == ()
    assert late_echo_effects == ()


def test_stale_mark_echo_does_not_arm_the_trail(
    reduce_settings: ReduceSettings,
) -> None:
    call, _ = reduce(
        speaking_call(),
        LiveOutputTranscriptEvent(delta=CLOSING_LINE),
        reduce_settings,
    )
    call, early_echo_effects = reduce(call, mark_echo("hangup"), reduce_settings)
    call, _ = reduce(call, LiveClosedEvent(), reduce_settings)
    call, stale_echo_effects = reduce(
        call, mark_echo("hangup-1"), reduce_settings
    )

    assert early_echo_effects == ()
    assert stale_echo_effects == ()
    assert call.hangup == HangupMarked()


def test_drain_timeout_hangs_up_without_a_mark_echo(
    reduce_settings: ReduceSettings,
) -> None:
    call, verdict_effects = reduce(
        speaking_call(),
        LiveOutputTranscriptEvent(delta=CLOSING_LINE),
        reduce_settings,
    )
    call, closed_effects = reduce(call, LiveClosedEvent(), reduce_settings)
    call, timeout_effects = reduce(
        call, verdict_effects[0].event, reduce_settings
    )
    call, late_echo_effects = reduce(call, mark_echo("hangup"), reduce_settings)

    assert closed_effects == (MarkEffect(name="hangup"),)
    assert timeout_effects == (HangupEffect(call_control_id="call-1"),)
    assert call.hangup == HangupRequested(via="drain_timeout")
    assert late_echo_effects == ()


def test_drain_timeout_before_live_closes_hangs_up_without_a_second_close(
    reduce_settings: ReduceSettings,
) -> None:
    call, verdict_effects = reduce(
        speaking_call(),
        LiveOutputTranscriptEvent(delta=CLOSING_LINE),
        reduce_settings,
    )
    call, timeout_effects = reduce(
        call, verdict_effects[0].event, reduce_settings
    )
    call, late_closed_effects = reduce(call, LiveClosedEvent(), reduce_settings)

    assert timeout_effects == (HangupEffect(call_control_id="call-1"),)
    assert call.hangup == HangupRequested(via="drain_timeout")
    assert late_closed_effects == ()
    assert call.live == LiveClosed()


def test_streaming_failed_during_the_drain_still_hangs_up(
    reduce_settings: ReduceSettings,
) -> None:
    call, _ = reduce(
        speaking_call(),
        LiveOutputTranscriptEvent(delta=CLOSING_LINE),
        reduce_settings,
    )
    call, effects = reduce(
        call,
        WebhookEvent(
            webhook_id="webhook-failed-2",
            event_type="streaming.failed",
            call_control_id="call-1",
            failure_reason="connection_failed",
        ),
        reduce_settings,
    )

    assert effects == (
        HangupEffect(call_control_id="call-1"),
        FinishEffect(
            result=Failed(
                call_control_id="call-1",
                reason="Telnyx media streaming failed: connection_failed",
            )
        ),
    )
    assert call.hangup == HangupRequested(via="failure")
    assert call.finished is True


def test_media_start_before_answer_opens_live_once_and_dedupes_webhook(
    reduce_settings: ReduceSettings,
) -> None:
    call = make_call()
    settings = reduce_settings

    call, first_effects = reduce(
        call,
        MediaStartEvent(
            stream_id=StreamId("stream-1"),
            call_control_id="call-1",
            codec=Codec.L16,
            sample_rate=16000,
        ),
        settings,
    )
    call, second_effects = reduce(
        call,
        WebhookEvent(
            webhook_id="webhook-1",
            event_type="call.answered",
            call_control_id="call-1",
        ),
        settings,
    )
    duplicate_call, duplicate_effects = reduce(
        call,
        WebhookEvent(
            webhook_id="webhook-1",
            event_type="call.answered",
            call_control_id="call-1",
        ),
        settings,
    )

    assert first_effects == ()
    assert [effect.kind for effect in second_effects] == ["open_live"]
    assert isinstance(second_effects[0], OpenLiveEffect)
    assert second_effects[0].spec.audio.model_dump() == {
        "type": "audio/pcm",
        "rate": 16000,
    }
    assert not isinstance(call.live, LiveDormant)
    assert duplicate_call == call
    assert duplicate_effects == ()


def test_streaming_failed_hangs_up_closes_live_and_fails_the_call(
    reduce_settings: ReduceSettings,
) -> None:
    settings = reduce_settings
    failed = WebhookEvent(
        webhook_id="webhook-failed-1",
        event_type="streaming.failed",
        call_control_id="call-1",
        failure_reason="connection_failed",
    )
    answered = make_call().model_copy(update={"telnyx": TelnyxAnswered()})
    starting = answered.model_copy(update={"live": LiveStarting()})

    dormant_call, dormant_effects = reduce(answered, failed, settings)
    starting_call, starting_effects = reduce(starting, failed, settings)

    result = Failed(
        call_control_id="call-1",
        reason="Telnyx media streaming failed: connection_failed",
    )
    assert dormant_effects == (
        HangupEffect(call_control_id="call-1"),
        FinishEffect(result=result),
    )
    assert starting_effects == (
        HangupEffect(call_control_id="call-1"),
        CloseLiveEffect(),
        FinishEffect(result=result),
    )
    assert dormant_call.finished is True
    assert dormant_call.hangup == HangupRequested(via="failure")
    assert dormant_call.live == LiveDormant()
    assert starting_call.live == LiveClosed()


def test_dial_placed_only_changes_pending_dial(
    reduce_settings: ReduceSettings,
) -> None:
    call = make_call().model_copy(update={"dial": make_call().dial})
    event = DialPlacedEvent(
        call_control_id="call-2",
        call_leg_id="leg-2",
        call_session_id="session-2",
    )

    reduced, effects = reduce(call, event, reduce_settings)

    assert reduced == call
    assert effects == ()


def test_assess_transcript_waits_for_closing_then_answers_or_declines() -> None:
    question = Question.from_cli(QuestionType.HOURS, None)
    answer_transcript = Transcript().append_delta(
        Speaker.BUSINESS, "We're open 9 to 5."
    )

    before_close = assess_transcript(answer_transcript, question)
    after_close = assess_transcript(
        answer_transcript.append_delta(
            Speaker.AGENT, "Thanks, that's all I needed. Goodbye."
        ),
        question,
    )
    declined = assess_transcript(
        Transcript()
        .append_delta(Speaker.BUSINESS, "I can't help with that.")
        .append_delta(
            Speaker.AGENT, "Thanks, that's all I needed. Goodbye."
        ),
        question,
    )

    assert before_close is None
    assert after_close == Answered(answer_text="We're open 9 to 5.")
    assert declined == Declined()


def test_build_instructions_puts_disclosure_before_ask() -> None:
    question = Question.from_cli(
        QuestionType.STOCK, "whole milk", "Do you carry whole milk?"
    )

    instructions = build_instructions(FirstName("Nathan"), question)

    assert instructions.index("on behalf of Nathan") < instructions.index(
        "Do you carry whole milk?"
    )
    assert instructions.splitlines()[1] == (
        "Hi, I'm calling on behalf of Nathan. I have one quick question."
    )


def test_quick_ask_result_is_discriminated_and_answer_is_nonempty() -> None:
    raw_results = [
        {
            "status": "answered",
            "answer_text": "Open 9 to 5.",
            "call_control_id": "call-1",
            "transcript_ref": "call-1/transcript.json",
            "audio_ref": "call-1/audio/inbound.wav",
        },
        {
            "status": "declined",
            "answer_text": None,
            "call_control_id": "call-1",
            "transcript_ref": None,
            "audio_ref": None,
        },
        {
            "status": "no_answer",
            "answer_text": None,
            "call_control_id": "call-1",
            "transcript_ref": None,
            "audio_ref": None,
        },
        {
            "status": "failed",
            "answer_text": None,
            "call_control_id": "call-1",
            "transcript_ref": None,
            "audio_ref": None,
            "reason": "event timeout",
        },
    ]

    dumped = [
        QUICK_ASK_RESULT_ADAPTER.validate_python(raw).model_dump(mode="json")
        for raw in raw_results
    ]

    assert dumped == raw_results
    with pytest.raises(ValidationError):
        Answered(answer_text="")
