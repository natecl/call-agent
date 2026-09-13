from __future__ import annotations

import base64
import re
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal, NamedTuple, Self, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic_core import core_schema

from .audio import resample_pcm16
from .tools import (
    ASK_OUTCOMES,
    BOOK_OUTCOMES,
    SESSION_TOOLS,
    ConfirmedFields,
    FunctionToolSpec,
    RecordedTask,
    RecordTaskResultArgs,
    check_record_task_result,
    parse_end_call,
    parse_record_task_result,
    tool_error,
    tool_ok,
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _Token(str):
    _pattern: ClassVar[re.Pattern[str]]
    _label: ClassVar[str] = "token"

    def __new__(cls, value: str) -> Self:
        if not cls._pattern.fullmatch(value):
            raise ValueError(f"invalid {cls._label}: {value!r}")
        return str.__new__(cls, value)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: type[Any], handler: Any
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(
            cls, core_schema.str_schema()
        )


class E164(_Token):
    _pattern = re.compile(r"^\+[1-9]\d{7,14}$")
    _label = "E.164"


class UserId(_Token):
    _pattern = re.compile(r"^[a-z0-9_-]{1,64}$")
    _label = "user id"


class FirstName(_Token):
    _pattern = re.compile(r"^[A-Za-z][A-Za-z' -]{0,31}$")
    _label = "first name"


class CallControlId(_Token):
    _pattern = re.compile(r"^\S+$")
    _label = "call control id"


class StreamId(_Token):
    _pattern = re.compile(r"^\S+$")
    _label = "stream id"


class RunId(_Token):
    _pattern = re.compile(r"^\S+$")
    _label = "run id"


class LiveSessionId(_Token):
    _pattern = re.compile(r"^\S+$")
    _label = "live session id"


class QuestionType(StrEnum):
    HOURS = "hours"
    STOCK = "stock"
    AISLE = "aisle"


class Mode(StrEnum):
    STUB = "stub"
    LIVE = "live"


class Speaker(StrEnum):
    AGENT = "agent"
    BUSINESS = "business"


class Codec(StrEnum):
    L16 = "L16"
    PCMU = "PCMU"


SampleRate: TypeAlias = Literal[8000, 16000]


class QuestionSpec(NamedTuple):
    default_text_template: str
    answer_needles: tuple[str, ...]
    decline_needles: tuple[str, ...]


DECLINE_NEEDLES = (
    "can't help",
    "cannot help",
    "not able",
    "don't give",
    "no information",
    "call back",
    "not allowed",
)

QUESTION_TABLE: dict[QuestionType, QuestionSpec] = {
    QuestionType.HOURS: QuestionSpec(
        "What are your hours today?",
        ("am", "pm", "open", "close", "hour"),
        DECLINE_NEEDLES,
    ),
    QuestionType.STOCK: QuestionSpec(
        "Do you have {item} in stock?",
        (
            "yes",
            "we have",
            "in stock",
            "out of stock",
            "we do",
            "we don't",
            "sold out",
        ),
        DECLINE_NEEDLES,
    ),
    QuestionType.AISLE: QuestionSpec(
        "Which aisle is {item} in?",
        ("aisle", "section", "row"),
        DECLINE_NEEDLES,
    ),
}


class Question(FrozenModel):
    kind: QuestionType
    text: str = Field(min_length=1)
    item: str | None = None

    @model_validator(mode="after")
    def validate_item(self) -> Self:
        if self.kind in (QuestionType.STOCK, QuestionType.AISLE) and not self.item:
            raise ValueError(f"{self.kind.value} questions require an item")
        if self.kind is QuestionType.HOURS and self.item is not None:
            raise ValueError("hours questions do not take an item")
        return self

    @classmethod
    def from_cli(
        cls,
        kind: QuestionType,
        item: str | None,
        text_override: str | None = None,
    ) -> Question:
        if kind in (QuestionType.STOCK, QuestionType.AISLE) and not item:
            raise ValueError(f"{kind.value} questions require --item")
        if kind is QuestionType.HOURS:
            item = None
        text = text_override or QUESTION_TABLE[kind].default_text_template.format(
            item=item
        )
        return cls(kind=kind, text=text, item=item)


DISCLOSURE_PHRASE = "on behalf of "
CLOSING_LINE = "Thanks, that's all I needed. Goodbye."

SILENCE_CONTINUE_LINE = (
    "If the other party pauses, keep listening briefly, then continue: "
    "confirm what you have or ask your next question."
)

DONE_STATE_LINE = (
    "When the task is done, do not end the call yourself. "
    "Delegate to the backend so it can record the result and end the call."
)

DELEGATION_POLICY_LINES = (
    "Delegation policy:",
    "Backend tools:",
    "- record_task_result, end_call.",
    "Delegate to the backend when:",
    "- the other party has given a final yes, no, time, or answer; "
    "you must decide whether to accept what they offered; the call should end.",
    "Do not end the call yourself; the backend records the result and ends it.",
    "Do not delegate to the backend when:",
    "- The other party greets you, repeats a result, or needs a brief clarification.",
    "- You can continue from these instructions and what they have already said.",
    "Do not guess or promise a result the other party has not said.",
)

GOODBYE_NEEDLES = ("goodbye", "bye for now", "have a good")


def first_spoken_line(caller: FirstName) -> str:
    return f"Hi, I'm calling on behalf of {caller}. I have one quick question."


def first_book_line(caller: FirstName) -> str:
    return f"Hi, I'm calling on behalf of {caller}. I'd like to book a table."


def _role_line(caller: FirstName, place: str) -> str:
    return (
        f"You are a calm, friendly phone assistant calling a {place} "
        f"on behalf of {caller}. Speak warmly and naturally at an unhurried "
        "pace, be clear and direct rather than overly cheerful, and if they "
        "are frustrated acknowledge it briefly then take the next helpful step."
    )


def build_instructions(caller: FirstName, question: Question) -> str:
    return "\n".join(
        (
            _role_line(caller, "business"),
            first_spoken_line(caller),
            question.text,
            DONE_STATE_LINE,
            (
                "Never book, reserve, or hold anything. "
                "Keep every turn under two sentences."
            ),
            SILENCE_CONTINUE_LINE,
            *DELEGATION_POLICY_LINES,
        )
    )


BOOK_NEEDLES = (
    "booked",
    "reserved",
    "reservation",
    "we'll see you",
    "we will see you",
    "table for",
    "you're all set",
    "you are all set",
    "confirmation",
)

DECLINE_BOOK_NEEDLES = (
    "fully booked",
    "no tables",
    "can't take",
    "cannot take",
    "no availability",
    "not available",
    "waitlist",
    "waiting list",
)


def build_book_instructions(
    caller: FirstName,
    party_size: int,
    time_text: str,
    constraints: str | None,
) -> str:
    notes = constraints or "none"
    return "\n".join(
        (
            _role_line(caller, "restaurant"),
            first_book_line(caller),
            (
                f"Party size: {party_size}. "
                f"Requested time: {time_text}. "
                f"Constraints: {notes}."
            ),
            (
                "Work with the restaurant on party, time, and constraints. "
                "Reason about alternatives they offer. "
                "Do not read a fixed list of questions."
            ),
            (
                "Never join a hold queue or waitlist. "
                "If they cannot seat this party at this time, accept the decline."
            ),
            DONE_STATE_LINE,
            "Keep every turn under two sentences.",
            SILENCE_CONTINUE_LINE,
            *DELEGATION_POLICY_LINES,
        )
    )


def request_opener(request: QuickAskRequest | BookRequest) -> str:
    if isinstance(request, BookRequest):
        return first_book_line(request.caller)
    return first_spoken_line(request.caller)


def request_instructions(request: QuickAskRequest | BookRequest) -> str:
    if isinstance(request, BookRequest):
        return build_book_instructions(
            request.caller,
            request.party_size,
            request.time_text,
            request.constraints,
        )
    return build_instructions(request.caller, request.question)


def build_backend_instructions(request: QuickAskRequest | BookRequest) -> str:
    if isinstance(request, BookRequest):
        notes = request.constraints or "none"
        task = (
            f"Caller {request.caller} wants a table for {request.party_size} "
            f"at {request.time_text}. Constraints: {notes}."
        )
        outcomes = "confirmed, declined, or no_result"
    else:
        task = (
            f"Caller {request.caller} asks: {request.question.text}"
        )
        outcomes = "answered, declined, or no_result"
    return "\n".join(
        (
            "## Voice conversation context",
            "You are helping an assistant in a live voice conversation. "
            "Transcripts can contain mistakes, unfinished phrases, and later "
            "corrections. Use the latest context and verified records. If a "
            "needed detail is still unclear, ask for that detail instead of "
            "guessing.",
            "## Task instructions",
            task,
            "Decide from the brief. Do not wait for the user. Accept a decline.",
            "Never join a hold queue or waitlist. Never invent a successful action.",
            f"Legal outcomes: {outcomes}.",
            "## Return the result",
            "Call record_task_result then end_call in the same turn with "
            "confirmed values. Return the relevant facts, whether the task "
            "is complete, and what comes next. Use confirmed values. Do not "
            "invent a successful action.",
        )
    )


class QuickAskRequest(FrozenModel):
    user_id: UserId
    to: E164
    question: Question
    caller: FirstName
    mode: Mode
    retain_call_artifacts: bool | None = None


class BookRequest(FrozenModel):
    user_id: UserId
    to: E164
    caller: FirstName
    mode: Mode
    party_size: int = Field(ge=1)
    time_text: str = Field(min_length=1)
    constraints: str | None = None
    retain_call_artifacts: bool | None = None


class Answered(FrozenModel):
    status: Literal["answered"] = "answered"
    answer_text: str = Field(min_length=1)
    call_control_id: str = ""
    transcript_ref: str | None = None
    audio_ref: str | None = None


class Declined(FrozenModel):
    status: Literal["declined"] = "declined"
    answer_text: None = None
    call_control_id: str = ""
    transcript_ref: str | None = None
    audio_ref: str | None = None


class NoAnswer(FrozenModel):
    status: Literal["no_answer"] = "no_answer"
    answer_text: None = None
    call_control_id: str = ""
    transcript_ref: str | None = None
    audio_ref: str | None = None


class Failed(FrozenModel):
    status: Literal["failed"] = "failed"
    answer_text: None = None
    call_control_id: str = ""
    transcript_ref: str | None = None
    audio_ref: str | None = None
    reason: str


class Booked(FrozenModel):
    status: Literal["booked"] = "booked"
    confirmation_text: str = Field(min_length=1)
    call_control_id: str = ""
    transcript_ref: str | None = None
    audio_ref: str | None = None
    answer_text: None = None


QuickAskResult: TypeAlias = Annotated[
    Answered | Declined | NoAnswer | Failed,
    Field(discriminator="status"),
]
QUICK_ASK_RESULT_ADAPTER = TypeAdapter(QuickAskResult)

BookResult: TypeAlias = Annotated[
    Booked | Declined | NoAnswer | Failed,
    Field(discriminator="status"),
]
BOOK_RESULT_ADAPTER = TypeAdapter(BookResult)

CallRequest: TypeAlias = QuickAskRequest | BookRequest
CallResult: TypeAlias = Annotated[
    Answered | Booked | Declined | NoAnswer | Failed,
    Field(discriminator="status"),
]
CALL_RESULT_ADAPTER = TypeAdapter(CallResult)


class ClientState(FrozenModel):
    run_id: RunId
    user_id: UserId

    def encode(self) -> str:
        return base64.b64encode(self.model_dump_json().encode()).decode()

    @classmethod
    def decode(cls, raw: str) -> ClientState:
        return cls.model_validate_json(base64.b64decode(raw))


class DialSpec(FrozenModel):
    connection_id: str
    to: E164
    from_did: E164
    stream_url: str
    webhook_url: str
    codec: Codec
    client_state: ClientState

    @field_validator("stream_url")
    @classmethod
    def validate_stream_url(cls, value: str) -> str:
        if not value.startswith("wss://"):
            raise ValueError("stream_url must start with wss://")
        return value

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("webhook_url must start with https://")
        return value


class LiveAudioFormat(FrozenModel):
    type: Literal["audio/pcm", "audio/pcmu"]
    rate: SampleRate

    @classmethod
    def for_codec(cls, codec: Codec) -> LiveAudioFormat:
        if codec is Codec.L16:
            return cls(type="audio/pcm", rate=16000)
        return cls(type="audio/pcmu", rate=8000)


class LiveSessionSpec(FrozenModel):
    model: str
    instructions: str
    audio: LiveAudioFormat
    voice: str
    backend_instructions: str = ""
    responses_model: str = "gpt-5.6-terra"
    tools: tuple[FunctionToolSpec, ...] = SESSION_TOOLS


class Turn(FrozenModel):
    speaker: Speaker
    text: str


class Transcript(FrozenModel):
    turns: tuple[Turn, ...] = ()

    def append_delta(self, speaker: Speaker, delta: str) -> Transcript:
        if self.turns and self.turns[-1].speaker is speaker:
            merged = self.turns[-1].model_copy(
                update={"text": self.turns[-1].text + delta}
            )
            return self.model_copy(update={"turns": self.turns[:-1] + (merged,)})
        return self.model_copy(
            update={"turns": self.turns + (Turn(speaker=speaker, text=delta),)}
        )

    def business_text(self) -> str:
        return " ".join(
            turn.text for turn in self.turns if turn.speaker is Speaker.BUSINESS
        )


Verdict: TypeAlias = Annotated[
    Answered | Booked | Declined, Field(discriminator="status")
]


class DialPending(FrozenModel):
    kind: Literal["dial_pending"] = "dial_pending"


class DialPlaced(FrozenModel):
    kind: Literal["dial_placed"] = "dial_placed"
    call_control_id: CallControlId
    call_leg_id: str
    call_session_id: str


DialTrack: TypeAlias = Annotated[
    DialPending | DialPlaced, Field(discriminator="kind")
]


class TelnyxAwaiting(FrozenModel):
    kind: Literal["telnyx_awaiting"] = "telnyx_awaiting"


class TelnyxRinging(FrozenModel):
    kind: Literal["telnyx_ringing"] = "telnyx_ringing"


class TelnyxAnswered(FrozenModel):
    kind: Literal["telnyx_answered"] = "telnyx_answered"


class TelnyxEnded(FrozenModel):
    kind: Literal["telnyx_ended"] = "telnyx_ended"
    cause: str
    source: str


TelnyxTrack: TypeAlias = Annotated[
    TelnyxAwaiting | TelnyxRinging | TelnyxAnswered | TelnyxEnded,
    Field(discriminator="kind"),
]


class MediaAwaiting(FrozenModel):
    kind: Literal["media_awaiting"] = "media_awaiting"


class MediaStreaming(FrozenModel):
    kind: Literal["media_streaming"] = "media_streaming"
    stream_id: StreamId
    codec: Codec
    sample_rate: SampleRate


class MediaStopped(FrozenModel):
    kind: Literal["media_stopped"] = "media_stopped"


MediaTrack: TypeAlias = Annotated[
    MediaAwaiting | MediaStreaming | MediaStopped,
    Field(discriminator="kind"),
]


class LiveDormant(FrozenModel):
    kind: Literal["live_dormant"] = "live_dormant"


class LiveStarting(FrozenModel):
    kind: Literal["live_starting"] = "live_starting"
    buffered_audio: tuple[str, ...] = ()


class LiveActive(FrozenModel):
    kind: Literal["live_active"] = "live_active"
    session_id: LiveSessionId
    activity_seq: int = 0
    stall_check_pending: bool = False
    response_id: str | None = None
    pending_tool_calls: frozenset[str] = frozenset()


class LiveClosing(FrozenModel):
    kind: Literal["live_closing"] = "live_closing"


class LiveClosed(FrozenModel):
    kind: Literal["live_closed"] = "live_closed"


LiveTrack: TypeAlias = Annotated[
    LiveDormant | LiveStarting | LiveActive | LiveClosing | LiveClosed,
    Field(discriminator="kind"),
]


class ConversationOpen(FrozenModel):
    kind: Literal["conversation_open"] = "conversation_open"
    transcript: Transcript = Transcript()


VerdictSource: TypeAlias = Literal["tool", "transcript"]


class ConversationTerminal(FrozenModel):
    kind: Literal["conversation_terminal"] = "conversation_terminal"
    transcript: Transcript
    verdict: Verdict
    verdict_source: VerdictSource = "transcript"


ConversationTrack: TypeAlias = Annotated[
    ConversationOpen | ConversationTerminal,
    Field(discriminator="kind"),
]


HANGUP_MARK = "hangup"

HangupVia: TypeAlias = Literal["trail", "drain_timeout", "failure", "stall", "tool"]


class HangupIdle(FrozenModel):
    kind: Literal["hangup_idle"] = "hangup_idle"


class HangupFarewell(FrozenModel):
    kind: Literal["hangup_farewell"] = "hangup_farewell"
    via: HangupVia
    wait_for_speech: bool = False


class HangupDraining(FrozenModel):
    kind: Literal["hangup_draining"] = "hangup_draining"
    via: HangupVia = "trail"


class HangupMarked(FrozenModel):
    kind: Literal["hangup_marked"] = "hangup_marked"
    via: HangupVia = "trail"


class HangupRequested(FrozenModel):
    kind: Literal["hangup_requested"] = "hangup_requested"
    via: HangupVia


class HangupConfirmed(FrozenModel):
    kind: Literal["hangup_confirmed"] = "hangup_confirmed"
    via: HangupVia


class HangupUnconfirmed(FrozenModel):
    kind: Literal["hangup_unconfirmed"] = "hangup_unconfirmed"
    via: HangupVia


HangupTrack: TypeAlias = Annotated[
    HangupIdle
    | HangupFarewell
    | HangupDraining
    | HangupMarked
    | HangupRequested
    | HangupConfirmed
    | HangupUnconfirmed,
    Field(discriminator="kind"),
]


class Call(FrozenModel):
    run_id: RunId
    request: QuickAskRequest | BookRequest
    from_did: E164
    dial: DialTrack = DialPending()
    telnyx: TelnyxTrack = TelnyxAwaiting()
    media: MediaTrack = MediaAwaiting()
    live: LiveTrack = LiveDormant()
    conversation: ConversationTrack = ConversationOpen()
    hangup: HangupTrack = HangupIdle()
    seen_webhook_ids: frozenset[str] = frozenset()
    finished: bool = False
    resampled: bool = False
    task: RecordedTask | None = None


class DialPlacedEvent(FrozenModel):
    kind: Literal["dial_placed"] = "dial_placed"
    call_control_id: CallControlId
    call_leg_id: str
    call_session_id: str


WebhookEventType: TypeAlias = Literal[
    "call.initiated",
    "call.answered",
    "streaming.started",
    "streaming.failed",
    "streaming.stopped",
    "call.hangup",
]


class WebhookEvent(FrozenModel):
    kind: Literal["webhook"] = "webhook"
    webhook_id: str
    event_type: WebhookEventType
    call_control_id: CallControlId
    hangup_cause: str | None = None
    hangup_source: str | None = None
    failure_reason: str | None = None


class MediaStartEvent(FrozenModel):
    kind: Literal["media_start"] = "media_start"
    stream_id: StreamId
    call_control_id: CallControlId
    codec: Codec
    sample_rate: SampleRate


class MediaChunkEvent(FrozenModel):
    kind: Literal["media_chunk"] = "media_chunk"
    stream_id: StreamId
    payload_b64: str


class MediaStopEvent(FrozenModel):
    kind: Literal["media_stop"] = "media_stop"
    stream_id: StreamId


class MediaMarkEvent(FrozenModel):
    kind: Literal["media_mark"] = "media_mark"
    stream_id: StreamId
    name: str


class LiveStartedEvent(FrozenModel):
    kind: Literal["live_started"] = "live_started"
    session_id: LiveSessionId


class LiveInputTranscriptEvent(FrozenModel):
    kind: Literal["live_input_transcript"] = "live_input_transcript"
    delta: str


class LiveOutputTranscriptEvent(FrozenModel):
    kind: Literal["live_output_transcript"] = "live_output_transcript"
    delta: str


class LiveOutputAudioEvent(FrozenModel):
    kind: Literal["live_output_audio"] = "live_output_audio"
    delta_b64: str


class LiveClosedEvent(FrozenModel):
    kind: Literal["live_closed"] = "live_closed"


class LiveErrorEvent(FrozenModel):
    kind: Literal["live_error"] = "live_error"
    message: str


class LiveNoteEvent(FrozenModel):
    kind: Literal["live_note"] = "live_note"
    event_type: str
    client_event_id: str | None = None
    usage_seconds: float | None = None


class LiveDelegationEvent(FrozenModel):
    kind: Literal["live_delegation"] = "live_delegation"
    delegation_id: str
    target: Literal["client", "responses"] = "client"
    response_id: str | None = None


class LiveToolCallEvent(FrozenModel):
    kind: Literal["live_tool_call"] = "live_tool_call"
    delegation_id: str
    response_id: str | None = None
    call_id: str
    name: str
    arguments_json: str


class LiveResponseEvent(FrozenModel):
    kind: Literal["live_response"] = "live_response"
    response_kind: Literal["created", "completed", "failed", "incomplete"]
    delegation_id: str
    response_id: str | None = None


class TimeoutEvent(FrozenModel):
    kind: Literal["timeout"] = "timeout"
    webhook_host: str | None = None
    stream_host: str | None = None


class HangupTrailElapsedEvent(FrozenModel):
    kind: Literal["hangup_trail_elapsed"] = "hangup_trail_elapsed"


class HangupDrainTimeoutEvent(FrozenModel):
    kind: Literal["hangup_drain_timeout"] = "hangup_drain_timeout"


class HangupConfirmTimeoutEvent(FrozenModel):
    kind: Literal["hangup_confirm_timeout"] = "hangup_confirm_timeout"


class FarewellIdleCheckEvent(FrozenModel):
    kind: Literal["farewell_idle_check"] = "farewell_idle_check"
    activity_seq: int


class FarewellElapsedEvent(FrozenModel):
    kind: Literal["farewell_elapsed"] = "farewell_elapsed"


class LiveStallCheckEvent(FrozenModel):
    kind: Literal["live_stall_check"] = "live_stall_check"
    activity_seq: int


class LiveStallGiveUpEvent(FrozenModel):
    kind: Literal["live_stall_give_up"] = "live_stall_give_up"


class MediaPlayFailedEvent(FrozenModel):
    kind: Literal["media_play_failed"] = "media_play_failed"
    stream_id: StreamId
    error_type: str


class RuntimeFailureEvent(FrozenModel):
    kind: Literal["runtime_failure"] = "runtime_failure"
    reason: str


CallEvent: TypeAlias = Annotated[
    DialPlacedEvent
    | WebhookEvent
    | MediaStartEvent
    | MediaChunkEvent
    | MediaStopEvent
    | MediaMarkEvent
    | LiveStartedEvent
    | LiveInputTranscriptEvent
    | LiveOutputTranscriptEvent
    | LiveOutputAudioEvent
    | LiveClosedEvent
    | LiveErrorEvent
    | LiveNoteEvent
    | LiveDelegationEvent
    | LiveToolCallEvent
    | LiveResponseEvent
    | TimeoutEvent
    | HangupTrailElapsedEvent
    | HangupDrainTimeoutEvent
    | HangupConfirmTimeoutEvent
    | FarewellIdleCheckEvent
    | FarewellElapsedEvent
    | LiveStallCheckEvent
    | LiveStallGiveUpEvent
    | MediaPlayFailedEvent
    | RuntimeFailureEvent,
    Field(discriminator="kind"),
]


class DialEffect(FrozenModel):
    kind: Literal["dial"] = "dial"
    spec: DialSpec


class OpenLiveEffect(FrozenModel):
    kind: Literal["open_live"] = "open_live"
    spec: LiveSessionSpec


class SpeakFirstEffect(FrozenModel):
    kind: Literal["speak_first"] = "speak_first"
    line: str


class ReplyDelegationEffect(FrozenModel):
    kind: Literal["reply_delegation"] = "reply_delegation"
    delegation_id: str
    thinking: str
    commentary: str


class SteerLiveEffect(FrozenModel):
    kind: Literal["steer_live"] = "steer_live"
    instructions: str
    event_id: str


class ToolResultEffect(FrozenModel):
    kind: Literal["tool_result"] = "tool_result"
    call_id: str
    output: str
    event_id: str


class ContinueResponseEffect(FrozenModel):
    kind: Literal["continue_response"] = "continue_response"
    event_id: str


class WriteTaskEffect(FrozenModel):
    kind: Literal["write_task"] = "write_task"
    payload: dict[str, Any]


class ForwardAudioEffect(FrozenModel):
    kind: Literal["forward_audio"] = "forward_audio"
    payload_b64: str


class PlayAudioEffect(FrozenModel):
    kind: Literal["play_audio"] = "play_audio"
    delta_b64: str


class MarkEffect(FrozenModel):
    kind: Literal["mark"] = "mark"
    name: str


class ScheduleEffect(FrozenModel):
    kind: Literal["schedule"] = "schedule"
    delay_s: float = Field(ge=0)
    event: CallEvent


class HangupEffect(FrozenModel):
    kind: Literal["hangup"] = "hangup"
    call_control_id: CallControlId


class CloseLiveEffect(FrozenModel):
    kind: Literal["close_live"] = "close_live"


class FinishEffect(FrozenModel):
    kind: Literal["finish"] = "finish"
    result: CallResult


Effect: TypeAlias = Annotated[
    DialEffect
    | OpenLiveEffect
    | SpeakFirstEffect
    | ReplyDelegationEffect
    | SteerLiveEffect
    | ToolResultEffect
    | ContinueResponseEffect
    | WriteTaskEffect
    | ForwardAudioEffect
    | PlayAudioEffect
    | MarkEffect
    | ScheduleEffect
    | HangupEffect
    | CloseLiveEffect
    | FinishEffect,
    Field(discriminator="kind"),
]


class ReduceSettings(FrozenModel):
    live_model: str
    voice: str
    responses_model: str = "gpt-5.6-terra"
    hangup_trailing_silence_s: float = Field(ge=0)
    hangup_drain_timeout_s: float = Field(ge=0)
    live_silence_failsafe_s: float = Field(default=20.0, ge=0)
    farewell_idle_s: float = Field(default=0.8, ge=0)
    farewell_max_s: float = Field(default=4.0, ge=0)
    hangup_confirm_timeout_s: float = Field(default=5.0, ge=0)


def _collapsed_agent_text(transcript: Transcript) -> str:
    agent_text = " ".join(
        turn.text for turn in transcript.turns if turn.speaker is Speaker.AGENT
    )
    return " ".join(agent_text.split())


def _agent_said_closing(transcript: Transcript) -> bool:
    return CLOSING_LINE.casefold() in _collapsed_agent_text(transcript).casefold()


def _last_agent_has_goodbye(transcript: Transcript) -> bool:
    last_agent = next(
        (
            turn.text
            for turn in reversed(transcript.turns)
            if turn.speaker is Speaker.AGENT
        ),
        "",
    )
    lowered = last_agent.casefold()
    return any(needle in lowered for needle in GOODBYE_NEEDLES)


def _ready_to_assess(transcript: Transcript) -> bool:
    return _agent_said_closing(transcript) or _last_agent_has_goodbye(
        transcript
    )


def exception_reason(error: BaseException) -> str:
    message = str(error).strip()
    name = type(error).__name__
    if not message:
        return name
    return f"{name}: {message}"


def next_step_line(
    request: QuickAskRequest | BookRequest, transcript: Transcript
) -> str:
    lowered = transcript.business_text().casefold()
    if isinstance(request, BookRequest):
        has_book = any(needle in lowered for needle in BOOK_NEEDLES)
        has_decline = any(needle in lowered for needle in DECLINE_BOOK_NEEDLES)
        if has_book or has_decline:
            return CLOSING_LINE
        return (
            f"Can you confirm the reservation for {request.party_size} at "
            f"{request.time_text} under {request.caller}?"
        )
    spec = QUESTION_TABLE[request.question.kind]
    has_answer = any(needle in lowered for needle in spec.answer_needles)
    has_decline = any(needle in lowered for needle in spec.decline_needles)
    if has_answer or has_decline:
        return CLOSING_LINE
    return request.question.text


def _delegation_thinking(request: QuickAskRequest | BookRequest) -> str:
    if isinstance(request, BookRequest):
        notes = request.constraints or "none"
        return (
            "Continue from the conversation. "
            f"Task: party {request.party_size} at {request.time_text} for "
            f"{request.caller}; constraints {notes}."
        )
    return (
        "Continue from the conversation. "
        f"Task: {request.question.text}."
    )


def assess_transcript(
    transcript: Transcript, question: Question
) -> Answered | Declined | None:
    if not _ready_to_assess(transcript):
        return None
    return assess_transcript_final(transcript, question)


def assess_transcript_final(
    transcript: Transcript, question: Question
) -> Answered | Declined | None:
    business_text = transcript.business_text()
    if not business_text.strip():
        return None
    lowered = business_text.casefold()
    spec = QUESTION_TABLE[question.kind]
    has_answer = any(needle in lowered for needle in spec.answer_needles)
    has_decline = any(needle in lowered for needle in spec.decline_needles)
    if has_decline and not has_answer:
        return Declined()
    last_business_turn = next(
        turn.text.strip()
        for turn in reversed(transcript.turns)
        if turn.speaker is Speaker.BUSINESS
    )
    return Answered(answer_text=last_business_turn)


def assess_book(transcript: Transcript) -> Booked | Declined | None:
    if not _ready_to_assess(transcript):
        return None
    return assess_book_final(transcript)


def assess_book_final(transcript: Transcript) -> Booked | Declined | None:
    business_text = transcript.business_text()
    if not business_text.strip():
        return None
    lowered = business_text.casefold()
    has_book = any(needle in lowered for needle in BOOK_NEEDLES)
    has_decline = any(needle in lowered for needle in DECLINE_BOOK_NEEDLES)
    if has_decline:
        return Declined()
    if not has_book:
        return None
    last_business_turn = next(
        turn.text.strip()
        for turn in reversed(transcript.turns)
        if turn.speaker is Speaker.BUSINESS
    )
    return Booked(confirmation_text=last_business_turn)


def assess_conversation(
    transcript: Transcript, request: QuickAskRequest | BookRequest
) -> Answered | Booked | Declined | None:
    if isinstance(request, BookRequest):
        return assess_book(transcript)
    return assess_transcript(transcript, request.question)


def assess_conversation_final(
    transcript: Transcript, request: QuickAskRequest | BookRequest
) -> Answered | Booked | Declined | None:
    if isinstance(request, BookRequest):
        return assess_book_final(transcript)
    return assess_transcript_final(transcript, request.question)


def _should_enter_hangup_drain(call: Call, transcript: Transcript) -> bool:
    if not isinstance(call.hangup, HangupIdle) or not isinstance(
        call.dial, DialPlaced
    ):
        return False
    return _agent_said_closing(transcript)


def _enter_hangup_drain(
    call: Call,
    settings: ReduceSettings,
    updates: dict[str, Any],
    via: HangupVia = "trail",
) -> tuple[dict[str, Any], tuple[Effect, ...]]:
    # The closing line's transcript arrives while GPT-Live is still
    # streaming that line's audio. Closing the session makes it flush
    # what is left and confirm with session.closed, which is when the
    # single playback mark can go out behind the last chunk.
    effects: tuple[Effect, ...] = (
        ScheduleEffect(
            delay_s=settings.hangup_drain_timeout_s,
            event=HangupDrainTimeoutEvent(),
        ),
    )
    if isinstance(call.live, (LiveStarting, LiveActive)):
        updates["hangup"] = HangupDraining(via=via)
        updates["live"] = LiveClosing()
        effects += (CloseLiveEffect(),)
    else:
        updates["hangup"] = HangupMarked(via=via)
        effects += (MarkEffect(name=HANGUP_MARK),)
    return updates, effects


def _enter_farewell(
    call: Call,
    settings: ReduceSettings,
    via: HangupVia,
    *,
    wait_for_speech: bool = False,
) -> tuple[Call, tuple[Effect, ...]]:
    if not isinstance(call.hangup, HangupIdle) or not isinstance(
        call.dial, DialPlaced
    ):
        return call, ()
    if not isinstance(call.live, LiveActive):
        updates, effects = _enter_hangup_drain(call, settings, {}, via=via)
        return call.model_copy(update=updates), effects
    effects: tuple[Effect, ...] = (
        ScheduleEffect(
            delay_s=settings.farewell_max_s,
            event=FarewellElapsedEvent(),
        ),
    )
    if not wait_for_speech:
        effects = (
            ScheduleEffect(
                delay_s=settings.farewell_idle_s,
                event=FarewellIdleCheckEvent(activity_seq=call.live.activity_seq),
            ),
        ) + effects
    return call.model_copy(
        update={
            "hangup": HangupFarewell(via=via, wait_for_speech=wait_for_speech)
        }
    ), effects


def _maybe_open_live(
    call: Call, settings: ReduceSettings
) -> tuple[Call, tuple[Effect, ...]]:
    if (
        isinstance(call.telnyx, TelnyxAnswered)
        and isinstance(call.media, MediaStreaming)
        and isinstance(call.live, LiveDormant)
    ):
        spec = LiveSessionSpec(
            model=settings.live_model,
            instructions=request_instructions(call.request),
            audio=LiveAudioFormat.for_codec(call.media.codec),
            voice=settings.voice,
            backend_instructions=build_backend_instructions(call.request),
            responses_model=settings.responses_model,
            tools=SESSION_TOOLS,
        )
        return (
            call.model_copy(update={"live": LiveStarting()}),
            (OpenLiveEffect(spec=spec),),
        )
    return call, ()


def _resample_b64(
    payload_b64: str, from_rate: SampleRate, to_rate: SampleRate
) -> tuple[str, bool]:
    if from_rate == to_rate:
        return payload_b64, False
    pcm = resample_pcm16(base64.b64decode(payload_b64), from_rate, to_rate)
    return base64.b64encode(pcm).decode(), True


def _to_live_rate(media: MediaStreaming, payload_b64: str) -> tuple[str, bool]:
    return _resample_b64(
        payload_b64,
        media.sample_rate,
        LiveAudioFormat.for_codec(media.codec).rate,
    )


def _to_media_rate(media: MediaStreaming, delta_b64: str) -> tuple[str, bool]:
    return _resample_b64(
        delta_b64,
        LiveAudioFormat.for_codec(media.codec).rate,
        media.sample_rate,
    )


def _call_control_id(call: Call) -> str:
    if isinstance(call.dial, DialPlaced):
        return str(call.dial.call_control_id)
    return ""


def _result_from_verdict(call: Call) -> Answered | Booked | Declined:
    call_control_id = _call_control_id(call)
    if not isinstance(call.conversation, ConversationTerminal):
        raise ValueError("terminal conversation required")
    verdict = call.conversation.verdict
    return verdict.model_copy(update={"call_control_id": call_control_id})


def _finish(
    call: Call, result: CallResult, effects: tuple[Effect, ...] = ()
) -> tuple[Call, tuple[Effect, ...]]:
    finished = call.model_copy(update={"finished": True})
    return finished, effects + (FinishEffect(result=result),)


def _close_live(call: Call) -> tuple[dict[str, Any], tuple[Effect, ...]]:
    if isinstance(call.live, (LiveStarting, LiveActive)):
        return {"live": LiveClosed()}, (CloseLiveEffect(),)
    return {}, ()


def _maybe_fallback_verdict(call: Call) -> Call:
    if isinstance(call.conversation, ConversationTerminal):
        return call
    if not isinstance(call.conversation, ConversationOpen):
        return call
    if call.task is not None:
        verdict = _verdict_from_record_fields(
            call.task.outcome,
            call.task.summary,
            call.task.confirmed_fields,
        )
        if verdict is None:
            return call
        return call.model_copy(
            update={
                "conversation": ConversationTerminal(
                    transcript=call.conversation.transcript,
                    verdict=verdict,
                    verdict_source="tool",
                )
            }
        )
    verdict = assess_conversation_final(
        call.conversation.transcript, call.request
    )
    if verdict is None:
        return call
    return call.model_copy(
        update={
            "conversation": ConversationTerminal(
                transcript=call.conversation.transcript,
                verdict=verdict,
                verdict_source="transcript",
            )
        }
    )


def _end_for_webhook(
    call: Call, previous_telnyx: TelnyxTrack, effects: tuple[Effect, ...]
) -> tuple[Call, tuple[Effect, ...]]:
    updates, close_effects = _close_live(call)
    extra_effects = effects + close_effects
    if isinstance(call.hangup, HangupRequested):
        updates["hangup"] = HangupConfirmed(via=call.hangup.via)
    call = call.model_copy(update=updates)
    call = _maybe_fallback_verdict(call)
    if isinstance(call.conversation, ConversationTerminal):
        return _finish(call, _result_from_verdict(call), extra_effects)
    call_control_id = _call_control_id(call)
    if not isinstance(previous_telnyx, TelnyxAnswered):
        return _finish(
            call,
            NoAnswer(call_control_id=call_control_id),
            extra_effects,
        )
    return _finish(
        call,
        Failed(
            call_control_id=call_control_id,
            reason="far party hung up before an answer",
        ),
        extra_effects,
    )


def _hangup_confirm_schedule(settings: ReduceSettings) -> ScheduleEffect:
    return ScheduleEffect(
        delay_s=settings.hangup_confirm_timeout_s,
        event=HangupConfirmTimeoutEvent(),
    )


def _hang_up(
    call: Call, via: HangupVia, settings: ReduceSettings
) -> tuple[Call, tuple[Effect, ...]]:
    if not isinstance(call.dial, DialPlaced):
        raise ValueError("placed dial required")
    updates, effects = _close_live(call)
    updates["hangup"] = HangupRequested(via=via)
    return (
        call.model_copy(update=updates),
        (HangupEffect(call_control_id=call.dial.call_control_id),)
        + effects
        + (_hangup_confirm_schedule(settings),),
    )


def timeout_failure_reason(call: Call, event: TimeoutEvent) -> str:
    webhook_host = event.webhook_host or "unknown"
    stream_host = event.stream_host or "unknown"
    hosts = f"webhook_host={webhook_host}, stream_host={stream_host}"
    if (
        isinstance(call.dial, DialPlaced)
        and isinstance(call.telnyx, TelnyxAwaiting)
        and isinstance(call.media, MediaAwaiting)
        and isinstance(call.live, LiveDormant)
    ):
        return (
            "event timeout: Telnyx placed the dial but never delivered "
            f"webhooks or media to this process ({hosts}). "
            "This is a webhook/media delivery miss, not GPT-Live, "
            "speak_first, or 8 kHz audio. CLI --live hosts CallHandle "
            "in this process; PUBLIC_BASE_URL must be a tunnel to it. "
            "A Fly origin is a different process with no BOOK entry."
        )
    if (
        isinstance(call.dial, DialPlaced)
        and isinstance(call.media, MediaAwaiting)
        and isinstance(call.live, LiveDormant)
    ):
        return (
            "event timeout: Telnyx webhooks reached this process but media "
            f"never started ({hosts}). Telnyx did not open /media-stream "
            "at that host. This is a media delivery miss, not GPT-Live "
            "or speak_first."
        )
    return "event timeout"


def _fail(
    call: Call,
    reason: str,
    settings: ReduceSettings,
    via: HangupVia = "failure",
) -> tuple[Call, tuple[Effect, ...]]:
    extra: tuple[Effect, ...] = ()
    if (
        isinstance(call.dial, DialPlaced)
        and not isinstance(call.telnyx, TelnyxEnded)
        and isinstance(
            call.hangup,
            (HangupIdle, HangupFarewell, HangupDraining, HangupMarked),
        )
    ):
        call, extra = _hang_up(call, via, settings)
    else:
        close_updates, close_effects = _close_live(call)
        call = call.model_copy(update=close_updates)
        extra = close_effects
    return _finish(
        call,
        Failed(call_control_id=_call_control_id(call), reason=reason),
        extra,
    )


def _bump_stall(
    call: Call, settings: ReduceSettings, updates: dict[str, Any]
) -> tuple[dict[str, Any], tuple[Effect, ...]]:
    hangup = updates.get("hangup", call.hangup)
    live = updates.get("live", call.live)
    if not isinstance(hangup, HangupIdle) or not isinstance(live, LiveActive):
        return updates, ()
    next_seq = live.activity_seq + 1
    extra: tuple[Effect, ...] = ()
    pending = live.stall_check_pending
    if not pending:
        extra = (
            ScheduleEffect(
                delay_s=settings.live_silence_failsafe_s,
                event=LiveStallCheckEvent(activity_seq=next_seq),
            ),
        )
        pending = True
    updates["live"] = live.model_copy(
        update={"activity_seq": next_seq, "stall_check_pending": pending}
    )
    return updates, extra


def _silence_failsafe(
    call: Call, settings: ReduceSettings
) -> tuple[Call, tuple[Effect, ...]]:
    if not isinstance(call.live, LiveActive) or not isinstance(
        call.hangup, HangupIdle
    ):
        return call, ()
    steered, farewell_effects = _enter_farewell(call, settings, via="stall")
    return steered, (
        SteerLiveEffect(
            instructions=(
                "The line has been quiet. Wrap up politely and say goodbye."
            ),
            event_id="silence-failsafe",
        ),
    ) + farewell_effects


def _allowed_outcomes(request: QuickAskRequest | BookRequest) -> frozenset[str]:
    return BOOK_OUTCOMES if isinstance(request, BookRequest) else ASK_OUTCOMES


def _verdict_from_record_fields(
    outcome: str,
    summary: str,
    confirmed_fields: ConfirmedFields,
) -> Answered | Booked | Declined | None:
    if outcome == "confirmed":
        return Booked(confirmation_text=summary)
    if outcome == "answered":
        return Answered(answer_text=confirmed_fields.answer_text or summary)
    if outcome == "declined":
        return Declined()
    return None


def _verdict_from_record(
    args: RecordTaskResultArgs,
) -> Answered | Booked | Declined | None:
    return _verdict_from_record_fields(
        args.outcome, args.summary, args.confirmed_fields
    )


def _handle_tool_call(
    call: Call, event: LiveToolCallEvent, settings: ReduceSettings
) -> tuple[Call, tuple[Effect, ...]]:
    if not isinstance(call.live, LiveActive):
        return call, ()
    updates: dict[str, Any] = {}
    updates, stall_effects = _bump_stall(call, settings, updates)
    call = call.model_copy(update=updates) if updates else call
    result_effects = (
        ToolResultEffect(
            call_id=event.call_id,
            output="",
            event_id=f"tool-result-{event.call_id}",
        ),
        ContinueResponseEffect(event_id=f"continue-{event.call_id}"),
    )
    if event.name == "record_task_result":
        try:
            args = parse_record_task_result(event.arguments_json)
        except Exception as error:
            output = tool_error(exception_reason(error))
            result_effects = (
                result_effects[0].model_copy(update={"output": output}),
                result_effects[1],
            )
            return call, result_effects + stall_effects
        allowed = _allowed_outcomes(call.request)
        problem = check_record_task_result(args, allowed)
        if problem is not None:
            output = tool_error(
                problem, allowed_outcomes=sorted(allowed)
            )
            result_effects = (
                result_effects[0].model_copy(update={"output": output}),
                result_effects[1],
            )
            return call, result_effects + stall_effects
        recorded = RecordedTask(
            call_id=event.call_id,
            name=event.name,
            outcome=args.outcome,
            summary=args.summary,
            confirmed_fields=args.confirmed_fields,
        )
        call_updates: dict[str, Any] = {"task": recorded}
        verdict = _verdict_from_record(args)
        if verdict is not None and isinstance(
            call.conversation, ConversationOpen
        ):
            call_updates["conversation"] = ConversationTerminal(
                transcript=call.conversation.transcript,
                verdict=verdict,
                verdict_source="tool",
            )
        output = tool_ok(
            recorded=args.outcome,
            next="the application will hang up; you may still call end_call",
        )
        result_effects = (
            result_effects[0].model_copy(update={"output": output}),
            result_effects[1],
            WriteTaskEffect(
                payload={
                    "call_id": event.call_id,
                    "name": event.name,
                    "delegation_id": event.delegation_id,
                    "outcome": args.outcome,
                    "summary": args.summary,
                    "confirmed_fields": args.confirmed_fields.model_dump(
                        mode="json"
                    ),
                }
            ),
        )
        call = call.model_copy(update=call_updates)
        farewell_effects: tuple[Effect, ...] = ()
        if verdict is not None and isinstance(call.hangup, HangupIdle):
            call, farewell_effects = _enter_farewell(
                call, settings, via="tool", wait_for_speech=True
            )
        return call, result_effects + stall_effects + farewell_effects
    if event.name == "end_call":
        try:
            args = parse_end_call(event.arguments_json)
        except Exception as error:
            output = tool_error(exception_reason(error))
            result_effects = (
                result_effects[0].model_copy(update={"output": output}),
                result_effects[1],
            )
            return call, result_effects + stall_effects
        if args.reason == "task_done" and call.task is None:
            output = tool_error("record_task_result first")
            result_effects = (
                result_effects[0].model_copy(update={"output": output}),
                result_effects[1],
            )
            return call, result_effects + stall_effects
        call_updates: dict[str, Any] = {}
        if call.task is None and isinstance(
            call.conversation, ConversationOpen
        ):
            if args.reason in {"declined", "cannot_help"}:
                call_updates["conversation"] = ConversationTerminal(
                    transcript=call.conversation.transcript,
                    verdict=Declined(),
                    verdict_source="tool",
                )
            elif args.reason in {"wrong_number", "voicemail"}:
                pass
        if call.task is not None and call.task.outcome == "no_result":
            pass
        output = tool_ok(hanging_up=True)
        result_effects = (
            result_effects[0].model_copy(update={"output": output}),
            result_effects[1],
        )
        call = call.model_copy(update=call_updates) if call_updates else call
        if args.reason in {"wrong_number", "voicemail"} and call.task is None:
            call, fail_effects = _fail(
                call, f"end_call:{args.reason}", settings, via="tool"
            )
            return call, result_effects + stall_effects + fail_effects
        if isinstance(
            call.hangup,
            (HangupFarewell, HangupDraining, HangupMarked, HangupRequested),
        ):
            return call, result_effects + stall_effects
        farewell_call, farewell_effects = _enter_farewell(
            call, settings, via="tool"
        )
        return farewell_call, result_effects + stall_effects + farewell_effects
    output = tool_error(f"unknown tool {event.name!r}")
    result_effects = (
        result_effects[0].model_copy(update={"output": output}),
        result_effects[1],
    )
    return call, result_effects + stall_effects


def reduce(
    call: Call, event: CallEvent, settings: ReduceSettings
) -> tuple[Call, tuple[Effect, ...]]:
    if call.finished:
        return call, ()

    if isinstance(event, DialPlacedEvent):
        if not isinstance(call.dial, DialPending):
            return call, ()
        return (
            call.model_copy(
                update={
                    "dial": DialPlaced(
                        call_control_id=event.call_control_id,
                        call_leg_id=event.call_leg_id,
                        call_session_id=event.call_session_id,
                    )
                }
            ),
            (),
        )

    if isinstance(event, WebhookEvent):
        if event.webhook_id in call.seen_webhook_ids:
            return call, ()
        previous_telnyx = call.telnyx
        updates: dict[str, Any] = {
            "seen_webhook_ids": call.seen_webhook_ids | {event.webhook_id}
        }
        if event.event_type == "call.initiated":
            updates["telnyx"] = TelnyxRinging()
        elif event.event_type == "call.answered":
            updates["telnyx"] = TelnyxAnswered()
        elif event.event_type == "streaming.stopped":
            updates["media"] = MediaStopped()
        elif event.event_type == "call.hangup":
            updates["telnyx"] = TelnyxEnded(
                cause=event.hangup_cause or "unknown",
                source=event.hangup_source or "unknown",
            )
        call = call.model_copy(update=updates)
        if event.event_type == "call.hangup":
            return _end_for_webhook(call, previous_telnyx, ())
        if event.event_type == "streaming.failed":
            reason = event.failure_reason or "unknown"
            return _fail(
                call, f"Telnyx media streaming failed: {reason}", settings
            )
        return _maybe_open_live(call, settings)

    if isinstance(event, MediaStartEvent):
        call = call.model_copy(
            update={
                "media": MediaStreaming(
                    stream_id=event.stream_id,
                    codec=event.codec,
                    sample_rate=event.sample_rate,
                )
            }
        )
        return _maybe_open_live(call, settings)

    if isinstance(event, MediaChunkEvent):
        if not isinstance(call.media, MediaStreaming) or not isinstance(
            call.live, (LiveStarting, LiveActive)
        ):
            return call, ()
        payload, did_resample = _to_live_rate(call.media, event.payload_b64)
        extra = {"resampled": True} if did_resample else {}
        if isinstance(call.live, LiveActive):
            if extra:
                call = call.model_copy(update=extra)
            return call, (ForwardAudioEffect(payload_b64=payload),)
        buffered = call.live.buffered_audio + (payload,)
        extra["live"] = LiveStarting(buffered_audio=buffered)
        return (
            call.model_copy(update=extra),
            (),
        )

    if isinstance(event, (MediaStopEvent, MediaPlayFailedEvent)):
        return call.model_copy(update={"media": MediaStopped()}), ()

    if isinstance(event, LiveStartedEvent):
        if not isinstance(call.live, LiveStarting):
            return call, ()
        effects: tuple[Effect, ...] = (
            SpeakFirstEffect(line=request_opener(call.request)),
        ) + tuple(
            ForwardAudioEffect(payload_b64=payload)
            for payload in call.live.buffered_audio
        )
        effects += (
            ScheduleEffect(
                delay_s=settings.live_silence_failsafe_s,
                event=LiveStallCheckEvent(activity_seq=0),
            ),
        )
        return (
            call.model_copy(
                update={
                    "live": LiveActive(
                        session_id=event.session_id,
                        stall_check_pending=True,
                    )
                }
            ),
            effects,
        )

    if isinstance(event, LiveOutputAudioEvent):
        if not isinstance(call.media, MediaStreaming):
            return call, ()
        delta, did_resample = _to_media_rate(call.media, event.delta_b64)
        updates: dict[str, Any] = {}
        if did_resample:
            updates["resampled"] = True
        play: tuple[Effect, ...] = (PlayAudioEffect(delta_b64=delta),)
        if isinstance(call.hangup, HangupFarewell) and isinstance(
            call.live, LiveActive
        ):
            next_seq = call.live.activity_seq + 1
            hangup = call.hangup
            if hangup.wait_for_speech:
                hangup = hangup.model_copy(update={"wait_for_speech": False})
            updates["live"] = call.live.model_copy(
                update={"activity_seq": next_seq}
            )
            updates["hangup"] = hangup
            call = call.model_copy(update=updates)
            return call, play + (
                ScheduleEffect(
                    delay_s=settings.farewell_idle_s,
                    event=FarewellIdleCheckEvent(activity_seq=next_seq),
                ),
            )
        updates, stall_effects = _bump_stall(call, settings, updates)
        call = call.model_copy(update=updates) if updates else call
        return call, play + stall_effects

    if isinstance(event, MediaMarkEvent):
        if not isinstance(call.hangup, HangupMarked) or (
            event.name != HANGUP_MARK
        ):
            return call, ()
        return call, (
            ScheduleEffect(
                delay_s=settings.hangup_trailing_silence_s,
                event=HangupTrailElapsedEvent(),
            ),
        )

    if isinstance(event, HangupTrailElapsedEvent):
        if not isinstance(call.hangup, HangupMarked):
            return call, ()
        return _hang_up(call, via=call.hangup.via, settings=settings)

    if isinstance(event, HangupDrainTimeoutEvent):
        if not isinstance(call.hangup, (HangupDraining, HangupMarked)):
            return call, ()
        return _hang_up(call, via="drain_timeout", settings=settings)

    if isinstance(event, HangupConfirmTimeoutEvent):
        if not isinstance(call.hangup, HangupRequested):
            return call, ()
        call = call.model_copy(
            update={"hangup": HangupUnconfirmed(via=call.hangup.via)}
        )
        call = _maybe_fallback_verdict(call)
        if isinstance(call.conversation, ConversationTerminal):
            return _finish(call, _result_from_verdict(call))
        return _finish(
            call,
            Failed(
                call_control_id=_call_control_id(call),
                reason=(
                    "hangup unconfirmed: Telnyx did not send call.hangup"
                ),
            ),
        )

    if isinstance(event, FarewellIdleCheckEvent):
        if not isinstance(call.hangup, HangupFarewell) or not isinstance(
            call.live, LiveActive
        ):
            return call, ()
        if call.hangup.wait_for_speech:
            return call, ()
        if event.activity_seq != call.live.activity_seq:
            return call, ()
        updates, effects = _enter_hangup_drain(
            call, settings, {}, via=call.hangup.via
        )
        return call.model_copy(update=updates), effects

    if isinstance(event, FarewellElapsedEvent):
        if not isinstance(call.hangup, HangupFarewell):
            return call, ()
        updates, effects = _enter_hangup_drain(
            call, settings, {}, via=call.hangup.via
        )
        return call.model_copy(update=updates), effects

    if isinstance(event, LiveStallCheckEvent):
        if not isinstance(call.live, LiveActive) or not isinstance(
            call.hangup, HangupIdle
        ):
            return call, ()
        if event.activity_seq != call.live.activity_seq:
            return call.model_copy(
                update={
                    "live": call.live.model_copy(
                        update={"stall_check_pending": True}
                    )
                }
            ), (
                ScheduleEffect(
                    delay_s=settings.live_silence_failsafe_s,
                    event=LiveStallCheckEvent(
                        activity_seq=call.live.activity_seq
                    ),
                ),
            )
        return _silence_failsafe(call, settings)

    if isinstance(event, LiveStallGiveUpEvent):
        if not isinstance(call.hangup, (HangupIdle, HangupFarewell)):
            return call, ()
        return _fail(
            call,
            (
                "agent stalled: no live output for "
                f"{settings.live_silence_failsafe_s}s"
            ),
            settings,
            via="stall",
        )

    if isinstance(event, LiveDelegationEvent):
        if not isinstance(call.live, LiveActive):
            return call, ()
        updates: dict[str, Any] = {}
        if event.response_id is not None:
            updates["live"] = call.live.model_copy(
                update={"response_id": event.response_id}
            )
        updates, stall_effects = _bump_stall(call, settings, updates)
        call = call.model_copy(update=updates) if updates else call
        if event.target == "responses":
            return call, stall_effects
        if not isinstance(call.conversation, ConversationOpen):
            return call, stall_effects
        return call, (
            ReplyDelegationEffect(
                delegation_id=event.delegation_id,
                thinking=_delegation_thinking(call.request),
                commentary=next_step_line(
                    call.request, call.conversation.transcript
                ),
            ),
        ) + stall_effects

    if isinstance(event, LiveToolCallEvent):
        return _handle_tool_call(call, event, settings)

    if isinstance(event, LiveResponseEvent):
        if not isinstance(call.live, LiveActive):
            return call, ()
        updates: dict[str, Any] = {}
        if event.response_kind == "created" and event.response_id:
            updates["live"] = call.live.model_copy(
                update={"response_id": event.response_id}
            )
        updates, stall_effects = _bump_stall(call, settings, updates)
        call = call.model_copy(update=updates) if updates else call
        if event.response_kind == "failed" and isinstance(
            call.hangup, HangupIdle
        ):
            return call, stall_effects + (
                SteerLiveEffect(
                    instructions=(
                        "The backend step failed; continue the conversation "
                        "from what they said."
                    ),
                    event_id="response-failed",
                ),
            )
        return call, stall_effects

    if isinstance(event, LiveNoteEvent):
        return call, ()

    if isinstance(
        event, (LiveInputTranscriptEvent, LiveOutputTranscriptEvent)
    ):
        if not isinstance(call.conversation, ConversationOpen):
            return call, ()
        speaker = (
            Speaker.BUSINESS
            if isinstance(event, LiveInputTranscriptEvent)
            else Speaker.AGENT
        )
        transcript = call.conversation.transcript.append_delta(
            speaker, event.delta
        )
        call = call.model_copy(
            update={"conversation": ConversationOpen(transcript=transcript)}
        )
        updates: dict[str, Any] = {}
        effects: tuple[Effect, ...] = ()
        if _should_enter_hangup_drain(call, transcript):
            updates, effects = _enter_hangup_drain(call, settings, updates)
        else:
            updates, stall_effects = _bump_stall(call, settings, updates)
            effects += stall_effects
        if not updates:
            return call, effects
        return call.model_copy(update=updates), effects

    if isinstance(event, LiveClosedEvent):
        updates = {"live": LiveClosed()}
        if not isinstance(call.hangup, HangupDraining):
            return call.model_copy(update=updates), ()
        updates["hangup"] = HangupMarked(via=call.hangup.via)
        return call.model_copy(update=updates), (MarkEffect(name=HANGUP_MARK),)

    if isinstance(event, LiveErrorEvent):
        return _fail(call, event.message, settings)

    if isinstance(event, TimeoutEvent):
        return _fail(call, timeout_failure_reason(call, event), settings)

    if isinstance(event, RuntimeFailureEvent):
        return _fail(call, event.reason, settings)

    return call, ()
