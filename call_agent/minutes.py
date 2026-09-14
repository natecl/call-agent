from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .accounts import AccountStore, UserRecord
from .config import Settings
from .domain import E164, UserId

# Cap locked 2026-09-14: $1.00 per 5 connected minutes. Max 12 packs
# (60 prepaid minutes). No auto-meter after included minutes hit 0.
PACK_INCREMENT_MINUTES = 5
PACK_PRICE_CENTS = 100
MAX_PACKS = 12
MAX_PREPAID_MINUTES = MAX_PACKS * PACK_INCREMENT_MINUTES
STRIPE_TEST_MODE = "test"
PACK_PRICE_UNSET = "pack_price_unset"
STRIPE_LIVE_FORBIDDEN = "stripe_live_forbidden"
INVALID_PACKS = "invalid_packs"
PACK_CAP_EXCEEDED = "pack_cap_exceeded"

NO_MINUTES_TEXT = (
    "Your included minutes are used up. "
    "Buy a 5-minute pack in your account settings, then text me again."
)


class MinutesCapError(ValueError):
    """Call cannot start without included remaining or prepaid pack balance."""


class PackPurchaseBlocked(ValueError):
    """Prepaid pack purchase is blocked (price, Stripe live, or pack cap)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class MeteredPackPurchase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    phone: E164
    packs: int = Field(ge=1, le=MAX_PACKS)

    @field_validator("packs")
    @classmethod
    def _packs_are_whole_increments(cls, value: int) -> int:
        if value < 1:
            raise ValueError("packs must be at least 1")
        if value > MAX_PACKS:
            raise ValueError(
                f"packs cannot exceed {MAX_PACKS} ({MAX_PREPAID_MINUTES} minutes)"
            )
        return value


class MeteredPackReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    record: UserRecord
    packs: int
    minutes_added: int
    remaining: int
    price_cents: int
    stripe_mode: str = STRIPE_TEST_MODE


def included_remaining(record: UserRecord) -> int:
    return max(0, record.minutes_included - record.minutes_used)


def remaining_minutes(record: UserRecord) -> int:
    return included_remaining(record) + max(0, record.minutes_prepaid)


def can_place_call(record: UserRecord) -> bool:
    return remaining_minutes(record) > 0


def billed_minutes(duration_ms: int) -> int:
    if duration_ms <= 0:
        return 1
    return max(1, (duration_ms + 59_999) // 60_000)


def prepaid_room(record: UserRecord) -> int:
    return max(0, MAX_PREPAID_MINUTES - max(0, record.minutes_prepaid))


def apply_consume(record: UserRecord, billed: int) -> UserRecord:
    charge = max(0, billed)
    from_included = min(charge, included_remaining(record))
    leftover = charge - from_included
    from_prepaid = min(leftover, max(0, record.minutes_prepaid))
    return record.model_copy(
        update={
            "minutes_used": record.minutes_used + from_included,
            "minutes_prepaid": record.minutes_prepaid - from_prepaid,
        }
    )


def consume_call_minutes(
    store: AccountStore, user_id: UserId, billed: int
) -> UserRecord | None:
    record = store.get(user_id)
    if record is None:
        return None
    updated = apply_consume(record, billed)
    store._update(updated)
    return store.get(user_id)


def metered_pack_price_cents(settings: Settings) -> int | None:
    price = settings.metered_pack_price_cents
    if price is None:
        return PACK_PRICE_CENTS
    if price <= 0:
        return None
    return price


def stripe_secret_is_live(secret: str | None) -> bool:
    raw = (secret or "").strip()
    return raw.startswith("sk_live_")


def pack_purchase_blocked_reason(settings: Settings) -> str | None:
    if metered_pack_price_cents(settings) is None:
        return PACK_PRICE_UNSET
    if stripe_secret_is_live(settings.stripe_secret_key):
        return STRIPE_LIVE_FORBIDDEN
    return None


def metered_pack_config(settings: Settings) -> dict[str, Any]:
    price = metered_pack_price_cents(settings)
    blocked = pack_purchase_blocked_reason(settings)
    return {
        "increment_minutes": PACK_INCREMENT_MINUTES,
        "price_cents": price,
        "price_usd": (price / 100) if price is not None else None,
        "max_packs": MAX_PACKS,
        "max_prepaid_minutes": MAX_PREPAID_MINUTES,
        "purchase_open": blocked is None,
        "stripe_mode": STRIPE_TEST_MODE,
        "blocked_reason": blocked,
    }


def minutes_snapshot(record: UserRecord) -> dict[str, int]:
    return {
        "included": record.minutes_included,
        "used": record.minutes_used,
        "included_remaining": included_remaining(record),
        "prepaid": record.minutes_prepaid,
        "prepaid_room": prepaid_room(record),
        "remaining": remaining_minutes(record),
    }


def require_minutes_or_pack(record: UserRecord) -> None:
    if not can_place_call(record):
        raise MinutesCapError(NO_MINUTES_TEXT)


def assert_account_can_place_call(
    settings: Settings, user_id: UserId
) -> UserRecord | None:
    from .accounts import open_accounts
    from .persistence import PersistenceConfigError

    try:
        store = open_accounts(settings)
    except PersistenceConfigError:
        return None
    record = store.get(user_id)
    if record is None:
        return None
    require_minutes_or_pack(record)
    return record


def purchase_metered_packs(
    settings: Settings,
    store: AccountStore,
    purchase: MeteredPackPurchase,
) -> MeteredPackReceipt:
    blocked = pack_purchase_blocked_reason(settings)
    if blocked == PACK_PRICE_UNSET:
        raise PackPurchaseBlocked(
            PACK_PRICE_UNSET,
            "prepaid pack purchase is blocked until METERED_PACK_PRICE_CENTS "
            "is a positive price (Cap locked $1.00 per 5 minutes)",
        )
    if blocked == STRIPE_LIVE_FORBIDDEN:
        raise PackPurchaseBlocked(
            STRIPE_LIVE_FORBIDDEN,
            "prepaid pack purchase stays Stripe TEST; sk_live_ is refused",
        )
    if purchase.packs < 1 or purchase.packs > MAX_PACKS:
        raise PackPurchaseBlocked(
            INVALID_PACKS,
            f"packs must be 1–{MAX_PACKS} (5-minute increments, max "
            f"{MAX_PREPAID_MINUTES} minutes)",
        )
    record = store.get_by_phone(purchase.phone)
    if record is None:
        raise LookupError(f"no account for {purchase.phone}")
    minutes_added = purchase.packs * PACK_INCREMENT_MINUTES
    if minutes_added > prepaid_room(record):
        raise PackPurchaseBlocked(
            PACK_CAP_EXCEEDED,
            "prepaid pack balance cannot exceed "
            f"{MAX_PREPAID_MINUTES} minutes ({MAX_PACKS} packs)",
        )
    credited = record.model_copy(
        update={"minutes_prepaid": record.minutes_prepaid + minutes_added}
    )
    store._update(credited)
    saved = store.get(record.user_id)
    if saved is None:
        raise RuntimeError("prepaid pack credit did not persist")
    price = metered_pack_price_cents(settings)
    assert price is not None
    return MeteredPackReceipt(
        record=saved,
        packs=purchase.packs,
        minutes_added=minutes_added,
        remaining=remaining_minutes(saved),
        price_cents=price,
    )
