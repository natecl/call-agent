import json
from pathlib import Path
from types import SimpleNamespace

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
