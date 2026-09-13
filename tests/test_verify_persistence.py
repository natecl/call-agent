from pathlib import Path

import pytest
from typer.testing import CliRunner

from call_agent.cli import app
from call_agent.config import Settings
from call_agent.persistence import PersistenceConfigError
from call_agent.verify_persistence import verify_persistence
from call_agent.verify import VerifyInput, verify_quick_ask


runner = CliRunner()


def _clear_cloud_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    for name in (
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET",
        "R2_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_verify_persistence_unset_passes_with_sqlite_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_cloud_env(monkeypatch)
    settings = Settings(
        _env_file=None,
        call_agent_db_path=tmp_path / "call-agent.sqlite3",
        call_agent_artifacts_dir=tmp_path / "artifacts",
    )

    report = verify_persistence(settings)

    assert report.passed
    assert report.ledger_kind == "sqlite"
    assert report.artifact_kind == "local"
    assert [check.name for check in report.checks] == [
        "config",
        "ledger_roundtrip",
        "account_roundtrip",
        "artifact_put_get",
    ]
    assert all(check.passed for check in report.checks)


def test_verify_persistence_partial_r2_fails_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_cloud_env(monkeypatch)
    settings = Settings(
        _env_file=None,
        call_agent_db_path=tmp_path / "call-agent.sqlite3",
        call_agent_artifacts_dir=tmp_path / "artifacts",
        r2_account_id="acct",
        r2_access_key_id="key",
        r2_secret_access_key="secret",
    )

    with pytest.raises(PersistenceConfigError, match="missing R2_BUCKET"):
        verify_persistence(settings)


def test_verify_persistence_complete_cloud_config_still_stubs_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_cloud_env(monkeypatch)
    settings = Settings(
        _env_file=None,
        call_agent_db_path=tmp_path / "call-agent.sqlite3",
        call_agent_artifacts_dir=tmp_path / "artifacts",
        neon_database_url="postgresql://user:pass@ep-test/neondb",
        r2_account_id="acct",
        r2_access_key_id="key",
        r2_secret_access_key="secret",
        r2_bucket="calls",
    )

    report = verify_persistence(settings)

    assert report.passed
    assert report.ledger_kind == "neon"
    assert report.artifact_kind == "r2"
    assert "stubs" in report.checks[0].detail


def test_verify_quick_ask_stays_green_on_sqlite_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_cloud_env(monkeypatch)
    from call_agent.verify import VerifyWorkspace

    report = verify_quick_ask(
        VerifyInput(),
        VerifyWorkspace(
            db_path=tmp_path / "call-agent.sqlite3",
            artifacts_dir=tmp_path / "artifacts",
        ),
    )

    assert report.passed
    assert [check.name for check in report.checks] == [
        "did_in_ledger",
        "dial_payload",
        "disclosure_first",
        "clean_hangup",
        "result_artifact",
        "session_delegation",
        "tool_loop",
        "app_owned_hangup",
    ]


def test_cli_verify_persistence_unset_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_cloud_env(monkeypatch)
    result = runner.invoke(
        app,
        ["verify-persistence"],
        env={
            "CALL_AGENT_DB_PATH": str(tmp_path / "call-agent.sqlite3"),
            "CALL_AGENT_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
            "QUICK_ASK_LIVE": "0",
        },
    )

    assert result.exit_code == 0
    assert "4/4 passed" in result.stdout
    assert result.stdout.count("[PASS]") == 4
    assert "sqlite ledger + local artifacts" in result.stdout


def test_cli_verify_persistence_partial_r2_exits_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_cloud_env(monkeypatch)
    result = runner.invoke(
        app,
        ["verify-persistence"],
        env={
            "CALL_AGENT_DB_PATH": str(tmp_path / "call-agent.sqlite3"),
            "CALL_AGENT_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
            "QUICK_ASK_LIVE": "0",
            "R2_ACCOUNT_ID": "acct",
        },
    )

    assert result.exit_code == 2
    assert "R2 is misconfigured" in result.output
    assert "R2_BUCKET" in result.output
