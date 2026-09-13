from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypeAlias

from .domain import (
    E164,
    HANGUP_MARK,
    Call,
    FirstName,
    FrozenModel,
    HangupConfirmed,
    HangupRequested,
    HangupUnconfirmed,
    HangupVia,
    Mode,
    CallResult,
    ReduceSettings,
    TelnyxEnded,
)

JournalRow: TypeAlias = dict[str, Any]

HangupPath: TypeAlias = HangupVia | Literal["far_party"]


class TimingMs(FrozenModel):
    dial_placed: int | None = None
    ringing: int | None = None
    answered: int | None = None
    media_streaming: int | None = None
    live_active: int | None = None
    verdict: int | None = None
    live_closed: int | None = None
    final_mark_sent: int | None = None
    final_mark_echoed: int | None = None
    hangup_sent: int | None = None
    call_ended: int | None = None


class HangupSummary(FrozenModel):
    path: HangupPath | None
    cause: str | None
    source: str | None
    final_mark: str
    trailing_silence_s: float
    drain_timeout_s: float
    confirmed: bool = False


class Counts(FrozenModel):
    events: int
    effects: int
    marks_sent: int
    mark_echoes: int
    stale_mark_echoes: int
    output_audio_chunks: int
    output_audio_chunks_after_verdict: int
    inbound_media_chunks: int
    transcript_turns: int


class Refs(FrozenModel):
    result: str
    transcript: str
    events: str
    inbound_audio: str
    outbound_audio: str


class CallReport(FrozenModel):
    call_control_id: str
    run_id: str
    mode: Mode
    to: E164
    from_did: E164
    status: Literal["answered", "booked", "declined", "no_answer", "failed"]
    answer_text: str | None
    reason: str | None
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    timing_ms: TimingMs
    hangup: HangupSummary
    counts: Counts
    refs: Refs


MaskedE164: TypeAlias = Literal["[phone]"]
MaskedFirstName: TypeAlias = Literal["[name]"]


class RedactedHangupSummary(FrozenModel):
    path: HangupPath | None
    trailing_silence_s: float
    drain_timeout_s: float


class RedactedCallReport(FrozenModel):
    mode: Mode
    to: MaskedE164
    from_did: MaskedE164
    caller_first_name: MaskedFirstName | None
    status: Literal["answered", "booked", "declined", "no_answer", "failed"]
    started_at: datetime
    ended_at: datetime
    duration_ms: int
    timing_ms: TimingMs
    hangup: RedactedHangupSummary
    counts: Counts


def mask_e164(value: E164) -> MaskedE164:
    return "[phone]"


def mask_first_name(value: FirstName | None) -> MaskedFirstName | None:
    if value is None:
        return None
    return "[name]"


def redact_call_report(
    report: CallReport, *, caller_first_name: FirstName | None
) -> RedactedCallReport:
    return RedactedCallReport(
        mode=report.mode,
        to=mask_e164(report.to),
        from_did=mask_e164(report.from_did),
        caller_first_name=mask_first_name(caller_first_name),
        status=report.status,
        started_at=report.started_at,
        ended_at=report.ended_at,
        duration_ms=report.duration_ms,
        timing_ms=report.timing_ms,
        hangup=RedactedHangupSummary(
            path=report.hangup.path,
            trailing_silence_s=report.hangup.trailing_silence_s,
            drain_timeout_s=report.hangup.drain_timeout_s,
        ),
        counts=report.counts,
    )


_TIMING_TRANSITIONS: dict[str, tuple[str, str]] = {
    "dial_placed": ("dial", "dial_placed"),
    "ringing": ("telnyx", "telnyx_ringing"),
    "answered": ("telnyx", "telnyx_answered"),
    "media_streaming": ("media", "media_streaming"),
    "live_active": ("live", "live_active"),
    "verdict": ("conversation", "conversation_terminal"),
    "live_closed": ("live", "live_closed"),
    "final_mark_sent": ("hangup", "hangup_marked"),
    "hangup_sent": ("hangup", "hangup_requested"),
    "call_ended": ("telnyx", "telnyx_ended"),
}


def _first_transition(
    rows: list[JournalRow], track: str, kind: str
) -> JournalRow | None:
    return next(
        (
            row
            for row in rows
            if row.get("transitions", {}).get(track) == kind
        ),
        None,
    )


def _hangup_path(call: Call) -> HangupPath | None:
    if isinstance(
        call.hangup, (HangupRequested, HangupConfirmed, HangupUnconfirmed)
    ):
        return call.hangup.via
    if isinstance(call.telnyx, TelnyxEnded):
        return "far_party"
    return None


def build_call_report(
    call: Call,
    result: CallResult,
    rows: list[JournalRow],
    started_at: datetime,
    ended_at: datetime,
    settings: ReduceSettings,
    refs: Refs,
) -> CallReport:
    events = [row for row in rows if "kind" in row]
    effects = [row for row in rows if "effect" in row]
    verdict_row = _first_transition(
        rows, "conversation", "conversation_terminal"
    )
    verdict_seq = verdict_row["seq"] if verdict_row else None
    output_audio = [row for row in events if row["kind"] == "live_output_audio"]
    mark_echoes = [row for row in events if row["kind"] == "media_mark"]
    final_echo = next(
        (row for row in mark_echoes if row["name"] == HANGUP_MARK), None
    )
    timing = {
        name: row["t_ms"]
        for name, (track, kind) in _TIMING_TRANSITIONS.items()
        if (row := _first_transition(rows, track, kind)) is not None
    }
    if final_echo is not None:
        timing["final_mark_echoed"] = final_echo["t_ms"]
    ended = call.telnyx if isinstance(call.telnyx, TelnyxEnded) else None
    return CallReport(
        call_control_id=result.call_control_id,
        run_id=str(call.run_id),
        mode=call.request.mode,
        to=call.request.to,
        from_did=call.from_did,
        status=result.status,
        answer_text=(
            result.confirmation_text
            if result.status == "booked"
            else result.answer_text
        ),
        reason=getattr(result, "reason", None),
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=round((ended_at - started_at).total_seconds() * 1000),
        timing_ms=TimingMs(**timing),
        hangup=HangupSummary(
            path=_hangup_path(call),
            cause=ended.cause if ended else None,
            source=ended.source if ended else None,
            final_mark=HANGUP_MARK,
            trailing_silence_s=settings.hangup_trailing_silence_s,
            drain_timeout_s=settings.hangup_drain_timeout_s,
            confirmed=isinstance(call.hangup, HangupConfirmed),
        ),
        counts=Counts(
            events=len(events),
            effects=len(effects),
            marks_sent=sum(1 for row in effects if row["effect"] == "mark"),
            mark_echoes=len(mark_echoes),
            stale_mark_echoes=sum(
                1 for row in mark_echoes if row["name"] != HANGUP_MARK
            ),
            output_audio_chunks=len(output_audio),
            output_audio_chunks_after_verdict=sum(
                1
                for row in output_audio
                if verdict_seq is not None and row["seq"] > verdict_seq
            ),
            inbound_media_chunks=sum(
                1 for row in events if row["kind"] == "media_chunk"
            ),
            transcript_turns=len(call.conversation.transcript.turns),
        ),
        refs=refs,
    )
