import json
from pathlib import Path
from types import SimpleNamespace

from call_agent.gpt_live import StubScenario
from call_agent.verify import VerifyWorkspace, verify_quick_ask


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
        workspace=VerifyWorkspace(
            db_path=workspace.db_path,
            artifacts_dir=workspace.artifacts_dir,
        ),
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
