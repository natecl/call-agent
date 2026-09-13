from pathlib import Path

from typer.testing import CliRunner

from call_agent.cli import app
from call_agent.verify_accounts import verify_accounts


runner = CliRunner()


def test_verify_accounts_passes_five_checks() -> None:
    report = verify_accounts()

    assert report.passed
    assert [check.name for check in report.checks] == [
        "cli_roundtrip",
        "omit_preserve",
        "sync_hmac",
        "dashboard_get",
        "no_name_column",
    ]
    assert all(check.passed for check in report.checks)


def test_cli_verify_accounts_exits_zero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-accounts"],
        env={
            "CALL_AGENT_DB_PATH": str(tmp_path / "call-agent.sqlite3"),
            "CALL_AGENT_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
            "QUICK_ASK_LIVE": "0",
            "NO_COLOR": "1",
        },
    )

    assert result.exit_code == 0
    assert "5/5 passed" in result.stdout
    assert result.stdout.count("[PASS]") == 5
    assert "[PASS] cli_roundtrip" in result.stdout
    assert "[PASS] omit_preserve" in result.stdout
    assert "[PASS] sync_hmac" in result.stdout
    assert "[PASS] dashboard_get" in result.stdout
    assert "[PASS] no_name_column" in result.stdout
