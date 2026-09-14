from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from .accounts import (
    ACCOUNT_SYNC_SIGNATURE_HEADER,
    AccountWrite,
    Accounts,
    sign_account_sync,
)
from .config import Settings
from .domain import E164, UserId
from .kickoff import (
    NO_MINUTES_TEXT,
    KickoffBlocked,
    KickoffCollecting,
    intake_kickoff,
)
from .minutes import (
    MAX_PACKS,
    MAX_PREPAID_MINUTES,
    PACK_INCREMENT_MINUTES,
    PACK_PRICE_CENTS,
    STRIPE_LIVE_FORBIDDEN,
    PACK_CAP_EXCEEDED,
    MeteredPackPurchase,
    PackPurchaseBlocked,
    metered_pack_config,
    metered_pack_price_cents,
    purchase_metered_packs,
    remaining_minutes,
)
from .server import app as fastapi_app
from .user_text import contains_banned_user_text
from .verify_linq_call import NATURAL_BOOK_TEXT


PHONE = E164("+14155552671")
USER_ID = UserId("acctnathan")
SECRET = "verify-minutes-secret"


class MinutesCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal[
        "cap_blocks",
        "included_allows",
        "prepaid_unlocks",
        "pack_cap",
        "price_locked",
        "stripe_test",
    ]
    passed: bool
    detail: str


class MinutesReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    checks: tuple[
        MinutesCheck,
        MinutesCheck,
        MinutesCheck,
        MinutesCheck,
        MinutesCheck,
        MinutesCheck,
    ]


def verify_minutes() -> MinutesReport:
    workspace = Path(tempfile.mkdtemp(prefix="verify-minutes-"))
    with _workspace_env(workspace, SECRET):
        store = Accounts(workspace / "ledger.sqlite3")
        settings = Settings(
            _env_file=None,
            call_agent_db_path=workspace / "ledger.sqlite3",
            call_agent_artifacts_dir=workspace / "artifacts",
            account_sync_secret=SECRET,
        )
        cap_check = _cap_blocks(store, settings)
        included_check = _included_allows(store, settings)
        prepaid_check = _prepaid_unlocks(store, settings)
        pack_check = _pack_cap(store, settings)
        price_check = _price_locked(settings)
        stripe_check = _stripe_test(store, settings)
    checks = (
        cap_check,
        included_check,
        prepaid_check,
        pack_check,
        price_check,
        stripe_check,
    )
    return MinutesReport(
        passed=all(check.passed for check in checks),
        checks=checks,
    )


def format_minutes_report(report: MinutesReport) -> str:
    lines = [
        (
            f"[{'PASS' if check.passed else 'FAIL'}] "
            f"{check.name:<18} {check.detail}"
        )
        for check in report.checks
    ]
    passed = sum(check.passed for check in report.checks)
    lines.append(f"{passed}/6 passed")
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
        "METERED_PACK_PRICE_CENTS": "100",
    }
    previous = {key: os.environ.get(key) for key in values}
    stripe_previous = os.environ.get("STRIPE_SECRET_KEY")
    os.environ.update(values)
    os.environ.pop("STRIPE_SECRET_KEY", None)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if stripe_previous is None:
            os.environ.pop("STRIPE_SECRET_KEY", None)
        else:
            os.environ["STRIPE_SECRET_KEY"] = stripe_previous


def _account(
    store: Accounts,
    *,
    minutes_included: int,
    minutes_used: int = 0,
    minutes_prepaid: int = 0,
    phone: E164 = PHONE,
    user_id: UserId = USER_ID,
) -> None:
    store.upsert(
        AccountWrite(
            phone=phone,
            first_name="Nathan",
            user_id=user_id,
            minutes_included=minutes_included,
            minutes_used=minutes_used,
            minutes_prepaid=minutes_prepaid,
        )
    )


def _cap_blocks(store: Accounts, settings: Settings) -> MinutesCheck:
    _account(store, minutes_included=0, minutes_used=0, minutes_prepaid=0)
    record = store.get_by_phone(PHONE)
    parsed = intake_kickoff(
        NATURAL_BOOK_TEXT,
        settings,
        account=record,
    )
    artifacts = settings.call_agent_artifacts_dir
    passed = (
        record is not None
        and remaining_minutes(record) == 0
        and isinstance(parsed, KickoffBlocked)
        and parsed.reply == NO_MINUTES_TEXT
        and not contains_banned_user_text(parsed.reply)
        and not (artifacts / "stub-call-0001").exists()
    )
    return MinutesCheck(
        name="cap_blocks",
        passed=passed,
        detail=(
            f"kind={type(parsed).__name__} remaining="
            f"{remaining_minutes(record) if record else None}"
        ),
    )


def _included_allows(store: Accounts, settings: Settings) -> MinutesCheck:
    _account(store, minutes_included=30, minutes_used=0, minutes_prepaid=0)
    record = store.get_by_phone(PHONE)
    parsed = intake_kickoff(
        NATURAL_BOOK_TEXT,
        settings,
        account=record,
    )
    passed = (
        record is not None
        and remaining_minutes(record) == 30
        and isinstance(parsed, KickoffCollecting)
        and parsed.verb == "book"
        and not (settings.call_agent_artifacts_dir / "stub-call-0001").exists()
    )
    return MinutesCheck(
        name="included_allows",
        passed=passed,
        detail=(
            f"kind={type(parsed).__name__} remaining="
            f"{remaining_minutes(record) if record else None}"
        ),
    )


def _prepaid_unlocks(store: Accounts, settings: Settings) -> MinutesCheck:
    _account(
        store,
        minutes_included=30,
        minutes_used=30,
        minutes_prepaid=PACK_INCREMENT_MINUTES,
    )
    record = store.get_by_phone(PHONE)
    parsed = intake_kickoff(
        NATURAL_BOOK_TEXT,
        settings,
        account=record,
    )
    passed = (
        record is not None
        and remaining_minutes(record) == PACK_INCREMENT_MINUTES
        and isinstance(parsed, KickoffCollecting)
        and parsed.verb == "book"
        and not (settings.call_agent_artifacts_dir / "stub-call-0001").exists()
    )
    return MinutesCheck(
        name="prepaid_unlocks",
        passed=passed,
        detail=(
            f"kind={type(parsed).__name__} remaining="
            f"{remaining_minutes(record) if record else None}"
        ),
    )


def _pack_cap(store: Accounts, settings: Settings) -> MinutesCheck:
    other = E164("+15555550188")
    _account(
        store,
        phone=other,
        user_id=UserId("acctpacks"),
        minutes_included=0,
        minutes_prepaid=0,
    )
    twelve = purchase_metered_packs(
        settings,
        store,
        MeteredPackPurchase(phone=other, packs=MAX_PACKS),
    )
    over = None
    try:
        purchase_metered_packs(
            settings,
            store,
            MeteredPackPurchase(phone=other, packs=1),
        )
    except PackPurchaseBlocked as error:
        over = error
    too_many = None
    try:
        MeteredPackPurchase(phone=other, packs=MAX_PACKS + 1)
    except ValueError as error:
        too_many = error
    passed = (
        twelve.minutes_added == MAX_PREPAID_MINUTES
        and twelve.record.minutes_prepaid == MAX_PREPAID_MINUTES
        and twelve.price_cents == PACK_PRICE_CENTS
        and isinstance(over, PackPurchaseBlocked)
        and over.code == PACK_CAP_EXCEEDED
        and too_many is not None
    )
    return MinutesCheck(
        name="pack_cap",
        passed=passed,
        detail=(
            f"prepaid={twelve.record.minutes_prepaid} "
            f"over={getattr(over, 'code', None)} "
            f"too_many={too_many is not None}"
        ),
    )


def _price_locked(settings: Settings) -> MinutesCheck:
    config = metered_pack_config(settings)
    price = metered_pack_price_cents(settings)
    passed = (
        price == PACK_PRICE_CENTS
        and config["price_cents"] == 100
        and config["price_usd"] == 1.0
        and config["increment_minutes"] == PACK_INCREMENT_MINUTES
        and config["max_packs"] == MAX_PACKS
        and config["max_prepaid_minutes"] == MAX_PREPAID_MINUTES
        and config["stripe_mode"] == "test"
        and config["purchase_open"] is True
    )
    return MinutesCheck(
        name="price_locked",
        passed=passed,
        detail=(
            f"price_cents={config.get('price_cents')} "
            f"usd={config.get('price_usd')} packs={config.get('max_packs')}"
        ),
    )


def _stripe_test(store: Accounts, settings: Settings) -> MinutesCheck:
    live = Settings(
        _env_file=None,
        call_agent_db_path=settings.call_agent_db_path,
        call_agent_artifacts_dir=settings.call_agent_artifacts_dir,
        account_sync_secret=SECRET,
        metered_pack_price_cents=PACK_PRICE_CENTS,
        stripe_secret_key="sk_live_forbidden",
    )
    live_error = None
    try:
        purchase_metered_packs(
            live,
            store,
            MeteredPackPurchase(phone=PHONE, packs=1),
        )
    except PackPurchaseBlocked as error:
        live_error = error
    body = json.dumps({"phone": str(PHONE), "packs": 1}).encode()
    with TestClient(fastapi_app) as client:
        denied = client.post("/billing/metered-pack", content=body)
        signed = client.post(
            "/billing/metered-pack",
            content=body,
            headers={
                ACCOUNT_SYNC_SIGNATURE_HEADER: sign_account_sync(SECRET, body)
            },
        )
    payload = signed.json() if signed.status_code == 200 else {}
    passed = (
        isinstance(live_error, PackPurchaseBlocked)
        and live_error.code == STRIPE_LIVE_FORBIDDEN
        and denied.status_code == 401
        and signed.status_code == 200
        and payload.get("ok") is True
        and payload.get("minutes_added") == PACK_INCREMENT_MINUTES
        and payload.get("price_cents") == PACK_PRICE_CENTS
        and payload.get("stripe_mode") == "test"
    )
    return MinutesCheck(
        name="stripe_test",
        passed=passed,
        detail=(
            f"live={getattr(live_error, 'code', None)} "
            f"unsigned={denied.status_code} signed={signed.status_code}"
        ),
    )
