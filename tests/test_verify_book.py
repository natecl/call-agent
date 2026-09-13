import json
from pathlib import Path
from types import SimpleNamespace

from call_agent.domain import E164
from call_agent.gpt_live import StubScenario
from call_agent.verify import BookVerifyInput, VerifyWorkspace, verify_book


def _workspace(workspace: SimpleNamespace) -> VerifyWorkspace:
    return VerifyWorkspace(
        db_path=workspace.db_path,
        artifacts_dir=workspace.artifacts_dir,
    )


def test_verify_book_proves_stub_booking_from_artifacts() -> None:
    report = verify_book()

    assert [check.name for check in report.checks] == [
        "did_in_ledger",
        "dial_payload",
        "disclosure_first",
        "clean_hangup",
        "result_artifact",
        "multi_turn_booking",
        "keeps_talking",
        "session_delegation",
        "tool_loop",
        "app_owned_hangup",
    ]
    assert report.passed is True
    assert report.call_control_id == "stub-call-0001"

    result_detail = report.checks[4].detail
    artifacts_dir = Path(
        result_detail.split("artifacts_dir=", 1)[1].split(" ", 1)[0]
    )
    result = json.loads(
        (artifacts_dir / "stub-call-0001" / "result.json").read_text()
    )
    transcript = json.loads(
        (artifacts_dir / "stub-call-0001" / "transcript.json").read_text()
    )

    assert result["status"] == "booked"
    assert "booked" in result["confirmation_text"].casefold()
    assert (artifacts_dir / result["transcript_ref"]).is_file()
    assert (artifacts_dir / result["audio_ref"]).is_file()
    assert len(transcript["turns"]) >= 5
    assert any(
        turn["speaker"] == "business" and "Friday 7pm" in turn["text"]
        for turn in transcript["turns"]
    )


def test_verify_book_fails_when_stub_skips_spoken_disclosure(
    workspace: SimpleNamespace,
) -> None:
    report = verify_book(
        workspace=_workspace(workspace),
        scenario=StubScenario(skip_disclosure=True),
    )

    assert report.checks[2].passed is False
    assert report.passed is False
    assert "first_agent_turn_discloses=False" in report.checks[2].detail


def test_verify_book_fails_when_from_did_is_not_the_users(
    workspace: SimpleNamespace,
) -> None:
    report = verify_book(
        BookVerifyInput(from_did=E164("+15555559999")),
        workspace=_workspace(workspace),
    )

    assert report.checks[0].passed is False
    assert report.checks[1].passed is False
    assert report.passed is False
    assert (
        "ledger has +15555550100, expected from_did +15555559999"
        in report.checks[0].detail
    )


def test_verify_book_fails_one_shot_theater(
    workspace: SimpleNamespace,
) -> None:
    report = verify_book(
        workspace=_workspace(workspace),
        scenario=StubScenario(
            one_shot_booking=True,
            canned_answer="Yes, we can do that",
        ),
    )

    assert report.passed is False
    assert report.checks[5].name == "multi_turn_booking"
    assert report.checks[5].passed is False
    assert "agent_turns=" in report.checks[5].detail
    run_dir = workspace.artifacts_dir / report.call_control_id
    result = json.loads((run_dir / "result.json").read_text())
    call_report = json.loads((run_dir / "call_report.json").read_text())
    hangup = json.loads((run_dir / "hangup.json").read_text())
    assert result["status"] == "failed"
    assert call_report["hangup"]["path"] == "trail"
    assert hangup["call_control_id"] == report.call_control_id
    assert call_report["counts"]["marks_sent"] == 1
    transcript = json.loads((run_dir / "transcript.json").read_text())
    agent_text = " ".join(
        turn["text"]
        for turn in transcript["turns"]
        if turn["speaker"] == "agent"
    )
    business_text = " ".join(
        turn["text"]
        for turn in transcript["turns"]
        if turn["speaker"] == "business"
    )
    assert "thanks, that's all i needed. goodbye." in agent_text.casefold()
    assert "yes, we can do that" in business_text.casefold()
    assert "hangup_path=trail" in report.checks[3].detail


def test_verify_book_fails_when_far_party_hangs_up(
    workspace: SimpleNamespace,
) -> None:
    report = verify_book(
        workspace=_workspace(workspace),
        scenario=StubScenario(far_party_hangs_up=True),
    )

    assert report.passed is False
    assert report.checks[3].passed is False
    assert "status=failed" in report.checks[3].detail


def test_verify_book_passes_on_clear_decline(
    workspace: SimpleNamespace,
) -> None:
    report = verify_book(
        workspace=_workspace(workspace),
        scenario=StubScenario(decline_booking=True),
    )

    assert report.passed is True
    run_dir = workspace.artifacts_dir / report.call_control_id
    result = json.loads((run_dir / "result.json").read_text())
    assert result["status"] == "declined"
    assert result["answer_text"] is None
    assert report.checks[6].name == "keeps_talking"
    assert report.checks[6].passed is True


def test_verify_book_fails_never_resumes_keeps_talking(
    workspace: SimpleNamespace,
) -> None:
    report = verify_book(
        workspace=_workspace(workspace),
        scenario=StubScenario(never_resumes=True),
    )

    assert report.passed is False
    keeps = report.checks[6]
    assert keeps.name == "keeps_talking"
    assert keeps.passed is False
    assert "hangup_path=stall" in keeps.detail
    run_dir = workspace.artifacts_dir / report.call_control_id
    call_report = json.loads((run_dir / "call_report.json").read_text())
    assert call_report["hangup"]["path"] == "stall"


def test_verify_book_fails_media_drop_clean_hangup_and_keeps_result(
    workspace: SimpleNamespace,
) -> None:
    report = verify_book(
        workspace=_workspace(workspace),
        scenario=StubScenario(media_drops_before_hangup=True),
    )

    assert report.passed is False
    assert report.checks[3].name == "clean_hangup"
    assert report.checks[3].passed is False
    assert "hangup_path=far_party" in report.checks[3].detail
    assert report.checks[4].name == "result_artifact"
    assert report.checks[4].passed is True
    run_dir = workspace.artifacts_dir / report.call_control_id
    assert (run_dir / "result.json").is_file()
    assert (run_dir / "call_report.json").is_file()
