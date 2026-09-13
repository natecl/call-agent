import base64
import json

import pytest
from pydantic import ValidationError

from call_agent.domain import (
    BOOK_RESULT_ADAPTER,
    CLOSING_LINE,
    Answered,
    Booked,
    BookRequest,
    Call,
    CloseLiveEffect,
    Codec,
    ContinueResponseEffect,
    ConversationOpen,
    ConversationTerminal,
    Declined,
    DialPlaced,
    DialPlacedEvent,
    E164,
    Failed,
    FarewellIdleCheckEvent,
    FinishEffect,
    FirstName,
    HangupConfirmTimeoutEvent,
    HangupDraining,
    HangupEffect,
    HangupFarewell,
    HangupMarked,
    HangupRequested,
    HangupUnconfirmed,
    LiveActive,
    LiveClosed,
    LiveClosedEvent,
    LiveClosing,
    LiveDelegationEvent,
    LiveDormant,
    LiveInputTranscriptEvent,
    LiveNoteEvent,
    LiveOutputAudioEvent,
    LiveOutputTranscriptEvent,
    LiveSessionId,
    LiveStallCheckEvent,
    LiveStallGiveUpEvent,
    LiveStarting,
    LiveToolCallEvent,
    MarkEffect,
    MediaPlayFailedEvent,
    MediaStartEvent,
    MediaStopped,
    MediaStreaming,
    Mode,
    OpenLiveEffect,
    PlayAudioEffect,
    Question,
    QuestionType,
    QuickAskRequest,
    QUICK_ASK_RESULT_ADAPTER,
    ReduceSettings,
    ReplyDelegationEffect,
    RunId,
    ScheduleEffect,
    Speaker,
    SteerLiveEffect,
    StreamId,
    TelnyxAnswered,
    TelnyxRinging,
    TimeoutEvent,
    Transcript,
    Turn,
    UserId,
    WebhookEvent,
    WriteTaskEffect,
    assess_book,
    assess_transcript,
    build_backend_instructions,
    build_book_instructions,
    build_instructions,
    exception_reason,
    next_step_line,
    reduce,
    timeout_failure_reason,
)
from call_agent.telnyx import parse_media_frame

AUDIO = base64.b64encode(bytes(640)).decode()


def hangup_requested_effects(
    call_control_id: str = "call-1", delay_s: float = 5.0
) -> tuple:
    return (
        HangupEffect(call_control_id=call_control_id),
        ScheduleEffect(
            delay_s=delay_s,
            event=HangupConfirmTimeoutEvent(),
        ),
    )


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
            "live": LiveActive(
                session_id=LiveSessionId("live-1"),
                stall_check_pending=True,
                activity_seq=1,
            ),
            "conversation": ConversationOpen(
                transcript=Transcript().append_delta(
                    Speaker.BUSINESS, "We're open 9 to 5."
                )
            ),
        }
    )


def speaking_book_call(business_text: str = "Yes, we can do that.") -> Call:
    request = BookRequest(
        user_id=UserId("user-1"),
        to=E164("+14155552671"),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
        party_size=2,
        time_text="Friday 7:30pm",
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
                Speaker.BUSINESS, business_text
            )
        ),
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
    assert isinstance(call.conversation, ConversationOpen)
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
    assert trail_effects == hangup_requested_effects()
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
    assert timeout_effects == hangup_requested_effects()
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

    assert timeout_effects == hangup_requested_effects()
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

    assert effects == hangup_requested_effects() + (
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
    assert [tool.name for tool in second_effects[0].spec.tools] == [
        "record_task_result",
        "end_call",
    ]
    assert second_effects[0].spec.responses_model == "gpt-5.6-terra"
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
    assert dormant_effects == hangup_requested_effects() + (
        FinishEffect(result=result),
    )
    assert starting_effects == (
        HangupEffect(call_control_id="call-1"),
        CloseLiveEffect(),
        ScheduleEffect(
            delay_s=5.0,
            event=HangupConfirmTimeoutEvent(),
        ),
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


def test_split_closing_turns_still_verdict_and_enter_hangup_drain(
    reduce_settings: ReduceSettings,
) -> None:
    question = Question.from_cli(QuestionType.HOURS, None)
    split_closing = Transcript(
        turns=(
            Turn(speaker=Speaker.BUSINESS, text="We're open 9 to 5."),
            Turn(speaker=Speaker.AGENT, text="Thanks,"),
            Turn(
                speaker=Speaker.AGENT,
                text=" that's all I needed. Goodbye.",
            ),
        )
    )

    verdict = assess_transcript(split_closing, question)
    call, before_close = reduce(
        speaking_call(),
        LiveOutputTranscriptEvent(delta="Thanks,"),
        reduce_settings,
    )
    call, _ = reduce(
        call,
        LiveInputTranscriptEvent(delta="We're open 9 to 5."),
        reduce_settings,
    )
    call, effects = reduce(
        call,
        LiveOutputTranscriptEvent(delta=" that's all I needed. Goodbye."),
        reduce_settings,
    )

    assert verdict == Answered(answer_text="We're open 9 to 5.")
    assert before_close == ()
    assert isinstance(call.conversation, ConversationOpen)
    assert call.hangup == HangupDraining()
    assert call.live == LiveClosing()
    assert [effect.model_dump(mode="json") for effect in effects] == [
        {
            "kind": "schedule",
            "delay_s": 10.0,
            "event": {"kind": "hangup_drain_timeout"},
        },
        {"kind": "close_live"},
    ]


def test_book_closing_line_enters_hangup_drain_without_a_booked_verdict(
    reduce_settings: ReduceSettings,
) -> None:
    split_closing = Transcript(
        turns=(
            Turn(speaker=Speaker.BUSINESS, text="Yes, we can do that."),
            Turn(speaker=Speaker.AGENT, text="Thanks,"),
            Turn(
                speaker=Speaker.AGENT,
                text=" that's all I needed. Goodbye.",
            ),
        )
    )

    verdict = assess_book(split_closing)
    call, before_close = reduce(
        speaking_book_call(),
        LiveOutputTranscriptEvent(delta="Thanks,"),
        reduce_settings,
    )
    call, effects = reduce(
        call,
        LiveOutputTranscriptEvent(delta=" that's all I needed. Goodbye."),
        reduce_settings,
    )

    assert verdict is None
    assert before_close == ()
    assert isinstance(call.conversation, ConversationOpen)
    assert call.hangup == HangupDraining()
    assert call.live == LiveClosing()
    assert [effect.model_dump(mode="json") for effect in effects] == [
        {
            "kind": "schedule",
            "delay_s": 10.0,
            "event": {"kind": "hangup_drain_timeout"},
        },
        {"kind": "close_live"},
    ]


def test_book_closing_line_hangs_up_through_the_trail_not_far_party(
    reduce_settings: ReduceSettings,
) -> None:
    call, _ = reduce(
        speaking_book_call(),
        LiveOutputTranscriptEvent(delta=CLOSING_LINE),
        reduce_settings,
    )
    call, closed_effects = reduce(call, LiveClosedEvent(), reduce_settings)
    call, echo_effects = reduce(call, mark_echo("hangup"), reduce_settings)
    call, trail_effects = reduce(
        call, echo_effects[0].event, reduce_settings
    )

    assert assess_book(call.conversation.transcript) is None
    assert isinstance(call.conversation, ConversationOpen)
    assert closed_effects == (MarkEffect(name="hangup"),)
    assert trail_effects == hangup_requested_effects()
    assert call.hangup == HangupRequested(via="trail")
    assert call.live == LiveClosed()


def test_book_booked_verdict_still_enters_hangup_drain(
    reduce_settings: ReduceSettings,
) -> None:
    call, effects = reduce(
        speaking_book_call("You're booked. We'll see you Friday."),
        LiveOutputTranscriptEvent(delta=CLOSING_LINE),
        reduce_settings,
    )

    assert isinstance(call.conversation, ConversationOpen)
    assert call.hangup == HangupDraining()
    assert call.live == LiveClosing()
    assert [effect.kind for effect in effects] == ["schedule", "close_live"]


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

    assert instructions == (
        "You are a calm, friendly phone assistant calling a business "
        "on behalf of Nathan. Speak warmly and naturally at an unhurried "
        "pace, be clear and direct rather than overly cheerful, and if they "
        "are frustrated acknowledge it briefly then take the next helpful step.\n"
        "Hi, I'm calling on behalf of Nathan. I have one quick question.\n"
        "Do you carry whole milk?\n"
        "When the task is done, say a short goodbye.\n"
        "Never book, reserve, or hold anything. "
        "Keep every turn under two sentences.\n"
        "If the other party pauses, keep listening briefly, then continue: "
        "confirm what you have or ask your next question.\n"
        "Delegation policy:\n"
        "Backend tools:\n"
        "- record_task_result, end_call.\n"
        "Delegate to the backend when:\n"
        "- the other party has given a final yes, no, time, or answer; "
        "you must decide whether to accept what they offered; the call should end.\n"
        "Do not delegate to the backend when:\n"
        "- The other party greets you, repeats a result, or needs a brief clarification.\n"
        "- You can continue from these instructions and what they have already said.\n"
        "Do not guess or promise a result the other party has not said."
    )
    assert instructions.index("on behalf of Nathan") < instructions.index(
        "Do you carry whole milk?"
    )
    assert instructions.splitlines()[1] == (
        "Hi, I'm calling on behalf of Nathan. I have one quick question."
    )
    assert instructions.splitlines()[2] == "Do you carry whole milk?"
    assert "Backchannel policy:" not in instructions
    assert "Interruption policy:" not in instructions


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


def test_assess_book_waits_for_closing_then_books_or_declines() -> None:
    talk = Transcript().append_delta(
        Speaker.BUSINESS, "You're booked. We'll see you Friday."
    )

    before_close = assess_book(talk)
    after_close = assess_book(
        talk.append_delta(
            Speaker.AGENT, "Thanks, that's all I needed. Goodbye."
        )
    )
    declined = assess_book(
        Transcript()
        .append_delta(
            Speaker.BUSINESS, "We're fully booked. We can't take that."
        )
        .append_delta(
            Speaker.AGENT, "Thanks, that's all I needed. Goodbye."
        )
    )
    theater = assess_book(
        Transcript()
        .append_delta(Speaker.BUSINESS, "Yes, that works.")
        .append_delta(
            Speaker.AGENT, "Thanks, that's all I needed. Goodbye."
        )
    )

    assert before_close is None
    assert after_close == Booked(
        confirmation_text="You're booked. We'll see you Friday."
    )
    assert declined == Declined()
    assert theater is None


def test_build_book_instructions_puts_disclosure_before_facts() -> None:
    instructions = build_book_instructions(
        FirstName("Nathan"), 2, "Friday 7pm", "outdoor if possible"
    )

    assert instructions == (
        "You are a calm, friendly phone assistant calling a restaurant "
        "on behalf of Nathan. Speak warmly and naturally at an unhurried "
        "pace, be clear and direct rather than overly cheerful, and if they "
        "are frustrated acknowledge it briefly then take the next helpful step.\n"
        "Hi, I'm calling on behalf of Nathan. I'd like to book a table.\n"
        "Party size: 2. Requested time: Friday 7pm. "
        "Constraints: outdoor if possible.\n"
        "Work with the restaurant on party, time, and constraints. "
        "Reason about alternatives they offer. "
        "Do not read a fixed list of questions.\n"
        "Never join a hold queue or waitlist. "
        "If they cannot seat this party at this time, accept the decline.\n"
        "When the task is done, say a short goodbye.\n"
        "Keep every turn under two sentences.\n"
        "If the other party pauses, keep listening briefly, then continue: "
        "confirm what you have or ask your next question.\n"
        "Delegation policy:\n"
        "Backend tools:\n"
        "- record_task_result, end_call.\n"
        "Delegate to the backend when:\n"
        "- the other party has given a final yes, no, time, or answer; "
        "you must decide whether to accept what they offered; the call should end.\n"
        "Do not delegate to the backend when:\n"
        "- The other party greets you, repeats a result, or needs a brief clarification.\n"
        "- You can continue from these instructions and what they have already said.\n"
        "Do not guess or promise a result the other party has not said."
    )
    assert instructions.index("on behalf of Nathan") < instructions.index(
        "I'd like to book a table"
    )
    assert instructions.splitlines()[1] == (
        "Hi, I'm calling on behalf of Nathan. I'd like to book a table."
    )
    assert instructions.splitlines()[2] == (
        "Party size: 2. Requested time: Friday 7pm. "
        "Constraints: outdoor if possible."
    )
    assert "Party size: 2." in instructions
    assert "Requested time: Friday 7pm." in instructions
    assert "Constraints: outdoor if possible." in instructions
    assert "hold queue" in instructions
    assert "Never book, reserve, or hold anything." not in instructions
    assert "Backchannel policy:" not in instructions
    assert "Interruption policy:" not in instructions


def test_book_result_is_discriminated_and_confirmation_is_nonempty() -> None:
    booked = {
        "status": "booked",
        "confirmation_text": "You're booked for Friday.",
        "call_control_id": "call-1",
        "transcript_ref": "call-1/transcript.json",
        "audio_ref": "call-1/audio/inbound.wav",
        "answer_text": None,
    }

    dumped = BOOK_RESULT_ADAPTER.validate_python(booked).model_dump(
        mode="json"
    )

    assert dumped == booked
    with pytest.raises(ValidationError):
        Booked(confirmation_text="")
    with pytest.raises(ValidationError):
        BookRequest(
            user_id=UserId("user-1"),
            to=E164("+14155552671"),
            caller=FirstName("Nathan"),
            mode=Mode.STUB,
            party_size=0,
            time_text="Friday 7pm",
        )


def test_timeout_after_dial_without_webhooks_or_media_flags_delivery(
    reduce_settings: ReduceSettings,
) -> None:
    event = TimeoutEvent(
        webhook_host="specs-publicity-singing-money.trycloudflare.com",
        stream_host="specs-publicity-singing-money.trycloudflare.com",
    )
    call, effects = reduce(make_call(), event, reduce_settings)
    result = effects[-1].result
    reason = timeout_failure_reason(make_call(), event)

    assert "webhook/media delivery miss" in result.reason
    assert "specs-publicity-singing-money.trycloudflare.com" in result.reason
    assert "GPT-Live" in result.reason
    assert "speak_first" in result.reason
    assert result.status == "failed"
    assert call.finished is True
    assert reason == result.reason


def test_timeout_after_ringing_without_media_flags_media_delivery(
    reduce_settings: ReduceSettings,
) -> None:
    event = TimeoutEvent(
        webhook_host="tunnel.example.test",
        stream_host="tunnel.example.test",
    )
    ringing = make_call().model_copy(update={"telnyx": TelnyxRinging()})
    _, effects = reduce(ringing, event, reduce_settings)

    assert "media delivery miss" in effects[-1].result.reason
    assert "webhook/media delivery miss" not in effects[-1].result.reason


def test_timeout_during_active_call_stays_generic(
    reduce_settings: ReduceSettings,
) -> None:
    _, effects = reduce(
        speaking_call(),
        TimeoutEvent(
            webhook_host="tunnel.example.test",
            stream_host="tunnel.example.test",
        ),
        reduce_settings,
    )

    assert effects[-1].result.reason == "event timeout"


def test_live_delegation_replies_with_next_step(
    reduce_settings: ReduceSettings,
) -> None:
    call, effects = reduce(
        speaking_book_call("Friday 7:30pm works for us."),
        LiveDelegationEvent(delegation_id="item_abc"),
        reduce_settings,
    )

    assert [effect.kind for effect in effects] == ["reply_delegation"]
    assert effects[0] == ReplyDelegationEffect(
        delegation_id="item_abc",
        thinking=(
            "Continue from the conversation. "
            "Task: party 2 at Friday 7:30pm for Nathan; constraints none."
        ),
        commentary=(
            "Can you confirm the reservation for 2 at Friday 7:30pm "
            "under Nathan?"
        ),
    )
    assert call.live.activity_seq == 2


def test_stall_check_rearms_when_activity_advanced(
    reduce_settings: ReduceSettings,
) -> None:
    call = speaking_book_call()
    call, _ = reduce(
        call,
        LiveOutputTranscriptEvent(delta="We need a table."),
        reduce_settings,
    )
    call, effects = reduce(
        call,
        LiveStallCheckEvent(activity_seq=1),
        reduce_settings,
    )

    assert effects == (
        ScheduleEffect(
            delay_s=20.0,
            event=LiveStallCheckEvent(activity_seq=2),
        ),
    )
    assert call.live.stall_check_pending is True


def test_silence_failsafe_steers_then_give_up_hangs_up_via_stall(
    reduce_settings: ReduceSettings,
) -> None:
    settings = reduce_settings.model_copy(
        update={"live_silence_failsafe_s": 0.0}
    )
    call = speaking_book_call()
    call, first = reduce(
        call, LiveStallCheckEvent(activity_seq=1), settings
    )
    call, give_up = reduce(call, LiveStallGiveUpEvent(), settings)

    assert first[0] == SteerLiveEffect(
        instructions=(
            "The line has been quiet. Wrap up politely and say goodbye."
        ),
        event_id="silence-failsafe",
    )
    assert call.hangup == HangupRequested(via="stall")
    assert give_up[-1].result.status == "failed"
    assert "agent stalled" in give_up[-1].result.reason
    assert call.finished is True


def test_stall_watchdog_idle_during_drain_and_while_input_flows(
    reduce_settings: ReduceSettings,
) -> None:
    draining, _ = reduce(
        speaking_call(),
        LiveOutputTranscriptEvent(delta=CLOSING_LINE),
        reduce_settings,
    )
    draining, drain_stall = reduce(
        draining, LiveStallCheckEvent(activity_seq=1), reduce_settings
    )
    listening = speaking_book_call()
    listening, _ = reduce(
        listening,
        LiveInputTranscriptEvent(delta="Let me check the book."),
        reduce_settings,
    )
    listening, input_stall = reduce(
        listening,
        LiveStallCheckEvent(activity_seq=1),
        reduce_settings,
    )

    assert draining.hangup == HangupDraining()
    assert drain_stall == ()
    assert input_stall == (
        ScheduleEffect(
            delay_s=20.0,
            event=LiveStallCheckEvent(activity_seq=2),
        ),
    )
    assert listening.live.stall_check_pending is True


def test_hangup_confirm_timeout_finishes_with_verdict(
    reduce_settings: ReduceSettings,
) -> None:
    call, _ = reduce(
        speaking_call(),
        LiveOutputTranscriptEvent(delta=CLOSING_LINE),
        reduce_settings,
    )
    call, _ = reduce(call, LiveClosedEvent(), reduce_settings)
    call, echo_effects = reduce(call, mark_echo("hangup"), reduce_settings)
    call, trail_effects = reduce(
        call, echo_effects[0].event, reduce_settings
    )
    call, confirm_effects = reduce(
        call, trail_effects[1].event, reduce_settings
    )

    assert call.hangup == HangupUnconfirmed(via="trail")
    assert call.finished is True
    assert confirm_effects[-1].result.status == "answered"


def test_goodbye_needle_with_booked_verdict_enters_hangup_drain(
    reduce_settings: ReduceSettings,
) -> None:
    call, effects = reduce(
        speaking_book_call("You're booked. We'll see you Friday."),
        LiveOutputTranscriptEvent(delta="Alright, goodbye."),
        reduce_settings,
    )

    assert isinstance(call.conversation, ConversationOpen)
    assert call.hangup.kind == "hangup_idle"
    assert all(effect.kind != "close_live" for effect in effects)


def test_goodbye_needle_without_verdict_does_not_enter_hangup_drain(
    reduce_settings: ReduceSettings,
) -> None:
    call, effects = reduce(
        speaking_book_call("Yes, we can do that."),
        LiveOutputTranscriptEvent(delta="Alright, goodbye."),
        reduce_settings,
    )

    assert isinstance(call.conversation, ConversationOpen)
    assert call.hangup.kind == "hangup_idle"
    assert all(effect.kind != "close_live" for effect in effects)
    assert next_step_line(
        call.request, call.conversation.transcript
    ) == (
        "Can you confirm the reservation for 2 at Friday 7:30pm under Nathan?"
    )


def test_live_note_is_a_no_op(reduce_settings: ReduceSettings) -> None:
    call = speaking_call()
    reduced, effects = reduce(
        call,
        LiveNoteEvent(
            event_type="session.instructions.appended",
            client_event_id="speak-first-1",
        ),
        reduce_settings,
    )

    assert reduced == call
    assert effects == ()


def test_media_play_failed_stops_media_and_keeps_the_call_open(
    reduce_settings: ReduceSettings,
) -> None:
    call, effects = reduce(
        speaking_call(),
        MediaPlayFailedEvent(
            stream_id=StreamId("stream-1"),
            error_type="WebSocketDisconnect",
        ),
        reduce_settings,
    )

    assert call.media == MediaStopped()
    assert call.finished is False
    assert effects == ()


def test_exception_reason_uses_type_name_when_str_is_empty() -> None:
    class EmptyDisconnect(Exception):
        def __str__(self) -> str:
            return "  "

    assert exception_reason(EmptyDisconnect()) == "EmptyDisconnect"
    assert exception_reason(ValueError("no tables")) == "ValueError: no tables"


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> LiveToolCallEvent:
    return LiveToolCallEvent(
        delegation_id="item_resp",
        response_id="resp_1",
        call_id=call_id,
        name=name,
        arguments_json=json.dumps(arguments),
    )


def test_responses_delegation_does_not_reply(
    reduce_settings: ReduceSettings,
) -> None:
    call, effects = reduce(
        speaking_book_call("Friday 7:30pm works for us."),
        LiveDelegationEvent(
            delegation_id="item_resp",
            target="responses",
            response_id="resp_1",
        ),
        reduce_settings,
    )

    assert all(effect.kind != "reply_delegation" for effect in effects)
    assert call.live.response_id == "resp_1"


def test_record_task_result_then_end_call_owns_hangup(
    reduce_settings: ReduceSettings,
) -> None:
    call, record_effects = reduce(
        speaking_book_call("You're booked. We'll see you Friday."),
        _tool_call(
            "record_task_result",
            {
                "outcome": "confirmed",
                "summary": "You're booked Friday.",
                "confirmed_fields": {
                    "party_size": 2,
                    "time_text": "Friday 7:30pm",
                    "name_on_record": "Nathan",
                    "answer_text": None,
                },
            },
        ),
        reduce_settings,
    )
    call, end_effects = reduce(
        call,
        _tool_call("end_call", {"reason": "task_done"}, call_id="call_end"),
        reduce_settings,
    )

    assert isinstance(call.conversation, ConversationTerminal)
    assert call.conversation.verdict == Booked(
        confirmation_text="You're booked Friday."
    )
    assert call.task is not None
    assert call.task.outcome == "confirmed"
    assert [effect.kind for effect in record_effects[:3]] == [
        "tool_result",
        "continue_response",
        "write_task",
    ]
    assert isinstance(record_effects[2], WriteTaskEffect)
    assert call.hangup == HangupFarewell(via="tool")
    assert end_effects[0].kind == "tool_result"
    assert json.loads(end_effects[0].output)["ok"] is True
    assert isinstance(end_effects[1], ContinueResponseEffect)
    idle = next(
        effect
        for effect in end_effects
        if isinstance(effect, ScheduleEffect)
        and isinstance(effect.event, FarewellIdleCheckEvent)
    )
    call, drain_effects = reduce(call, idle.event, reduce_settings)
    assert call.hangup == HangupDraining(via="tool")
    assert CloseLiveEffect() in drain_effects


def test_invalid_record_task_result_returns_error_and_stays_open(
    reduce_settings: ReduceSettings,
) -> None:
    call, effects = reduce(
        speaking_book_call(),
        _tool_call(
            "record_task_result",
            {
                "outcome": "answered",
                "summary": "hours",
                "confirmed_fields": {
                    "party_size": None,
                    "time_text": None,
                    "name_on_record": None,
                    "answer_text": "Open 9 to 5.",
                },
            },
        ),
        reduce_settings,
    )

    assert call.task is None
    assert isinstance(call.conversation, ConversationOpen)
    assert call.hangup.kind == "hangup_idle"
    assert json.loads(effects[0].output)["ok"] is False
    assert effects[1].kind == "continue_response"


def test_end_call_task_done_without_record_does_not_hang_up(
    reduce_settings: ReduceSettings,
) -> None:
    call, effects = reduce(
        speaking_call(),
        _tool_call("end_call", {"reason": "task_done"}),
        reduce_settings,
    )

    assert call.hangup.kind == "hangup_idle"
    assert json.loads(effects[0].output) == {
        "error": "record_task_result first",
        "ok": False,
    }


def test_build_backend_instructions_lists_legal_outcomes() -> None:
    ask = QuickAskRequest(
        user_id=UserId("user-1"),
        to=E164("+14155552671"),
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
    )
    book = BookRequest(
        user_id=UserId("user-1"),
        to=E164("+14155552671"),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
        party_size=2,
        time_text="Friday 7pm",
    )

    ask_text = build_backend_instructions(ask)
    book_text = build_backend_instructions(book)

    assert "answered, declined, or no_result" in ask_text
    assert "confirmed, declined, or no_result" in book_text
    assert "record_task_result" in ask_text
    assert "Never invent a successful action" in book_text

