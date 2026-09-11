import pytest
from pydantic import ValidationError

from call_agent.domain import (
    Answered,
    Call,
    CloseLiveEffect,
    Codec,
    Declined,
    DialPlaced,
    DialPlacedEvent,
    E164,
    Failed,
    FinishEffect,
    FirstName,
    HangupEffect,
    HangupRequested,
    LiveClosed,
    LiveDormant,
    LiveStarting,
    MediaStartEvent,
    Mode,
    OpenLiveEffect,
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


def test_media_start_before_answer_opens_live_once_and_dedupes_webhook() -> None:
    call = make_call()
    settings = ReduceSettings(live_model="gpt-live-1", voice="marin")

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


def test_streaming_failed_hangs_up_closes_live_and_fails_the_call() -> None:
    settings = ReduceSettings(live_model="gpt-live-1", voice="marin")
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
    assert dormant_call.hangup == HangupRequested()
    assert dormant_call.live == LiveDormant()
    assert starting_call.live == LiveClosed()


def test_dial_placed_only_changes_pending_dial() -> None:
    call = make_call().model_copy(update={"dial": make_call().dial})
    event = DialPlacedEvent(
        call_control_id="call-2",
        call_leg_id="leg-2",
        call_session_id="session-2",
    )

    reduced, effects = reduce(
        call,
        event,
        ReduceSettings(live_model="gpt-live-1", voice="marin"),
    )

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
