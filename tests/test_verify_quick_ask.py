import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from call_agent.domain import E164
from call_agent.gpt_live import StubScenario
from call_agent.verify import VerifyInput, VerifyWorkspace, verify_quick_ask


def _workspace(workspace: SimpleNamespace) -> VerifyWorkspace:
    return VerifyWorkspace(
        db_path=workspace.db_path,
        artifacts_dir=workspace.artifacts_dir,
    )


def test_verify_quick_ask_proves_stub_call_from_artifacts() -> None:
    report = verify_quick_ask()

    assert [check.name for check in report.checks] == [
        "did_in_ledger",
        "dial_payload",
        "disclosure_first",
        "clean_hangup",
        "result_artifact",
    ]
    assert report.passed is True
    assert report.call_control_id == "stub-call-0001"

    result_detail = report.checks[-1].detail
    artifacts_dir = Path(
        result_detail.split("artifacts_dir=", 1)[1].split(" ", 1)[0]
    )
    result = json.loads(
        (
            artifacts_dir
            / "stub-call-0001"
            / "result.json"
        ).read_text()
    )

    assert result["status"] == "answered"
    assert result["answer_text"] == (
        "We're open Monday through Friday, 9 AM to 5 PM."
    )
    assert (artifacts_dir / result["transcript_ref"]).is_file()
    assert (artifacts_dir / result["audio_ref"]).is_file()


def test_verify_fails_when_stub_skips_spoken_disclosure(
    workspace: SimpleNamespace,
) -> None:
    report = verify_quick_ask(
        workspace=_workspace(workspace),
        scenario=StubScenario(skip_disclosure=True),
    )

    assert [check.passed for check in report.checks] == [
        True,
        True,
        False,
        True,
        True,
    ]
    assert report.passed is False
    assert "first_agent_turn_discloses=False" in report.checks[2].detail


def test_verify_fails_when_from_did_is_not_the_users(
    workspace: SimpleNamespace,
) -> None:
    report = verify_quick_ask(
        VerifyInput(from_did=E164("+15555559999")),
        workspace=_workspace(workspace),
    )

    assert [check.passed for check in report.checks] == [
        False,
        False,
        True,
        True,
        True,
    ]
    assert report.passed is False
    assert (
        "ledger has +15555550100, expected from_did +15555559999"
        in report.checks[0].detail
    )


def test_verify_fails_when_dial_body_is_not_bidirectional(
    workspace: SimpleNamespace,
) -> None:
    report = verify_quick_ask(
        workspace=_workspace(workspace),
        scenario=StubScenario(
            dial_override={"stream_bidirectional_mode": "mp3"}
        ),
    )

    assert [check.passed for check in report.checks] == [
        True,
        False,
        True,
        True,
        True,
    ]
    assert report.passed is False
    assert "mode=mp3/self" in report.checks[1].detail


def test_verify_fails_when_far_party_hangs_up_before_answering(
    workspace: SimpleNamespace,
) -> None:
    report = verify_quick_ask(
        workspace=_workspace(workspace),
        scenario=StubScenario(far_party_hangs_up=True),
    )

    assert report.passed is False
    assert report.checks[3].passed is False
    assert "status=failed" in report.checks[3].detail
    assert (
        "reason: far party hung up before an answer"
        in report.checks[3].detail
    )


def test_verify_fails_clean_hangup_when_playback_never_drains(
    workspace: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HANGUP_DRAIN_TIMEOUT_S", "0")

    report = verify_quick_ask(
        workspace=_workspace(workspace),
        scenario=StubScenario(playback_never_drains=True),
    )
    run_dir = workspace.artifacts_dir / report.call_control_id
    kinds = [row.get("kind") for row in _rows(run_dir)]
    call_report = json.loads((run_dir / "call_report.json").read_text())

    assert [check.passed for check in report.checks] == [
        True,
        True,
        True,
        False,
        True,
    ]
    assert report.passed is False
    assert "status=answered" in report.checks[3].detail
    assert "marks_sent=1" in report.checks[3].detail
    assert "drain_timed_out=True" in report.checks[3].detail
    assert "hangup_path=drain_timeout" in report.checks[3].detail
    assert "hangup_drain_timeout" in kinds
    assert "hangup_trail_elapsed" not in kinds
    assert call_report["hangup"]["path"] == "drain_timeout"
    assert call_report["timing_ms"]["final_mark_echoed"] is None


def test_verify_passes_with_one_final_mark_when_goodbye_audio_storms(
    workspace: SimpleNamespace,
) -> None:
    report = verify_quick_ask(
        workspace=_workspace(workspace),
        scenario=StubScenario(audio_chunks_after_closing_line=98),
    )
    run_dir = workspace.artifacts_dir / report.call_control_id
    rows = _rows(run_dir)
    marks = [row["name"] for row in rows if row.get("effect") == "mark"]
    echoes = [row["name"] for row in rows if row.get("kind") == "media_mark"]
    verdict_row = next(
        row
        for row in rows
        if row.get("transitions", {}).get("conversation")
        == "conversation_terminal"
    )
    call_report = json.loads((run_dir / "call_report.json").read_text())
    timing = call_report["timing_ms"]

    assert report.passed is True
    assert marks == ["hangup"]
    assert echoes == ["hangup"]
    assert verdict_row["transitions"] == {
        "conversation": "conversation_terminal",
        "hangup": "hangup_draining",
        "live": "live_closing",
    }
    assert call_report["status"] == "answered"
    assert call_report["call_control_id"] == "stub-call-0001"
    assert call_report["hangup"] == {
        "path": "trail",
        "cause": "normal_clearing",
        "source": "caller",
        "final_mark": "hangup",
        "trailing_silence_s": 0.0,
        "drain_timeout_s": 10.0,
        "confirmed": True,
    }
    assert call_report["counts"] == {
        "events": 121,
        "effects": 115,
        "marks_sent": 1,
        "mark_echoes": 1,
        "stale_mark_echoes": 0,
        "output_audio_chunks": 101,
        "output_audio_chunks_after_verdict": 99,
        "inbound_media_chunks": 3,
        "transcript_turns": 3,
    }
    assert call_report["refs"] == {
        "result": "stub-call-0001/result.json",
        "transcript": "stub-call-0001/transcript.json",
        "events": "stub-call-0001/events.jsonl",
        "inbound_audio": "stub-call-0001/audio/inbound.wav",
        "outbound_audio": "stub-call-0001/audio/outbound.wav",
    }
    assert (
        timing["dial_placed"]
        <= timing["answered"]
        <= timing["media_streaming"]
        <= timing["live_active"]
        <= timing["verdict"]
        <= timing["live_closed"]
        <= timing["final_mark_sent"]
        <= timing["final_mark_echoed"]
        <= timing["hangup_sent"]
        <= timing["call_ended"]
        <= call_report["duration_ms"]
    )


def _rows(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text().splitlines()
    ]


def test_verify_quick_ask_passes_when_telnyx_streams_l16_at_8000(
    workspace: SimpleNamespace,
) -> None:
    report = verify_quick_ask(
        workspace=_workspace(workspace),
        scenario=StubScenario(media_sample_rate=8000),
    )

    run_dir = workspace.artifacts_dir / report.call_control_id
    dial = json.loads((run_dir / "dial.json").read_text())
    session_start = json.loads(
        (run_dir / "session_start.json").read_text()
    )
    with wave.open(str(run_dir / "audio" / "inbound.wav")) as inbound:
        inbound_shape = (inbound.getframerate(), inbound.getnframes())
    with wave.open(str(run_dir / "audio" / "outbound.wav")) as outbound:
        outbound_shape = (outbound.getframerate(), outbound.getnframes())

    assert report.passed is True
    assert dial["stream_bidirectional_sampling_rate"] == 16000
    assert session_start["session"]["audio"]["format"] == {
        "type": "audio/pcm",
        "rate": 16000,
    }
    assert inbound_shape == (8000, 960)
    assert outbound_shape == (8000, 800)
