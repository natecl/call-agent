from pathlib import Path
from types import SimpleNamespace

import pytest

from call_agent.config import Settings


@pytest.fixture(autouse=True)
def instant_trailing_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HANGUP_TRAILING_SILENCE_S", "0")


@pytest.fixture
def workspace(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        db_path=tmp_path / "call-agent.sqlite3",
        artifacts_dir=tmp_path / "artifacts",
    )


@pytest.fixture
def settings(workspace: SimpleNamespace) -> Settings:
    return Settings(
        _env_file=None,
        call_agent_db_path=workspace.db_path,
        call_agent_artifacts_dir=workspace.artifacts_dir,
    )
