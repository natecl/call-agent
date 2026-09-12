from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from .config import Settings
from .domain import E164, UserId
from .persistence import (
    NeonLedgerConfig,
    PersistenceConfigError,
    SqliteLedgerConfig,
)
from .telnyx import OrderedNumber, TelnyxHttp


class DidRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: UserId
    did: E164 | None
    status: Literal["ordering", "active"]
    order_id: str | None
    telnyx_number_id: str | None
    connection_id: str
    provisioned_at: str


DIDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS dids (
    user_id TEXT PRIMARY KEY,
    did TEXT UNIQUE,
    status TEXT NOT NULL
        CHECK(status IN ('ordering', 'active')),
    order_id TEXT,
    telnyx_number_id TEXT,
    connection_id TEXT,
    provisioned_at TEXT NOT NULL
)
"""


class LedgerStore(Protocol):
    def provision(
        self,
        user_id: UserId,
        telnyx: TelnyxHttp,
        connection_id: str,
    ) -> DidRecord: ...

    def get(self, user_id: UserId) -> DidRecord | None: ...

    def list(self) -> list[DidRecord]: ...


def provision(
    store: LedgerStore,
    user_id: UserId,
    telnyx: TelnyxHttp,
    connection_id: str,
    insert: Callable[[DidRecord], None],
    activate: Callable[[UserId, OrderedNumber], None],
) -> DidRecord:
    existing = store.get(user_id)
    if existing is not None and existing.status == "active":
        return existing
    if existing is not None:
        if existing.order_id is None:
            raise RuntimeError("ordering DID has no order_id")
        ordered = telnyx.get_order(existing.order_id)
        if ordered.did is None:
            return existing
        activate(user_id, ordered)
        activated = store.get(user_id)
        if activated is None:
            raise RuntimeError("DID activation did not persist")
        return activated

    ordered = telnyx.search_and_order_number(connection_id)
    status: Literal["ordering", "active"] = (
        "active" if ordered.did is not None else "ordering"
    )
    insert(
        DidRecord(
            user_id=user_id,
            did=ordered.did,
            status=status,
            order_id=ordered.order_id,
            telnyx_number_id=ordered.telnyx_number_id,
            connection_id=connection_id,
            provisioned_at=datetime.now(UTC).isoformat(),
        )
    )
    created = store.get(user_id)
    if created is None:
        raise RuntimeError("DID provision did not persist")
    return created


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(DIDS_SCHEMA)

    def provision(
        self,
        user_id: UserId,
        telnyx: TelnyxHttp,
        connection_id: str,
    ) -> DidRecord:
        return provision(
            self, user_id, telnyx, connection_id, self._insert, self._activate
        )

    def get(self, user_id: UserId) -> DidRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    user_id,
                    did,
                    status,
                    order_id,
                    telnyx_number_id,
                    connection_id,
                    provisioned_at
                FROM dids
                WHERE user_id = ?
                """,
                (str(user_id),),
            ).fetchone()
        return self._record(row) if row is not None else None

    def list(self) -> list[DidRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    user_id,
                    did,
                    status,
                    order_id,
                    telnyx_number_id,
                    connection_id,
                    provisioned_at
                FROM dids
                ORDER BY provisioned_at, user_id
                """
            ).fetchall()
        return [self._record(row) for row in rows]

    def _insert(self, record: DidRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO dids (
                    user_id,
                    did,
                    status,
                    order_id,
                    telnyx_number_id,
                    connection_id,
                    provisioned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.user_id),
                    str(record.did) if record.did is not None else None,
                    record.status,
                    record.order_id,
                    record.telnyx_number_id,
                    record.connection_id,
                    record.provisioned_at,
                ),
            )

    def _activate(
        self, user_id: UserId, ordered: OrderedNumber
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE dids
                SET
                    did = ?,
                    status = 'active',
                    telnyx_number_id = ?
                WHERE user_id = ?
                """,
                (
                    str(ordered.did),
                    ordered.telnyx_number_id,
                    str(user_id),
                ),
            )

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

    @staticmethod
    def _record(row: Any) -> DidRecord:
        return DidRecord(
            user_id=row["user_id"],
            did=row["did"],
            status=row["status"],
            order_id=row["order_id"],
            telnyx_number_id=row["telnyx_number_id"],
            connection_id=row["connection_id"],
            provisioned_at=row["provisioned_at"],
        )


class NeonLedger:
    def __init__(
        self,
        database_url: str,
        connect: Callable[[], Any] | None = None,
    ) -> None:
        self.database_url = database_url
        self._connect_factory = connect or _psycopg_connect(database_url)
        with self._connect() as connection:
            connection.execute(DIDS_SCHEMA)

    def provision(
        self,
        user_id: UserId,
        telnyx: TelnyxHttp,
        connection_id: str,
    ) -> DidRecord:
        return provision(
            self, user_id, telnyx, connection_id, self._insert, self._activate
        )

    def get(self, user_id: UserId) -> DidRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    user_id,
                    did,
                    status,
                    order_id,
                    telnyx_number_id,
                    connection_id,
                    provisioned_at
                FROM dids
                WHERE user_id = %s
                """,
                (str(user_id),),
            ).fetchone()
        return Ledger._record(row) if row is not None else None

    def list(self) -> list[DidRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    user_id,
                    did,
                    status,
                    order_id,
                    telnyx_number_id,
                    connection_id,
                    provisioned_at
                FROM dids
                ORDER BY provisioned_at, user_id
                """
            ).fetchall()
        return [Ledger._record(row) for row in rows]

    def _insert(self, record: DidRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO dids (
                    user_id,
                    did,
                    status,
                    order_id,
                    telnyx_number_id,
                    connection_id,
                    provisioned_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(record.user_id),
                    str(record.did) if record.did is not None else None,
                    record.status,
                    record.order_id,
                    record.telnyx_number_id,
                    record.connection_id,
                    record.provisioned_at,
                ),
            )

    def _activate(
        self, user_id: UserId, ordered: OrderedNumber
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE dids
                SET
                    did = %s,
                    status = 'active',
                    telnyx_number_id = %s
                WHERE user_id = %s
                """,
                (
                    str(ordered.did),
                    ordered.telnyx_number_id,
                    str(user_id),
                ),
            )

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


def _psycopg_connect(database_url: str) -> Callable[[], Any]:
    def factory() -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise PersistenceConfigError(
                "NEON_DATABASE_URL is set but psycopg is not installed"
            ) from error
        return psycopg.connect(database_url, row_factory=dict_row)

    return factory


def open_ledger(settings: Settings) -> LedgerStore:
    config = settings.ledger_config()
    if isinstance(config, SqliteLedgerConfig):
        return Ledger(config.path)
    if isinstance(config, NeonLedgerConfig):
        return NeonLedger(config.database_url)
    raise PersistenceConfigError("unknown ledger backend")
