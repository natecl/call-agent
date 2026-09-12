from pathlib import Path

import pytest

from call_agent.config import Settings
from call_agent.persistence import (
    LocalArtifactConfig,
    NeonLedgerConfig,
    PersistenceConfigError,
    R2_REQUIRED,
    R2ArtifactConfig,
    SqliteLedgerConfig,
    parse_artifact_config,
    parse_ledger_config,
)


def test_unset_env_selects_sqlite_and_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    for name in (*R2_REQUIRED, "R2_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    db = tmp_path / "call-agent.sqlite3"
    artifacts = tmp_path / "artifacts"
    settings = Settings(
        _env_file=None,
        call_agent_db_path=db,
        call_agent_artifacts_dir=artifacts,
    )

    ledger = settings.ledger_config()
    artifacts_cfg = settings.artifact_config()

    assert ledger == SqliteLedgerConfig(path=db)
    assert artifacts_cfg == LocalArtifactConfig(root=artifacts)


def test_neon_url_selects_neon_ledger(tmp_path: Path) -> None:
    config = parse_ledger_config(
        "postgresql://user:pass@ep-test/neondb",
        tmp_path / "unused.sqlite3",
    )

    assert config == NeonLedgerConfig(
        database_url="postgresql://user:pass@ep-test/neondb"
    )


def test_blank_neon_url_stays_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "call-agent.sqlite3"

    assert parse_ledger_config("   ", path) == SqliteLedgerConfig(path=path)
    assert parse_ledger_config(None, path) == SqliteLedgerConfig(path=path)


def test_neon_url_without_scheme_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(PersistenceConfigError, match="postgresql://"):
        parse_ledger_config("not-a-url", tmp_path / "db")


def test_partial_r2_fails_loud_and_names_missing(tmp_path: Path) -> None:
    with pytest.raises(PersistenceConfigError, match="missing R2_BUCKET") as error:
        parse_artifact_config(
            "acct",
            "key",
            "secret",
            None,
            tmp_path / "artifacts",
        )

    assert "R2_ACCOUNT_ID" in str(error.value)
    assert "R2_ACCESS_KEY_ID" in str(error.value)


def test_complete_r2_derives_endpoint(tmp_path: Path) -> None:
    config = parse_artifact_config(
        "acct",
        "key",
        "secret",
        "calls",
        tmp_path / "artifacts",
    )

    assert config == R2ArtifactConfig(
        account_id="acct",
        access_key_id="key",
        secret_access_key="secret",
        bucket="calls",
        endpoint="https://acct.r2.cloudflarestorage.com",
    )


def test_r2_endpoint_override(tmp_path: Path) -> None:
    config = parse_artifact_config(
        "acct",
        "key",
        "secret",
        "calls",
        tmp_path / "artifacts",
        "https://custom.example/r2/",
    )

    assert isinstance(config, R2ArtifactConfig)
    assert config.endpoint == "https://custom.example/r2"
