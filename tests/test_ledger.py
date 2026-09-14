import sqlite3
from pathlib import Path

import pytest

from call_agent.config import Settings
from call_agent.domain import UserId
from call_agent.ledger import Ledger, NeonLedger, _psycopg_connect, open_ledger
from call_agent.persistence import PersistenceConfigError
from call_agent.telnyx import StubTelnyxHttp


class _SqlitePg:
    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql.replace("%s", "?"), params)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def test_provision_is_idempotent_and_users_get_distinct_dids(
    tmp_path: Path,
) -> None:
    ledger = Ledger(tmp_path / "call-agent.sqlite3")
    telnyx = StubTelnyxHttp()

    first = ledger.provision(
        UserId("first-user"), telnyx, "connection-1"
    )
    repeated = ledger.provision(
        UserId("first-user"), telnyx, "connection-1"
    )
    second = ledger.provision(
        UserId("second-user"), telnyx, "connection-1"
    )
    records = ledger.list()

    assert first.did == "+15555550100"
    assert repeated.model_dump(mode="json") == first.model_dump(mode="json")
    assert second.did == "+15555550101"
    assert [
        {
            "user_id": str(record.user_id),
            "did": str(record.did),
            "status": record.status,
        }
        for record in records
    ] == [
        {
            "user_id": "first-user",
            "did": "+15555550100",
            "status": "active",
        },
        {
            "user_id": "second-user",
            "did": "+15555550101",
            "status": "active",
        },
    ]


def test_neon_ledger_roundtrip_through_sqlite_bridge(tmp_path: Path) -> None:
    path = tmp_path / "neon.sqlite3"
    ledger = NeonLedger(
        "postgresql://unused",
        connect=lambda: _SqlitePg(path),
    )
    telnyx = StubTelnyxHttp()

    first = ledger.provision(UserId("first-user"), telnyx, "connection-1")
    repeated = ledger.provision(UserId("first-user"), telnyx, "connection-1")

    assert first.did == "+15555550100"
    assert repeated.model_dump(mode="json") == first.model_dump(mode="json")
    assert ledger.get(UserId("first-user")) == first
    assert [record.user_id for record in ledger.list()] == ["first-user"]


def test_open_ledger_unset_returns_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    settings = Settings(
        _env_file=None,
        call_agent_db_path=tmp_path / "call-agent.sqlite3",
    )

    assert isinstance(open_ledger(settings), Ledger)


def test_psycopg_connect_fails_loud_when_driver_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "psycopg" or name.startswith("psycopg."):
            raise ImportError("no driver")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(PersistenceConfigError, match="psycopg is not installed"):
        _psycopg_connect("postgresql://user:pass@ep-test/neondb")()


def test_psycopg_binary_is_on_the_image_path() -> None:
    import psycopg
    from psycopg.rows import dict_row

    repo = Path(__file__).resolve().parents[1]
    pyproject = (repo / "pyproject.toml").read_text()
    dockerfile = (repo / "Dockerfile").read_text()

    assert '"psycopg[binary]>=3,<4"' in pyproject
    assert "pip install --no-cache-dir ." in dockerfile
    assert 'python -c "import psycopg"' in dockerfile
    assert callable(psycopg.connect)
    assert dict_row is not None
