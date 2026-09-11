from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .domain import E164, UserId
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


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
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
            )

    def provision(
        self,
        user_id: UserId,
        telnyx: TelnyxHttp,
        connection_id: str,
    ) -> DidRecord:
        existing = self.get(user_id)
        if existing is not None and existing.status == "active":
            return existing
        if existing is not None:
            if existing.order_id is None:
                raise RuntimeError("ordering DID has no order_id")
            ordered = telnyx.get_order(existing.order_id)
            if ordered.did is None:
                return existing
            self._activate(user_id, ordered)
            activated = self.get(user_id)
            if activated is None:
                raise RuntimeError("DID activation did not persist")
            return activated

        ordered = telnyx.search_and_order_number(connection_id)
        status = "active" if ordered.did is not None else "ordering"
        provisioned_at = datetime.now(UTC).isoformat()
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
                    str(user_id),
                    str(ordered.did) if ordered.did is not None else None,
                    status,
                    ordered.order_id,
                    ordered.telnyx_number_id,
                    connection_id,
                    provisioned_at,
                ),
            )
        created = self.get(user_id)
        if created is None:
            raise RuntimeError("DID provision did not persist")
        return created

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
    def _record(row: sqlite3.Row) -> DidRecord:
        return DidRecord(
            user_id=row["user_id"],
            did=row["did"],
            status=row["status"],
            order_id=row["order_id"],
            telnyx_number_id=row["telnyx_number_id"],
            connection_id=row["connection_id"],
            provisioned_at=row["provisioned_at"],
        )
