from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import Settings
from .domain import E164, FirstName, UserId
from .ledger import _psycopg_connect
from .persistence import NeonLedgerConfig, PersistenceConfigError, SqliteLedgerConfig


ACCOUNT_SYNC_SIGNATURE_HEADER = "x-account-sync-signature"

# Cap account model: `phone` is the user's own number (unique kickoff
# key). The Treena iMessage bot is shared sitewide (`LINQ_FROM_E164`),
# not a unique DID per user. `from_did` is the Telnyx caller ID: nullable,
# not unique. Kickoff still requires it until Cap names a shared Telnyx
# from-number. Do not store the shared bot on this column.

USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    phone TEXT NOT NULL UNIQUE,
    first_name TEXT,
    from_did TEXT,
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

USERS_FROM_DID_MIGRATION = "ALTER TABLE users ADD COLUMN from_did TEXT"

USERS_SELECT = """
    SELECT
        user_id,
        phone,
        first_name,
        from_did,
        stripe_customer_id,
        plan,
        minutes_included,
        minutes_used,
        retain_call_artifacts,
        prefs,
        stripe_synced_at,
        created_at,
        updated_at
    FROM users
"""


def _reject_from_did_equal_to_phone(phone: E164, from_did: E164 | None) -> None:
    if from_did is not None and str(from_did) == str(phone):
        raise ValueError(
            "from_did is the Telnyx caller ID, not the user's own phone "
            "and not the shared iMessage bot"
        )


def _ensure_from_did_column(add_column: Callable[[], None]) -> None:
    try:
        add_column()
    except Exception as error:
        if not _is_duplicate_column(error):
            raise


def _is_duplicate_column(error: Exception) -> bool:
    text = str(error).casefold()
    return (
        "duplicate column" in text
        or "already exists" in text
        or type(error).__name__ == "DuplicateColumn"
    )


class AccountConflict(ValueError):
    pass


class AccountSyncAuthError(ValueError):
    pass


class AccountSyncPayloadError(ValueError):
    pass


class UserRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UserId
    phone: E164
    first_name: str | None = None
    from_did: E164 | None = None
    stripe_customer_id: str | None = None
    plan: str | None = None
    minutes_included: int = 0
    minutes_used: int = 0
    retain_call_artifacts: bool = False
    prefs: dict[str, str] = Field(default_factory=dict)
    stripe_synced_at: str | None = None
    created_at: str
    updated_at: str

    @model_validator(mode="after")
    def _from_did_is_not_the_users_phone(self) -> UserRecord:
        _reject_from_did_equal_to_phone(self.phone, self.from_did)
        return self


class AccountWrite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    phone: E164
    first_name: str | None = None
    from_did: E164 | None = None
    user_id: UserId | None = None
    stripe_customer_id: str | None = None
    plan: str | None = None
    minutes_included: int = 0
    minutes_used: int | None = None
    retain_call_artifacts: bool = False
    prefs: dict[str, str] | None = None
    stripe_event_created: str | None = None

    @field_validator("minutes_included")
    @classmethod
    def _minutes_included_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("minutes cannot be negative")
        return value

    @field_validator("minutes_used")
    @classmethod
    def _minutes_used_non_negative(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError("minutes cannot be negative")
        return value

    @field_validator("first_name")
    @classmethod
    def _optional_first_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        FirstName(stripped)
        return stripped

    @field_validator("stripe_customer_id", "plan")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("prefs")
    @classmethod
    def _string_prefs(
        cls, value: dict[str, str] | None
    ) -> dict[str, str] | None:
        if value is None:
            return None
        cleaned: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not isinstance(item, str):
                raise ValueError("prefs keys and values must be strings")
            cleaned[key] = item
        return cleaned

    @model_validator(mode="after")
    def _from_did_is_not_the_users_phone(self) -> AccountWrite:
        _reject_from_did_equal_to_phone(self.phone, self.from_did)
        return self


class AccountUpsertResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    record: UserRecord
    skipped: bool
    created: bool


class AccountStore(Protocol):
    def get(self, user_id: UserId) -> UserRecord | None: ...

    def get_by_phone(self, phone: E164) -> UserRecord | None: ...

    def get_by_stripe_customer_id(
        self, stripe_customer_id: str
    ) -> UserRecord | None: ...

    def upsert(self, write: AccountWrite) -> AccountUpsertResult: ...

    def list(self) -> list[UserRecord]: ...


def lookup_account(
    settings: Settings, sender: object
) -> UserRecord | None:
    raw = str(sender).strip()
    try:
        phone = E164(raw)
    except ValueError:
        return None
    return open_accounts(settings).get_by_phone(phone)


def sign_account_sync(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_account_sync_signature(
    secret: str,
    body: bytes,
    headers: Mapping[str, str],
) -> None:
    if not secret.strip():
        raise AccountSyncAuthError("ACCOUNT_SYNC_SECRET is missing")
    signature = _header(headers, ACCOUNT_SYNC_SIGNATURE_HEADER)
    expected = sign_account_sync(secret, body)
    if not hmac.compare_digest(expected, signature):
        raise AccountSyncAuthError("account sync signature mismatch")


def parse_account_sync_payload(raw: bytes) -> AccountWrite:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AccountSyncPayloadError("account sync body is not JSON") from error
    if not isinstance(payload, dict):
        raise AccountSyncPayloadError("account sync body must be an object")
    try:
        return AccountWrite.model_validate(payload)
    except (ValueError, TypeError) as error:
        raise AccountSyncPayloadError(str(error)) from error


def generate_user_id() -> UserId:
    return UserId(uuid.uuid4().hex)


def open_accounts(settings: Settings) -> AccountStore:
    config = settings.ledger_config()
    if isinstance(config, SqliteLedgerConfig):
        return Accounts(config.path)
    if isinstance(config, NeonLedgerConfig):
        return NeonAccounts(config.database_url)
    raise PersistenceConfigError("unknown ledger backend")


def upsert_account(
    store: AccountStore,
    write: AccountWrite,
    *,
    now: datetime | None = None,
) -> AccountUpsertResult:
    clock = now or datetime.now(UTC)
    stamp = _iso(clock)
    event_at = _parse_iso(write.stripe_event_created)
    target = _target_row(store, write)
    if target is not None and _should_skip(target.stripe_synced_at, event_at):
        return AccountUpsertResult(record=target, skipped=True, created=False)
    synced_at = (
        write.stripe_event_created
        if write.stripe_event_created is not None
        else (stamp if write.stripe_customer_id is not None else None)
    )
    if target is None:
        user_id = write.user_id or generate_user_id()
        record = UserRecord(
            user_id=user_id,
            phone=write.phone,
            first_name=write.first_name,
            from_did=write.from_did,
            stripe_customer_id=write.stripe_customer_id,
            plan=write.plan,
            minutes_included=write.minutes_included,
            minutes_used=_keep(write.minutes_used, 0),
            retain_call_artifacts=write.retain_call_artifacts,
            prefs=_keep(write.prefs, {}),
            stripe_synced_at=synced_at,
            created_at=stamp,
            updated_at=stamp,
        )
        store._insert(record)
        created = store.get(user_id)
        if created is None:
            raise RuntimeError("account insert did not persist")
        return AccountUpsertResult(record=created, skipped=False, created=True)
    record = UserRecord(
        user_id=target.user_id,
        phone=write.phone,
        first_name=_keep(write.first_name, target.first_name),
        from_did=_keep(write.from_did, target.from_did),
        stripe_customer_id=(
            write.stripe_customer_id or target.stripe_customer_id
        ),
        plan=write.plan if write.plan is not None else target.plan,
        minutes_included=write.minutes_included,
        minutes_used=_keep(write.minutes_used, target.minutes_used),
        retain_call_artifacts=write.retain_call_artifacts,
        prefs=_keep(write.prefs, target.prefs),
        stripe_synced_at=synced_at or target.stripe_synced_at,
        created_at=target.created_at,
        updated_at=stamp,
    )
    store._update(record)
    updated = store.get(target.user_id)
    if updated is None:
        raise RuntimeError("account update did not persist")
    return AccountUpsertResult(record=updated, skipped=False, created=False)


def _target_row(store: AccountStore, write: AccountWrite) -> UserRecord | None:
    by_stripe = None
    if write.stripe_customer_id is not None:
        by_stripe = store.get_by_stripe_customer_id(write.stripe_customer_id)
    by_phone = store.get_by_phone(write.phone)
    by_id = store.get(write.user_id) if write.user_id is not None else None
    if by_stripe is not None and by_phone is not None:
        if by_stripe.user_id != by_phone.user_id:
            raise AccountConflict(
                f"phone {write.phone} already belongs to another account"
            )
    if by_stripe is not None and by_id is not None:
        if by_stripe.user_id != by_id.user_id:
            raise AccountConflict(
                "user_id does not match stripe_customer_id"
            )
    if by_phone is not None and by_id is not None:
        if by_phone.user_id != by_id.user_id:
            raise AccountConflict(
                f"phone {write.phone} already belongs to another account"
            )
    if (
        by_phone is not None
        and write.stripe_customer_id is not None
        and by_phone.stripe_customer_id is not None
        and by_phone.stripe_customer_id != write.stripe_customer_id
    ):
        raise AccountConflict(
            f"phone {write.phone} already belongs to another account"
        )
    return by_stripe or by_phone or by_id


def _should_skip(existing_synced_at: str | None, event_at: datetime | None) -> bool:
    if existing_synced_at is None or event_at is None:
        return False
    existing = _parse_iso(existing_synced_at)
    if existing is None:
        return False
    return event_at <= existing


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AccountSyncPayloadError(
            f"invalid stripe_event_created: {value!r}"
        ) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _keep(incoming: Any, existing: Any) -> Any:
    return incoming if incoming is not None else existing


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.casefold()
    for key, value in headers.items():
        if key.casefold() == wanted:
            stripped = value.strip()
            if stripped:
                return stripped
    raise AccountSyncAuthError(f"missing {name} header")


def _prefs_json(prefs: dict[str, str]) -> str:
    return json.dumps(prefs, sort_keys=True)


def _record(row: Any) -> UserRecord:
    prefs_raw = row["prefs"]
    if isinstance(prefs_raw, dict):
        prefs = {str(key): str(value) for key, value in prefs_raw.items()}
    else:
        loaded = json.loads(prefs_raw)
        if not isinstance(loaded, dict):
            raise RuntimeError("users.prefs is not an object")
        prefs = {str(key): str(value) for key, value in loaded.items()}
    retain = row["retain_call_artifacts"]
    return UserRecord(
        user_id=row["user_id"],
        phone=row["phone"],
        first_name=row["first_name"] or None,
        from_did=row["from_did"] or None,
        stripe_customer_id=row["stripe_customer_id"],
        plan=row["plan"],
        minutes_included=int(row["minutes_included"]),
        minutes_used=int(row["minutes_used"]),
        retain_call_artifacts=bool(retain),
        prefs=prefs,
        stripe_synced_at=row["stripe_synced_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_values(record: UserRecord) -> tuple[object, ...]:
    return (
        str(record.user_id),
        str(record.phone),
        record.first_name,
        str(record.from_did) if record.from_did is not None else None,
        record.stripe_customer_id,
        record.plan,
        record.minutes_included,
        record.minutes_used,
        1 if record.retain_call_artifacts else 0,
        _prefs_json(record.prefs),
        record.stripe_synced_at,
        record.created_at,
        record.updated_at,
    )


def _update_values(record: UserRecord) -> tuple[object, ...]:
    return (
        str(record.phone),
        record.first_name,
        str(record.from_did) if record.from_did is not None else None,
        record.stripe_customer_id,
        record.plan,
        record.minutes_included,
        record.minutes_used,
        1 if record.retain_call_artifacts else 0,
        _prefs_json(record.prefs),
        record.stripe_synced_at,
        record.updated_at,
        str(record.user_id),
    )


def _is_unique_violation(error: Exception) -> bool:
    if isinstance(error, sqlite3.IntegrityError):
        return True
    text = str(error).casefold()
    return "unique" in text or type(error).__name__ == "UniqueViolation"


class Accounts:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(USERS_SCHEMA)
        _ensure_from_did_column(self._add_from_did_column)

    def _add_from_did_column(self) -> None:
        with self._connect() as connection:
            connection.execute(USERS_FROM_DID_MIGRATION)

    def get(self, user_id: UserId) -> UserRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                USERS_SELECT + " WHERE user_id = ?",
                (str(user_id),),
            ).fetchone()
        return _record(row) if row is not None else None

    def get_by_phone(self, phone: E164) -> UserRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                USERS_SELECT + " WHERE phone = ?",
                (str(phone),),
            ).fetchone()
        return _record(row) if row is not None else None

    def get_by_stripe_customer_id(
        self, stripe_customer_id: str
    ) -> UserRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                USERS_SELECT + " WHERE stripe_customer_id = ?",
                (stripe_customer_id,),
            ).fetchone()
        return _record(row) if row is not None else None

    def upsert(self, write: AccountWrite) -> AccountUpsertResult:
        return upsert_account(self, write)

    def list(self) -> list[UserRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                USERS_SELECT + " ORDER BY created_at, user_id"
            ).fetchall()
        return [_record(row) for row in rows]

    def _insert(self, record: UserRecord) -> None:
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id,
                        phone,
                        first_name,
                        from_did,
                        stripe_customer_id,
                        plan,
                        minutes_included,
                        minutes_used,
                        retain_call_artifacts,
                        prefs,
                        stripe_synced_at,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _row_values(record),
                )
            except sqlite3.IntegrityError as error:
                raise AccountConflict(
                    f"phone {record.phone} already belongs to another account"
                ) from error

    def _update(self, record: UserRecord) -> None:
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    UPDATE users
                    SET
                        phone = ?,
                        first_name = ?,
                        from_did = ?,
                        stripe_customer_id = ?,
                        plan = ?,
                        minutes_included = ?,
                        minutes_used = ?,
                        retain_call_artifacts = ?,
                        prefs = ?,
                        stripe_synced_at = ?,
                        updated_at = ?
                    WHERE user_id = ?
                    """,
                    _update_values(record),
                )
            except sqlite3.IntegrityError as error:
                raise AccountConflict(
                    f"phone {record.phone} already belongs to another account"
                ) from error

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class NeonAccounts:
    def __init__(
        self,
        database_url: str,
        connect: Callable[[], Any] | None = None,
    ) -> None:
        self.database_url = database_url
        self._connect_factory = connect or _psycopg_connect(database_url)
        with self._connect() as connection:
            connection.execute(USERS_SCHEMA)
        _ensure_from_did_column(self._add_from_did_column)

    def _add_from_did_column(self) -> None:
        with self._connect() as connection:
            connection.execute(USERS_FROM_DID_MIGRATION)

    def get(self, user_id: UserId) -> UserRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                USERS_SELECT + " WHERE user_id = %s",
                (str(user_id),),
            ).fetchone()
        return _record(row) if row is not None else None

    def get_by_phone(self, phone: E164) -> UserRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                USERS_SELECT + " WHERE phone = %s",
                (str(phone),),
            ).fetchone()
        return _record(row) if row is not None else None

    def get_by_stripe_customer_id(
        self, stripe_customer_id: str
    ) -> UserRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                USERS_SELECT + " WHERE stripe_customer_id = %s",
                (stripe_customer_id,),
            ).fetchone()
        return _record(row) if row is not None else None

    def upsert(self, write: AccountWrite) -> AccountUpsertResult:
        return upsert_account(self, write)

    def list(self) -> list[UserRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                USERS_SELECT + " ORDER BY created_at, user_id"
            ).fetchall()
        return [_record(row) for row in rows]

    def _insert(self, record: UserRecord) -> None:
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO users (
                        user_id,
                        phone,
                        first_name,
                        from_did,
                        stripe_customer_id,
                        plan,
                        minutes_included,
                        minutes_used,
                        retain_call_artifacts,
                        prefs,
                        stripe_synced_at,
                        created_at,
                        updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    _row_values(record),
                )
            except Exception as error:
                if _is_unique_violation(error):
                    raise AccountConflict(
                        f"phone {record.phone} already belongs to another account"
                    ) from error
                raise

    def _update(self, record: UserRecord) -> None:
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    UPDATE users
                    SET
                        phone = %s,
                        first_name = %s,
                        from_did = %s,
                        stripe_customer_id = %s,
                        plan = %s,
                        minutes_included = %s,
                        minutes_used = %s,
                        retain_call_artifacts = %s,
                        prefs = %s,
                        stripe_synced_at = %s,
                        updated_at = %s
                    WHERE user_id = %s
                    """,
                    _update_values(record),
                )
            except Exception as error:
                if _is_unique_violation(error):
                    raise AccountConflict(
                        f"phone {record.phone} already belongs to another account"
                    ) from error
                raise

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        connection = self._connect_factory()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
