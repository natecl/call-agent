import json
import sqlite3
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
    USERS_SCHEMA,
    generate_user_id,
    open_accounts,
    sign_account_sync,
)
from call_agent.cli import app
from call_agent.config import Settings
from call_agent.dashboard import dashboard_signature_payload
from call_agent.domain import E164, UserId
from call_agent.server import app as fastapi_app


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
            from_did=E164("+15555550177"),
        )
    )

    assert result.record.first_name == "Nathan"
    assert result.record.from_did == E164("+15555550177")
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
            "--from-did",
            "+15555550177",
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
    assert payload["from_did"] == "+15555550177"
    assert fetched.exit_code == 0
    assert json.loads(fetched.stdout)["user_id"] == "acctnathan"
    assert json.loads(fetched.stdout)["from_did"] == "+15555550177"
    assert listed.exit_code == 0
    assert "acctnathan" in listed.stdout
    assert "+14155552671" in listed.stdout
    assert "Nathan" in listed.stdout
    assert "+15555550177" in listed.stdout
    assert "FROM_DID" in listed.stdout


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


def test_upsert_merges_from_did_when_omitted(tmp_path: Path) -> None:
    store = Accounts(tmp_path / "ledger.sqlite3")
    first = store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            user_id=UserId("acctnathan"),
            from_did=E164("+15555550177"),
        )
    )
    second = store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            stripe_customer_id="cus_123",
        )
    )

    assert first.record.from_did == E164("+15555550177")
    assert second.record.from_did == E164("+15555550177")
    assert second.record.stripe_customer_id == "cus_123"


def test_from_did_equal_to_phone_fails_loud(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not the user's own phone"):
        AccountWrite(
            phone=E164("+17272701288"),
            first_name="Nathan",
            from_did=E164("+17272701288"),
        )
    env = {
        "CALL_AGENT_DB_PATH": str(tmp_path / "ledger.sqlite3"),
        "CALL_AGENT_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
        "QUICK_ASK_LIVE": "0",
        "NO_COLOR": "1",
        "TERM": "dumb",
        "COLUMNS": "120",
    }
    result = runner.invoke(
        app,
        [
            "account",
            "upsert",
            "--phone",
            "+17272701288",
            "--name",
            "Nathan",
            "--from-did",
            "+17272701288",
        ],
        env=env,
    )
    assert result.exit_code == 2
    assert "not the user's own phone" in result.output


def test_existing_users_table_gains_from_did_column(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            phone TEXT NOT NULL UNIQUE,
            first_name TEXT,
            stripe_customer_id TEXT UNIQUE,
            plan TEXT,
            minutes_included INTEGER NOT NULL DEFAULT 0,
            minutes_used INTEGER NOT NULL DEFAULT 0,
            retain_call_artifacts INTEGER NOT NULL DEFAULT 0,
            prefs TEXT NOT NULL DEFAULT '{}',
            stripe_synced_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO users (
            user_id, phone, first_name, minutes_included, minutes_used,
            retain_call_artifacts, prefs, created_at, updated_at
        ) VALUES (?, ?, ?, 0, 0, 0, '{}', ?, ?)
        """,
        (
            "acctnathan",
            "+14155552671",
            "Nathan",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()

    store = Accounts(path)
    fetched = store.get_by_phone(E164("+14155552671"))
    assert fetched is not None
    assert fetched.from_did is None
    again = Accounts(path)
    assert again.get_by_phone(E164("+14155552671")).from_did is None


def test_existing_neon_users_table_gains_from_did_column(
    tmp_path: Path,
) -> None:
    path = tmp_path / "neon.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            phone TEXT NOT NULL UNIQUE,
            first_name TEXT,
            stripe_customer_id TEXT UNIQUE,
            plan TEXT,
            minutes_included INTEGER NOT NULL DEFAULT 0,
            minutes_used INTEGER NOT NULL DEFAULT 0,
            retain_call_artifacts INTEGER NOT NULL DEFAULT 0,
            prefs TEXT NOT NULL DEFAULT '{}',
            stripe_synced_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO users (
            user_id, phone, first_name, minutes_included, minutes_used,
            retain_call_artifacts, prefs, created_at, updated_at
        ) VALUES (?, ?, ?, 0, 0, 0, '{}', ?, ?)
        """,
        (
            "acctnathan",
            "+14155552671",
            "Nathan",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    connection.commit()
    connection.close()
    store = NeonAccounts(
        "postgresql://unused",
        connect=lambda: _SqlitePg(path),
    )
    fetched = store.get_by_phone(E164("+14155552671"))
    assert fetched is not None
    assert fetched.from_did is None


def test_accounts_sync_without_from_did_preserves_seeded_did(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = "sync-secret"
    monkeypatch.setenv("ACCOUNT_SYNC_SECRET", secret)
    monkeypatch.setenv("CALL_AGENT_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    monkeypatch.setenv("CALL_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    settings = _settings(tmp_path)
    open_accounts(settings).upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            user_id=UserId("acctnathan"),
            from_did=E164("+15555550177"),
        )
    )
    body = json.dumps(
        {
            "phone": "+14155552671",
            "first_name": "Nathan",
            "user_id": "acctnathan",
            "stripe_customer_id": "cus_123",
            "stripe_event_created": "2026-09-13T12:00:00+00:00",
        }
    ).encode()
    assigned = json.dumps(
        {
            "phone": "+14155552671",
            "first_name": "Nathan",
            "user_id": "acctnathan",
            "from_did": "+15555550188",
            "stripe_event_created": "2026-09-13T13:00:00+00:00",
        }
    ).encode()
    with TestClient(fastapi_app) as client:
        kept = client.post(
            "/accounts/sync",
            content=body,
            headers={
                ACCOUNT_SYNC_SIGNATURE_HEADER: sign_account_sync(secret, body)
            },
        )
        after_omit = open_accounts(settings).get_by_phone(
            E164("+14155552671")
        )
        updated = client.post(
            "/accounts/sync",
            content=assigned,
            headers={
                ACCOUNT_SYNC_SIGNATURE_HEADER: sign_account_sync(
                    secret, assigned
                )
            },
        )

    assert kept.status_code == 200
    assert after_omit is not None
    assert after_omit.from_did == E164("+15555550177")
    assert updated.status_code == 200
    record = open_accounts(settings).get_by_phone(E164("+14155552671"))
    assert record is not None
    assert record.from_did == E164("+15555550188")


def test_users_schema_has_no_name_column(tmp_path: Path) -> None:
    assert "\n    name TEXT" not in USERS_SCHEMA
    assert "first_name TEXT" in USERS_SCHEMA
    store = Accounts(tmp_path / "ledger.sqlite3")
    store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            prefs={"full_name": "Nathan Tester"},
        )
    )
    connection = sqlite3.connect(tmp_path / "ledger.sqlite3")
    columns = [
        row[1] for row in connection.execute("PRAGMA table_info(users)")
    ]
    connection.close()
    assert "name" not in columns
    assert "first_name" in columns
    assert "prefs" in columns


def test_upsert_keeps_first_name_prefs_and_minutes_used_when_omitted(
    tmp_path: Path,
) -> None:
    store = Accounts(tmp_path / "ledger.sqlite3")
    first = store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            user_id=UserId("acctnathan"),
            minutes_used=7,
            prefs={"full_name": "Nathan Tester"},
        )
    )
    second = store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            stripe_customer_id="cus_123",
        )
    )
    zeroed = store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            minutes_used=0,
            stripe_event_created="2099-01-01T00:00:00+00:00",
        )
    )

    assert first.record.first_name == "Nathan"
    assert first.record.prefs == {"full_name": "Nathan Tester"}
    assert first.record.minutes_used == 7
    assert second.record.first_name == "Nathan"
    assert second.record.prefs == {"full_name": "Nathan Tester"}
    assert second.record.minutes_used == 7
    assert second.record.stripe_customer_id == "cus_123"
    assert zeroed.record.minutes_used == 0
    assert zeroed.record.first_name == "Nathan"
    assert zeroed.record.prefs == {"full_name": "Nathan Tester"}


def test_accounts_sync_without_name_prefs_or_minutes_used_preserves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = "sync-secret"
    monkeypatch.setenv("ACCOUNT_SYNC_SECRET", secret)
    monkeypatch.setenv("CALL_AGENT_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    monkeypatch.setenv("CALL_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    settings = _settings(tmp_path)
    open_accounts(settings).upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            user_id=UserId("acctnathan"),
            minutes_used=4,
            prefs={"full_name": "Nathan Tester"},
        )
    )
    body = json.dumps(
        {
            "phone": "+14155552671",
            "stripe_customer_id": "cus_123",
            "stripe_event_created": "2026-09-13T12:00:00+00:00",
        }
    ).encode()
    with TestClient(fastapi_app) as client:
        response = client.post(
            "/accounts/sync",
            content=body,
            headers={
                ACCOUNT_SYNC_SIGNATURE_HEADER: sign_account_sync(secret, body)
            },
        )

    assert response.status_code == 200
    record = open_accounts(settings).get_by_phone(E164("+14155552671"))
    assert record is not None
    assert record.first_name == "Nathan"
    assert record.prefs == {"full_name": "Nathan Tester"}
    assert record.minutes_used == 4
    assert record.stripe_customer_id == "cus_123"


def test_accounts_dashboard_empty_for_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = "sync-secret"
    monkeypatch.setenv("ACCOUNT_SYNC_SECRET", secret)
    monkeypatch.setenv("CALL_AGENT_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    monkeypatch.setenv("CALL_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    phone = "+14155552671"
    signed = dashboard_signature_payload(phone)
    with TestClient(fastapi_app) as client:
        denied = client.get(
            "/accounts/dashboard",
            params={"phone": phone},
        )
        missing_phone = client.get(
            "/accounts/dashboard",
            headers={
                ACCOUNT_SYNC_SIGNATURE_HEADER: sign_account_sync(
                    secret, signed
                )
            },
        )
        unknown = client.get(
            "/accounts/dashboard",
            params={"phone": phone},
            headers={
                ACCOUNT_SYNC_SIGNATURE_HEADER: sign_account_sync(
                    secret, signed
                )
            },
        )

    assert denied.status_code == 401
    assert missing_phone.status_code == 400
    assert unknown.status_code == 200
    payload = unknown.json()
    assert payload["ok"] is True
    assert payload["user"] is None
    assert payload["threads"] == []
    assert payload["calls"] == []


def test_accounts_dashboard_includes_retained_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = "sync-secret"
    artifacts = tmp_path / "artifacts"
    monkeypatch.setenv("ACCOUNT_SYNC_SECRET", secret)
    monkeypatch.setenv("CALL_AGENT_DB_PATH", str(tmp_path / "ledger.sqlite3"))
    monkeypatch.setenv("CALL_AGENT_ARTIFACTS_DIR", str(artifacts))
    settings = _settings(tmp_path)
    open_accounts(settings).upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            user_id=UserId("acctnathan"),
            prefs={"full_name": "Nathan Tester"},
            retain_call_artifacts=True,
        )
    )
    event_id = "evt-retain-1"
    call_control_id = "ccid-retain-1"
    other_event = "evt-other"
    wiped_event = "evt-wiped"
    _write_json(
        artifacts / "linq" / event_id / "inbound.json",
        {
            "event_id": event_id,
            "chat_id": "chat-1",
            "sender": "+14155552671",
            "text": "book poke palace",
        },
    )
    _write_json(
        artifacts / "linq" / event_id / "kickoff.json",
        {
            "event_id": event_id,
            "chat_id": "chat-1",
            "sender": "+14155552671",
            "verb": "book",
            "account": {
                "user_id": "acctnathan",
                "phone": "+14155552671",
                "minutes_used": 0,
            },
        },
    )
    _write_json(
        artifacts / "linq" / event_id / "call.json",
        {
            "call_control_id": call_control_id,
            "status": "booked",
            "call_report_ref": f"{call_control_id}/call_report.json",
        },
    )
    _write_json(
        artifacts / call_control_id / "call_report.json",
        {
            "call_control_id": call_control_id,
            "status": "booked",
            "duration_ms": 8000,
            "started_at": "2026-09-13T12:00:00+00:00",
            "ended_at": "2026-09-13T12:00:08+00:00",
            "answer_text": "Table for two at 7:30.",
            "hangup": {"path": "tool", "confirmed": True},
            "counts": {"marks_sent": 1},
            "timing_ms": {},
            "task": {"outcome": "booked"},
        },
    )
    _write_json(
        artifacts / call_control_id / "transcript.json",
        {"turns": [{"role": "agent", "text": "Thanks, that's all I needed."}]},
    )
    _write_json(
        artifacts / "linq" / other_event / "inbound.json",
        {
            "event_id": other_event,
            "chat_id": "chat-2",
            "sender": "+15555550199",
        },
    )
    _write_json(
        artifacts / "linq" / wiped_event / "inbound.json",
        {
            "event_id": wiped_event,
            "chat_id": "chat-1",
            "sender": "+14155552671",
        },
    )
    _write_json(
        artifacts / "linq" / wiped_event / "call.json",
        {
            "call_control_id": "ccid-wiped",
            "status": "booked",
            "call_report_ref": "ccid-wiped/call_report.json",
        },
    )
    phone = "+14155552671"
    signed = dashboard_signature_payload(phone)
    with TestClient(fastapi_app) as client:
        response = client.get(
            "/accounts/dashboard",
            params={"phone": phone},
            headers={
                ACCOUNT_SYNC_SIGNATURE_HEADER: sign_account_sync(
                    secret, signed
                )
            },
        )
        no_calls = client.get(
            "/accounts/dashboard",
            params={"phone": "+15555550188"},
            headers={
                ACCOUNT_SYNC_SIGNATURE_HEADER: sign_account_sync(
                    secret, dashboard_signature_payload("+15555550188")
                )
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["user"]["user_id"] == "acctnathan"
    assert payload["user"]["first_name"] == "Nathan"
    assert payload["user"]["prefs"]["full_name"] == "Nathan Tester"
    assert payload["user"]["name"] == {
        "first_name": "Nathan",
        "full_name": "Nathan Tester",
    }
    event_ids = [thread["event_id"] for thread in payload["threads"]]
    assert event_ids == [event_id, wiped_event]
    assert other_event not in event_ids
    assert len(payload["calls"]) == 1
    call = payload["calls"][0]
    assert call["call_control_id"] == call_control_id
    assert call["call_report"]["status"] == "booked"
    assert call["observability"]["hangup_path"] == "tool"
    assert call["transcript"]["turns"][0]["role"] == "agent"
    assert no_calls.status_code == 200
    empty = no_calls.json()
    assert empty["user"] is None
    assert empty["threads"] == []
    assert empty["calls"] == []


def test_cli_account_upsert_omitted_name_preserves(
    tmp_path: Path,
) -> None:
    env = {
        "CALL_AGENT_DB_PATH": str(tmp_path / "ledger.sqlite3"),
        "CALL_AGENT_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
        "QUICK_ASK_LIVE": "0",
        "NO_COLOR": "1",
        "TERM": "dumb",
        "COLUMNS": "120",
    }
    first = runner.invoke(
        app,
        [
            "account",
            "upsert",
            "--phone",
            "+14155552671",
            "--name",
            "Nathan",
            "--user",
            "acctnathan",
        ],
        env=env,
    )
    store = Accounts(tmp_path / "ledger.sqlite3")
    store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            minutes_used=3,
            prefs={"full_name": "Nathan Tester"},
        )
    )
    second = runner.invoke(
        app,
        ["account", "upsert", "--phone", "+14155552671"],
        env=env,
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    record = json.loads(second.stdout)
    assert record["first_name"] == "Nathan"
    assert record["prefs"] == {"full_name": "Nathan Tester"}
    assert record["minutes_used"] == 3


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

