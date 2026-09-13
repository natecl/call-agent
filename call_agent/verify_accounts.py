from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from .accounts import (
    ACCOUNT_SYNC_SIGNATURE_HEADER,
    USERS_SCHEMA,
    AccountWrite,
    Accounts,
    sign_account_sync,
)
from .dashboard import dashboard_signature_payload
from .domain import E164, UserId
from .server import app as fastapi_app


PHONE = E164("+14155552671")
FROM_DID = E164("+15555550177")
USER_ID = UserId("acctnathan")
SECRET = "verify-accounts-secret"


class AccountsCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal[
        "cli_roundtrip",
        "omit_preserve",
        "sync_hmac",
        "dashboard_get",
        "no_name_column",
    ]
    passed: bool
    detail: str


class AccountsReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    checks: tuple[
        AccountsCheck,
        AccountsCheck,
        AccountsCheck,
        AccountsCheck,
        AccountsCheck,
    ]


def verify_accounts() -> AccountsReport:
    workspace = Path(tempfile.mkdtemp(prefix="verify-accounts-"))
    with _workspace_env(workspace, SECRET):
        store = Accounts(workspace / "ledger.sqlite3")
        cli_check = _cli_roundtrip(store)
        omit_check = _omit_preserve(store)
        sync_check = _sync_hmac()
        dashboard_check = _dashboard_get()
        schema_check = _no_name_column(workspace / "ledger.sqlite3")
    checks = (cli_check, omit_check, sync_check, dashboard_check, schema_check)
    return AccountsReport(
        passed=all(check.passed for check in checks),
        checks=checks,
    )


def format_accounts_report(report: AccountsReport) -> str:
    lines = [
        (
            f"[{'PASS' if check.passed else 'FAIL'}] "
            f"{check.name:<18} {check.detail}"
        )
        for check in report.checks
    ]
    passed = sum(check.passed for check in report.checks)
    lines.append(f"{passed}/5 passed")
    return "\n".join(lines)


@contextmanager
def _workspace_env(root: Path, secret: str) -> Iterator[None]:
    artifacts = root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    values = {
        "CALL_AGENT_DB_PATH": str(root / "ledger.sqlite3"),
        "CALL_AGENT_ARTIFACTS_DIR": str(artifacts),
        "ACCOUNT_SYNC_SECRET": secret,
        "QUICK_ASK_LIVE": "0",
    }
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _cli_roundtrip(store: Accounts) -> AccountsCheck:
    first = store.upsert(
        AccountWrite(
            phone=PHONE,
            first_name="Nathan",
            user_id=USER_ID,
            from_did=FROM_DID,
            minutes_included=30,
            minutes_used=3,
            prefs={"full_name": "Nathan Tester"},
        )
    )
    fetched = store.get_by_phone(PHONE)
    listed = store.list()
    passed = (
        first.created
        and fetched is not None
        and fetched.user_id == USER_ID
        and fetched.first_name == "Nathan"
        and fetched.from_did == FROM_DID
        and fetched.minutes_used == 3
        and fetched.prefs == {"full_name": "Nathan Tester"}
        and listed == [fetched]
        and store.get(USER_ID) == fetched
    )
    return AccountsCheck(
        name="cli_roundtrip",
        passed=passed,
        detail=(
            f"phone={PHONE} user_id={first.record.user_id} "
            f"from_did={first.record.from_did}"
        ),
    )


def _omit_preserve(store: Accounts) -> AccountsCheck:
    second = store.upsert(AccountWrite(phone=PHONE))
    record = second.record
    passed = (
        not second.created
        and record.first_name == "Nathan"
        and record.from_did == FROM_DID
        and record.prefs == {"full_name": "Nathan Tester"}
        and record.minutes_used == 3
        and record.retain_call_artifacts is False
    )
    return AccountsCheck(
        name="omit_preserve",
        passed=passed,
        detail=(
            f"first_name={record.first_name} from_did={record.from_did} "
            f"minutes_used={record.minutes_used}"
        ),
    )


def _sync_hmac() -> AccountsCheck:
    body = json.dumps(
        {
            "phone": str(PHONE),
            "stripe_customer_id": "cus_verify_accounts",
            "plan": "alpha",
            "stripe_event_created": "2026-09-13T12:00:00+00:00",
        }
    ).encode()
    with TestClient(fastapi_app) as client:
        denied = client.post("/accounts/sync", content=body)
        signed = client.post(
            "/accounts/sync",
            content=body,
            headers={
                ACCOUNT_SYNC_SIGNATURE_HEADER: sign_account_sync(SECRET, body)
            },
        )
    store = Accounts(Path(os.environ["CALL_AGENT_DB_PATH"]))
    record = store.get_by_phone(PHONE)
    passed = (
        denied.status_code == 401
        and signed.status_code == 200
        and signed.json().get("ok") is True
        and signed.json().get("skipped") is False
        and record is not None
        and record.first_name == "Nathan"
        and record.from_did == FROM_DID
        and record.stripe_customer_id == "cus_verify_accounts"
        and record.minutes_used == 3
    )
    return AccountsCheck(
        name="sync_hmac",
        passed=passed,
        detail=(
            f"unsigned={denied.status_code} signed={signed.status_code} "
            f"name_kept={record.first_name if record else None}"
        ),
    )


def _dashboard_get() -> AccountsCheck:
    signed_body = dashboard_signature_payload(str(PHONE))
    with TestClient(fastapi_app) as client:
        denied = client.get(
            "/accounts/dashboard",
            params={"phone": str(PHONE)},
        )
        signed = client.get(
            "/accounts/dashboard",
            params={"phone": str(PHONE)},
            headers={
                ACCOUNT_SYNC_SIGNATURE_HEADER: sign_account_sync(
                    SECRET, signed_body
                )
            },
        )
        unknown = client.get(
            "/accounts/dashboard",
            params={"phone": "+15555550199"},
            headers={
                ACCOUNT_SYNC_SIGNATURE_HEADER: sign_account_sync(
                    SECRET, dashboard_signature_payload("+15555550199")
                )
            },
        )
    payload = signed.json() if signed.status_code == 200 else {}
    user = payload.get("user") or {}
    name = user.get("name") or {}
    unknown_payload = unknown.json() if unknown.status_code == 200 else {}
    passed = (
        denied.status_code == 401
        and signed.status_code == 200
        and user.get("first_name") == "Nathan"
        and name.get("first_name") == "Nathan"
        and name.get("full_name") == "Nathan Tester"
        and payload.get("threads") == []
        and unknown.status_code == 200
        and unknown_payload.get("user") is None
        and unknown_payload.get("threads") == []
        and unknown_payload.get("calls") == []
    )
    return AccountsCheck(
        name="dashboard_get",
        passed=passed,
        detail=(
            f"unsigned={denied.status_code} signed={signed.status_code} "
            f"name={name.get('first_name')}"
        ),
    )


def _no_name_column(path: Path) -> AccountsCheck:
    connection = sqlite3.connect(path)
    columns = [row[1] for row in connection.execute("PRAGMA table_info(users)")]
    connection.close()
    passed = (
        "name" not in columns
        and "first_name" in columns
        and "prefs" in columns
        and "from_did" in columns
        and "\n    name TEXT" not in USERS_SCHEMA
    )
    return AccountsCheck(
        name="no_name_column",
        passed=passed,
        detail=f"columns={','.join(columns)}",
    )
