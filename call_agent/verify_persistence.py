from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .artifacts import LocalArtifactStore
from .config import Settings
from .domain import UserId
from .ledger import Ledger
from .telnyx import StubTelnyxHttp


class PersistenceCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Literal["config", "ledger_roundtrip", "artifact_put_get"]
    passed: bool
    detail: str


class PersistenceReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    checks: tuple[PersistenceCheck, PersistenceCheck, PersistenceCheck]
    ledger_kind: str
    artifact_kind: str


def verify_persistence(settings: Settings | None = None) -> PersistenceReport:
    active = settings or Settings()
    ledger_cfg = active.ledger_config()
    artifact_cfg = active.artifact_config()

    config_check = PersistenceCheck(
        name="config",
        passed=True,
        detail=(
            f"{ledger_cfg.kind} ledger + {artifact_cfg.kind} artifacts; "
            "ports exercised with sqlite/local stubs"
        ),
    )
    workspace = Path(tempfile.mkdtemp(prefix="verify-persistence-"))
    ledger_check = _ledger_roundtrip(workspace / "call-agent.sqlite3")
    artifact_check = _artifact_roundtrip(workspace / "artifacts")
    checks = (config_check, ledger_check, artifact_check)
    return PersistenceReport(
        passed=all(check.passed for check in checks),
        checks=checks,
        ledger_kind=ledger_cfg.kind,
        artifact_kind=artifact_cfg.kind,
    )


def format_persistence_report(report: PersistenceReport) -> str:
    lines = [
        (
            f"[{'PASS' if check.passed else 'FAIL'}] "
            f"{check.name:<18} {check.detail}"
        )
        for check in report.checks
    ]
    passed = sum(check.passed for check in report.checks)
    lines.append(f"{passed}/3 passed")
    return "\n".join(lines)


def _ledger_roundtrip(path: Path) -> PersistenceCheck:
    ledger = Ledger(path)
    telnyx = StubTelnyxHttp()
    user = UserId("persist")
    first = ledger.provision(user, telnyx, "stub-connection")
    second = ledger.provision(user, telnyx, "stub-connection")
    listed = ledger.list()
    passed = (
        first.did is not None
        and first.model_dump(mode="json") == second.model_dump(mode="json")
        and len(listed) == 1
        and listed[0].user_id == user
        and ledger.get(user) == first
    )
    return PersistenceCheck(
        name="ledger_roundtrip",
        passed=passed,
        detail=(
            f"did={first.did} repeat_agrees="
            f"{first.did == second.did} rows={len(listed)}"
        ),
    )


def _artifact_roundtrip(root: Path) -> PersistenceCheck:
    store = LocalArtifactStore(root)
    key = "stub-call-0001/transcript.json"
    body = b'{"turns":[]}\n'
    store.put(key, body, "application/json")
    got = store.get(key)
    passed = got == body
    return PersistenceCheck(
        name="artifact_put_get",
        passed=passed,
        detail=f"key={key} bytes={len(got)} match={passed}",
    )
