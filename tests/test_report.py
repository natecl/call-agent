import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from call_agent.domain import E164, FirstName, Mode
from call_agent.report import (
    CallReport,
    Counts,
    HangupSummary,
    RedactedCallReport,
    Refs,
    TimingMs,
    mask_e164,
    mask_first_name,
    redact_call_report,
)
from call_agent.verify import VerifyWorkspace, verify_quick_ask


def make_call_report() -> CallReport:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    ended = datetime(2026, 1, 1, 0, 0, 8, tzinfo=UTC)
    return CallReport(
        call_control_id="stub-call-0001",
        run_id="run-1",
        mode=Mode.STUB,
        to=E164("+14155552671"),
        from_did=E164("+15555550100"),
        status="answered",
        answer_text="Nathan, we're open Monday through Friday, 9 AM to 5 PM.",
        reason=None,
        started_at=started,
        ended_at=ended,
        duration_ms=8000,
        timing_ms=TimingMs(),
        hangup=HangupSummary(
            path="trail",
            cause="normal_clearing",
            source="caller",
            final_mark="hangup",
            trailing_silence_s=0.0,
            drain_timeout_s=10.0,
        ),
        counts=Counts(
            events=121,
            effects=113,
            marks_sent=1,
            mark_echoes=1,
            stale_mark_echoes=0,
            output_audio_chunks=101,
            output_audio_chunks_after_verdict=99,
            inbound_media_chunks=3,
            transcript_turns=3,
        ),
        refs=Refs(
            result="stub-call-0001/result.json",
            transcript="stub-call-0001/transcript.json",
            events="stub-call-0001/events.jsonl",
            inbound_audio="stub-call-0001/audio/inbound.wav",
            outbound_audio="stub-call-0001/audio/outbound.wav",
        ),
    )


def test_mask_e164_returns_phone_token() -> None:
    masked = mask_e164(E164("+14155552671"))

    assert masked == "[phone]"
    assert "+14155552671" not in masked


def test_mask_first_name_returns_name_token() -> None:
    masked = mask_first_name(FirstName("Nathan"))

    assert masked == "[name]"
    assert "Nathan" not in masked


def test_mask_first_name_none_stays_none() -> None:
    assert mask_first_name(None) is None


def test_redact_call_report_keeps_allowlist_and_drops_identity() -> None:
    full = make_call_report()
    redacted = redact_call_report(
        full, caller_first_name=FirstName("Nathan")
    )
    dump = redacted.model_dump(mode="json")
    dump_text = json.dumps(dump)

    assert redacted.to == "[phone]"
    assert redacted.from_did == "[phone]"
    assert redacted.caller_first_name == "[name]"
    assert redacted.mode is Mode.STUB
    assert redacted.status == "answered"
    assert redacted.started_at == full.started_at
    assert redacted.ended_at == full.ended_at
    assert redacted.duration_ms == 8000
    assert redacted.hangup.path == "trail"
    assert redacted.hangup.trailing_silence_s == 0.0
    assert redacted.hangup.drain_timeout_s == 10.0
    assert set(dump) == {
        "mode",
        "to",
        "from_did",
        "caller_first_name",
        "status",
        "started_at",
        "ended_at",
        "duration_ms",
        "timing_ms",
        "hangup",
        "counts",
    }
    assert set(dump["hangup"]) == {
        "path",
        "trailing_silence_s",
        "drain_timeout_s",
    }
    assert "+14155552671" not in dump_text
    assert "+15555550100" not in dump_text
    assert "Nathan" not in dump_text


def test_redacted_call_report_rejects_full_report_dump() -> None:
    full = make_call_report()

    with pytest.raises(ValidationError):
        RedactedCallReport.model_validate(full.model_dump(mode="json"))


def test_redacted_call_report_rejects_raw_e164() -> None:
    dump = redact_call_report(
        make_call_report(), caller_first_name=FirstName("Nathan")
    ).model_dump(mode="json")
    dump["to"] = "+14155552671"

    with pytest.raises(ValidationError):
        RedactedCallReport.model_validate(dump)


def test_verify_quick_ask_writes_redacted_sibling(
    workspace: SimpleNamespace,
) -> None:
    report = verify_quick_ask(
        workspace=VerifyWorkspace(
            db_path=workspace.db_path,
            artifacts_dir=workspace.artifacts_dir,
        )
    )
    run_dir = workspace.artifacts_dir / report.call_control_id
    full = CallReport.model_validate_json(
        (run_dir / "call_report.json").read_text()
    )
    sibling = RedactedCallReport.model_validate_json(
        (run_dir / "call_report.redacted.json").read_text()
    )
    transcript = (run_dir / "transcript.json").read_text()

    assert report.passed is True
    assert full.to == "+14155552671"
    assert full.from_did == "+15555550100"
    assert full.hangup.confirmed is True
    assert "confirmed" not in sibling.hangup.model_dump()
    assert (run_dir / "call_report.redacted.json").is_file()
    assert "Nathan" in transcript
    assert (
        redact_call_report(
            full, caller_first_name=FirstName("Nathan")
        ).model_dump(mode="json")
        == sibling.model_dump(mode="json")
    )
    assert (run_dir / "audio" / "inbound.wav").is_file()
    assert (run_dir / "audio" / "outbound.wav").is_file()
