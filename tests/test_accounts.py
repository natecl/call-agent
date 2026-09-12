import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from call_agent.accounts import (
    ACCOUNT_SYNC_SIGNATURE_HEADER,
    AccountConflict,
    AccountWrite,
    Accounts,
    NeonAccounts,
    generate_user_id,
    open_accounts,
    sign_account_sync,
)
from call_agent.cli import app
from call_agent.config import Settings
from call_agent.domain import E164, UserId
from call_agent.server import app as fastapi_app
from tests.test_ledger import _SqlitePg


runner = CliRunner()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        call_agent_db_path=tmp_path / "ledger.sqlite3",
        call_agent_artifacts_dir=tmp_path / "artifacts",
    )


def test_upsert_get_by_phone_and_list(tmp_path: Path) -> None:
    store = Accounts(tmp_path / "ledger.sqlite3")
    result = store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            user_id=UserId("acctnathan"),
            minutes_included=30,
            retain_call_artifacts=True,
            prefs={"full_name": "Nathan Tester"},
        )
    )

    fetched = store.get_by_phone(E164("+14155552671"))
    listed = store.list()

    assert result.created is True
    assert fetched is not None
    assert fetched.user_id == UserId("acctnathan")
    assert fetched.first_name == "Nathan"
    assert fetched.minutes_included == 30
    assert fetched.retain_call_artifacts is True
    assert fetched.prefs == {"full_name": "Nathan Tester"}
    assert listed == [fetched]
    assert store.get(UserId("acctnathan")) == fetched


def test_upsert_is_idempotent_on_phone(tmp_path: Path) -> None:
    store = Accounts(tmp_path / "ledger.sqlite3")
    first = store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            user_id=UserId("acctnathan"),
        )
    )
    second = store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            stripe_customer_id="cus_123",
        )
    )

    assert first.record.user_id == second.record.user_id
    assert second.created is False
    assert second.record.stripe_customer_id == "cus_123"


def test_older_stripe_event_is_skipped(tmp_path: Path) -> None:
    store = Accounts(tmp_path / "ledger.sqlite3")
    store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            stripe_customer_id="cus_123",
            stripe_event_created="2026-09-12T12:00:00+00:00",
        )
    )
    skipped = store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Old",
            stripe_customer_id="cus_123",
            stripe_event_created="2026-09-12T11:00:00+00:00",
        )
    )

    assert skipped.skipped is True
    assert skipped.record.first_name == "Nathan"


def test_phone_collision_across_stripe_customers_fails_loud(
    tmp_path: Path,
) -> None:
    store = Accounts(tmp_path / "ledger.sqlite3")
    store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            stripe_customer_id="cus_one",
        )
    )

    with pytest.raises(AccountConflict, match="already belongs"):
        store.upsert(
            AccountWrite(
                phone=E164("+14155552671"),
                first_name="Other",
                stripe_customer_id="cus_two",
            )
        )


def test_neon_accounts_roundtrip_through_sqlite_bridge(tmp_path: Path) -> None:
    path = tmp_path / "neon.sqlite3"
    store = NeonAccounts(
        "postgresql://unused",
        connect=lambda: _SqlitePg(path),
    )
    result = store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            user_id=UserId("acctnathan"),
        )
    )

    assert result.record.first_name == "Nathan"
    assert store.get_by_phone(E164("+14155552671")) == result.record


def test_open_accounts_unset_returns_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    settings = _settings(tmp_path)

    assert isinstance(open_accounts(settings), Accounts)


def test_generate_user_id_matches_user_id_token() -> None:
    generated = generate_user_id()
    assert generated == UserId(str(generated))


def test_cli_account_upsert_get_list(tmp_path: Path) -> None:
    env = {
        "CALL_AGENT_DB_PATH": str(tmp_path / "ledger.sqlite3"),
        "CALL_AGENT_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
        "QUICK_ASK_LIVE": "0",
        "NO_COLOR": "1",
        "TERM": "dumb",
        "COLUMNS": "120",
    }
    upserted = runner.invoke(
        app,
        [
            "account",
            "upsert",
            "--phone",
            "+14155552671",
            "--name",
            "Nathan",
            "--minutes",
            "30",
            "--retain",
            "--user",
            "acctnathan",
        ],
        env=env,
    )
    fetched = runner.invoke(
        app,
        ["account", "get", "--phone", "+14155552671"],
        env=env,
    )
    listed = runner.invoke(app, ["account", "list"], env=env)

    assert upserted.exit_code == 0
    payload = json.loads(upserted.stdout)
    assert payload["user_id"] == "acctnathan"
    assert payload["first_name"] == "Nathan"
    assert fetched.exit_code == 0
    assert json.loads(fetched.stdout)["user_id"] == "acctnathan"
    assert listed.exit_code == 0
    assert "acctnathan" in listed.stdout
    assert "+14155552671" in listed.stdout
    assert "Nathan" in listed.stdout


def test_cli_account_get_missing_exits_one(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["account", "get", "--phone", "+14155552671"],
        env={
            "CALL_AGENT_DB_PATH": str(tmp_path / "ledger.sqlite3"),
            "CALL_AGENT_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
            "QUICK_ASK_LIVE": "0",
        },
    )

    assert result.exit_code == 1
    assert "no account for +14155552671" in result.output


def test_accounts_sync_upserts_with_hmac(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = "sync-secret"
    monkeypatch.setenv("ACCOUNT_SYNC_SECRET", secret)
    monkeypatch.setenv("CALL_AGENT_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    monkeypatch.setenv("CALL_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    body = json.dumps(
        {
            "phone": "+14155552671",
            "first_name": "Nathan",
            "user_id": "acctnathan",
            "stripe_customer_id": "cus_123",
            "plan": "alpha",
            "minutes_included": 30,
            "retain_call_artifacts": True,
            "prefs": {"full_name": "Nathan Tester"},
            "stripe_event_created": "2026-09-12T12:00:00+00:00",
        }
    ).encode()
    with TestClient(fastapi_app) as client:
        denied = client.post("/accounts/sync", content=body)
        signed = client.post(
            "/accounts/sync",
            content=body,
            headers={
                ACCOUNT_SYNC_SIGNATURE_HEADER: sign_account_sync(secret, body)
            },
        )
        skipped = client.post(
            "/accounts/sync",
            content=body,
            headers={
                ACCOUNT_SYNC_SIGNATURE_HEADER: sign_account_sync(secret, body)
            },
        )

    assert denied.status_code == 401
    assert signed.status_code == 200
    payload = signed.json()
    assert payload["ok"] is True
    assert payload["user_id"] == "acctnathan"
    assert payload["created"] is True
    assert payload["skipped"] is False
    assert skipped.json()["skipped"] is True
    record = open_accounts(_settings(tmp_path)).get_by_phone(
        E164("+14155552671")
    )
    assert record is not None
    assert record.first_name == "Nathan"
    assert record.stripe_customer_id == "cus_123"


def test_accounts_sync_missing_secret_is_401(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ACCOUNT_SYNC_SECRET", raising=False)
    monkeypatch.setenv("CALL_AGENT_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    monkeypatch.setenv("CALL_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    body = b'{"phone":"+14155552671"}'
    with TestClient(fastapi_app) as client:
        response = client.post(
            "/accounts/sync",
            content=body,
            headers={
                ACCOUNT_SYNC_SIGNATURE_HEADER: sign_account_sync("x", body)
            },
        )

    assert response.status_code == 401
