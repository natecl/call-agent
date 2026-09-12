from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, HttpUrl

CheckName = Literal[
    "health",
    "signature",
    "idempotency",
    "linq_hook",
    "media_gap",
]


class VerifyWebhooksInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: HttpUrl | None = None


class VerifyWebhooksCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: CheckName
    passed: bool
    detail: str


class VerifyWebhooksReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    checks: tuple[
        VerifyWebhooksCheck,
        VerifyWebhooksCheck,
        VerifyWebhooksCheck,
        VerifyWebhooksCheck,
        VerifyWebhooksCheck,
    ]
    stdout: str


def webhook_script() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "workers"
        / "webhooks"
        / "scripts"
        / "verify.mjs"
    )


def verify_webhooks(
    verify_input: VerifyWebhooksInput | None = None,
) -> VerifyWebhooksReport:
    active = verify_input or VerifyWebhooksInput()
    script = webhook_script()
    if not script.is_file():
        raise RuntimeError(f"webhook verifier missing: {script}")
    command = [
        "node",
        "--experimental-strip-types",
        str(script),
    ]
    if active.url is not None:
        command.extend(["--url", str(active.url).rstrip("/")])
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("node is required to run verify-webhooks") from error
    stdout = completed.stdout or completed.stderr
    checks = _parse_checks(stdout)
    if checks is None:
        raise RuntimeError(
            "webhook verifier did not print five named checks:\n"
            f"{stdout}{completed.stderr}"
        )
    return VerifyWebhooksReport(
        passed=completed.returncode == 0
        and all(check.passed for check in checks),
        checks=checks,
        stdout=stdout,
    )


def format_webhooks_report(report: VerifyWebhooksReport) -> str:
    return report.stdout.rstrip()


def _parse_checks(stdout: str) -> tuple[VerifyWebhooksCheck, ...] | None:
    found: list[VerifyWebhooksCheck] = []
    names: tuple[CheckName, ...] = (
        "health",
        "signature",
        "idempotency",
        "linq_hook",
        "media_gap",
    )
    for line in stdout.splitlines():
        if not line.startswith("[PASS]") and not line.startswith("[FAIL]"):
            continue
        passed = line.startswith("[PASS]")
        rest = line[6:].lstrip()
        for name in names:
            if rest.startswith(name):
                found.append(
                    VerifyWebhooksCheck(
                        name=name,
                        passed=passed,
                        detail=rest[len(name) :].strip(),
                    )
                )
                break
    if len(found) != 5:
        return None
    return (
        found[0],
        found[1],
        found[2],
        found[3],
        found[4],
    )
