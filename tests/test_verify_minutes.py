from pathlib import Path

from typer.testing import CliRunner

from call_agent.cli import app
from call_agent.verify_minutes import verify_minutes


runner = CliRunner()


def test_verify_minutes_passes_six_checks() -> None:
    report = verify_minutes()

    assert report.passed
    assert [check.name for check in report.checks] == [
        "cap_blocks",
        "included_allows",
        "prepaid_unlocks",
        "pack_cap",
        "price_locked",
        "stripe_test",
    ]
    assert all(check.passed for check in report.checks)


def test_cli_verify_minutes_exits_zero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-minutes"],
        env={
            "CALL_AGENT_DB_PATH": str(tmp_path / "call-agent.sqlite3"),
            "CALL_AGENT_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
            "QUICK_ASK_LIVE": "0",
            "NO_COLOR": "1",
        },
    )

    assert result.exit_code == 0, result.output
    assert "6/6 passed" in result.stdout
    assert result.stdout.count("[PASS]") == 6
    assert "[PASS] cap_blocks" in result.stdout
    assert "[PASS] included_allows" in result.stdout
    assert "[PASS] prepaid_unlocks" in result.stdout
    assert "[PASS] pack_cap" in result.stdout
    assert "[PASS] price_locked" in result.stdout
    assert "[PASS] stripe_test" in result.stdout
