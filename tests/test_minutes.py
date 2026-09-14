import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from call_agent.accounts import AccountWrite, Accounts, open_accounts
from call_agent.cli import app
from call_agent.config import Settings
from call_agent.domain import (
    E164,
    FirstName,
    Mode,
    Question,
    QuestionType,
    QuickAskRequest,
    UserId,
)
from call_agent.kickoff import (
    NO_MINUTES_TEXT,
    KickoffBlocked,
    KickoffCollecting,
    intake_kickoff,
)
from call_agent.minutes import (
    MAX_PACKS,
    MAX_PREPAID_MINUTES,
    PACK_INCREMENT_MINUTES,
    PACK_PRICE_CENTS,
    PACK_CAP_EXCEEDED,
    PACK_PRICE_UNSET,
    STRIPE_LIVE_FORBIDDEN,
    MeteredPackPurchase,
    MinutesCapError,
    PackPurchaseBlocked,
    apply_consume,
    billed_minutes,
    can_place_call,
    metered_pack_config,
    purchase_metered_packs,
    remaining_minutes,
)
from call_agent.runtime import run_call
from call_agent.user_text import contains_banned_user_text
from call_agent.verify_linq_call import NATURAL_BOOK_TEXT


def _settings(tmp_path: Path, **updates: object) -> Settings:
    values = dict(
        _env_file=None,
        call_agent_db_path=tmp_path / "ledger.sqlite3",
        call_agent_artifacts_dir=tmp_path / "artifacts",
        telnyx_from_number="+12135690899",
        metered_pack_price_cents=PACK_PRICE_CENTS,
    )
    values.update(updates)
    settings = Settings(**values)
    settings.call_agent_artifacts_dir.mkdir(parents=True, exist_ok=True)
    return settings


def _request(user_id: str = "acctnathan") -> QuickAskRequest:
    return QuickAskRequest(
        user_id=UserId(user_id),
        to=E164("+14155552671"),
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
    )


def test_locked_price_is_one_dollar_per_five_minutes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    config = metered_pack_config(settings)

    assert PACK_PRICE_CENTS == 100
    assert PACK_INCREMENT_MINUTES == 5
    assert MAX_PACKS == 12
    assert MAX_PREPAID_MINUTES == 60
    assert config["price_cents"] == 100
    assert config["price_usd"] == 1.0
    assert config["purchase_open"] is True
    assert config["stripe_mode"] == "test"


def test_zero_included_and_prepaid_blocks_kickoff(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = Accounts(tmp_path / "ledger.sqlite3")
    record = store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            user_id=UserId("acctnathan"),
            minutes_included=0,
            minutes_used=0,
            minutes_prepaid=0,
        )
    ).record

    parsed = intake_kickoff(NATURAL_BOOK_TEXT, settings, account=record)

    assert not can_place_call(record)
    assert remaining_minutes(record) == 0
    assert isinstance(parsed, KickoffBlocked)
    assert parsed.reply == NO_MINUTES_TEXT
    assert not contains_banned_user_text(parsed.reply)
    assert not (tmp_path / "artifacts" / "stub-call-0001").exists()


def test_prepaid_pack_unlocks_after_included_cap(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    record = Accounts(tmp_path / "ledger.sqlite3").upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            minutes_included=30,
            minutes_used=30,
            minutes_prepaid=5,
        )
    ).record

    parsed = intake_kickoff(NATURAL_BOOK_TEXT, settings, account=record)

    assert remaining_minutes(record) == 5
    assert isinstance(parsed, KickoffCollecting)


def test_consume_does_not_auto_meter_overage(tmp_path: Path) -> None:
    store = Accounts(tmp_path / "ledger.sqlite3")
    record = store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            user_id=UserId("acctnathan"),
            minutes_included=30,
            minutes_used=29,
            minutes_prepaid=0,
        )
    ).record

    billed = billed_minutes(5 * 60_000)
    assert billed == 5
    after = apply_consume(record, billed)

    assert after.minutes_used == 30
    assert after.minutes_prepaid == 0
    assert remaining_minutes(after) == 0


def test_purchase_credits_five_minutes_at_one_dollar(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = Accounts(tmp_path / "ledger.sqlite3")
    store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            minutes_included=0,
        )
    )

    receipt = purchase_metered_packs(
        settings,
        store,
        MeteredPackPurchase(phone=E164("+14155552671"), packs=1),
    )

    assert receipt.minutes_added == 5
    assert receipt.price_cents == 100
    assert receipt.record.minutes_prepaid == 5
    assert receipt.stripe_mode == "test"


def test_purchase_refuses_more_than_twelve_packs(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = Accounts(tmp_path / "ledger.sqlite3")
    store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            minutes_included=0,
        )
    )
    purchase_metered_packs(
        settings,
        store,
        MeteredPackPurchase(phone=E164("+14155552671"), packs=12),
    )

    with pytest.raises(ValidationError):
        MeteredPackPurchase(phone=E164("+14155552671"), packs=13)
    with pytest.raises(PackPurchaseBlocked) as over:
        purchase_metered_packs(
            settings,
            store,
            MeteredPackPurchase(phone=E164("+14155552671"), packs=1),
        )
    assert over.value.code == PACK_CAP_EXCEEDED
    assert store.get_by_phone(E164("+14155552671")).minutes_prepaid == 60


def test_purchase_refuses_live_stripe_key(tmp_path: Path) -> None:
    settings = _settings(tmp_path, stripe_secret_key="sk_live_nope")
    store = Accounts(tmp_path / "ledger.sqlite3")
    store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
        )
    )

    with pytest.raises(PackPurchaseBlocked) as blocked:
        purchase_metered_packs(
            settings,
            store,
            MeteredPackPurchase(phone=E164("+14155552671"), packs=1),
        )
    assert blocked.value.code == STRIPE_LIVE_FORBIDDEN


async def test_run_call_refuses_when_account_has_no_minutes(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    open_accounts(settings).upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            user_id=UserId("acctnathan"),
            minutes_included=0,
            minutes_prepaid=0,
        )
    )

    with pytest.raises(MinutesCapError, match="5-minute pack"):
        await run_call(
            _request(),
            settings,
            from_did=E164("+15555550100"),
        )
    assert not (tmp_path / "artifacts" / "stub-call-0001").exists()


async def test_run_call_consumes_included_then_stops(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, retain_call_artifacts=True)
    store = open_accounts(settings)
    store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
            user_id=UserId("acctnathan"),
            minutes_included=30,
            minutes_used=0,
        )
    )

    result = await run_call(
        _request(),
        settings,
        from_did=E164("+15555550100"),
    )
    after = store.get(UserId("acctnathan"))

    assert result.status == "answered"
    assert after is not None
    assert after.minutes_used == 1
    assert after.minutes_prepaid == 0
    assert remaining_minutes(after) == 29


def test_empty_pack_price_defaults_to_one_dollar(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        call_agent_db_path=tmp_path / "ledger.sqlite3",
        call_agent_artifacts_dir=tmp_path / "artifacts",
        metered_pack_price_cents="",
    )
    assert settings.metered_pack_price_cents == PACK_PRICE_CENTS
    assert metered_pack_config(settings)["price_cents"] == 100


def test_zero_pack_price_is_ops_kill_switch(tmp_path: Path) -> None:
    settings = _settings(tmp_path, metered_pack_price_cents=0)
    store = Accounts(tmp_path / "ledger.sqlite3")
    store.upsert(
        AccountWrite(
            phone=E164("+14155552671"),
            first_name="Nathan",
        )
    )

    with pytest.raises(PackPurchaseBlocked) as blocked:
        purchase_metered_packs(
            settings,
            store,
            MeteredPackPurchase(phone=E164("+14155552671"), packs=1),
        )
    assert blocked.value.code == PACK_PRICE_UNSET


def test_cli_credit_pack_credits_five_minutes(tmp_path: Path) -> None:
    env = {
        "CALL_AGENT_DB_PATH": str(tmp_path / "ledger.sqlite3"),
        "CALL_AGENT_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
        "QUICK_ASK_LIVE": "0",
        "NO_COLOR": "1",
        "TERM": "dumb",
        "COLUMNS": "120",
        "METERED_PACK_PRICE_CENTS": "100",
    }
    runner = CliRunner()
    seeded = runner.invoke(
        app,
        [
            "account",
            "upsert",
            "--phone",
            "+14155552671",
            "--name",
            "Nathan",
            "--minutes",
            "0",
        ],
        env=env,
    )
    credited = runner.invoke(
        app,
        ["account", "credit-pack", "--phone", "+14155552671", "--packs", "1"],
        env=env,
    )

    assert seeded.exit_code == 0, seeded.output
    assert credited.exit_code == 0, credited.output
    payload = json.loads(credited.stdout)
    assert payload["minutes_added"] == 5
    assert payload["price_cents"] == 100
    assert payload["minutes_prepaid"] == 5
    assert payload["stripe_mode"] == "test"
