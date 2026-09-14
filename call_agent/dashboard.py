from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .accounts import UserRecord
from .domain import E164, UserId


LINQ_SKIP_DIRS = frozenset({"pending"})


def dashboard_signature_payload(phone: str) -> bytes:
    return phone.encode("utf-8")


def account_display_name(record: UserRecord) -> dict[str, str | None]:
    full_name = record.prefs.get("full_name") or None
    return {
        "first_name": record.first_name,
        "full_name": full_name,
    }


def user_dashboard_payload(record: UserRecord) -> dict[str, Any]:
    payload = record.model_dump(mode="json")
    payload["name"] = account_display_name(record)
    return payload


def build_account_dashboard(
    *,
    record: UserRecord | None,
    artifacts_dir: Path,
    phone: E164,
    bot_number: str | None = None,
) -> dict[str, Any]:
    threads, calls = scan_linq_artifacts(
        artifacts_dir,
        phone=phone,
        user_id=record.user_id if record is not None else None,
    )
    return {
        "ok": True,
        "user": user_dashboard_payload(record) if record is not None else None,
        "threads": threads,
        "calls": calls,
        "bot_number": bot_number,
    }


def scan_linq_artifacts(
    artifacts_dir: Path,
    *,
    phone: E164,
    user_id: UserId | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    linq_root = artifacts_dir / "linq"
    if not linq_root.is_dir():
        return [], []
    wanted_phone = str(phone)
    wanted_user = str(user_id) if user_id is not None else None
    threads: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for child in sorted(linq_root.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or child.name in LINQ_SKIP_DIRS:
            continue
        inbound = _read_json(child / "inbound.json")
        kickoff = _read_json(child / "kickoff.json")
        call = _read_json(child / "call.json")
        if not _belongs_to_user(
            inbound, kickoff, wanted_phone, wanted_user
        ):
            continue
        sender = _first_text(
            (inbound or {}).get("sender"),
            (kickoff or {}).get("sender"),
        )
        threads.append(
            {
                "event_id": child.name,
                "chat_id": _first_text(
                    (inbound or {}).get("chat_id"),
                    (kickoff or {}).get("chat_id"),
                ),
                "sender": sender,
                "verb": (kickoff or {}).get("verb"),
                "inbound": inbound,
                "kickoff": kickoff,
                "call": call,
            }
        )
        call_control_id = str((call or {}).get("call_control_id") or "")
        report = (
            _read_json(artifacts_dir / call_control_id / "call_report.json")
            if call_control_id
            else None
        )
        transcript = (
            _read_json(artifacts_dir / call_control_id / "transcript.json")
            if call_control_id
            else None
        )
        if report is None and transcript is None:
            continue
        calls.append(
            {
                "event_id": child.name,
                "call_control_id": call_control_id or None,
                "status": (call or {}).get("status")
                or (report or {}).get("status"),
                "observability": _observability(report),
                "call_report": report,
                "transcript": transcript,
            }
        )
    return threads, calls


def _belongs_to_user(
    inbound: dict[str, Any] | None,
    kickoff: dict[str, Any] | None,
    phone: str,
    user_id: str | None,
) -> bool:
    senders = [
        value
        for value in (
            (inbound or {}).get("sender"),
            (kickoff or {}).get("sender"),
        )
        if value
    ]
    if phone in {str(sender) for sender in senders}:
        return True
    account = (kickoff or {}).get("account")
    if isinstance(account, dict):
        if str(account.get("phone") or "") == phone:
            return True
        if user_id is not None and str(account.get("user_id") or "") == user_id:
            return True
    request = (kickoff or {}).get("request")
    if (
        isinstance(request, dict)
        and user_id is not None
        and str(request.get("user_id") or "") == user_id
    ):
        return True
    return False


def _observability(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if report is None:
        return None
    hangup = report.get("hangup")
    hangup = hangup if isinstance(hangup, dict) else {}
    return {
        "status": report.get("status"),
        "duration_ms": report.get("duration_ms"),
        "hangup_path": hangup.get("path"),
        "hangup_confirmed": hangup.get("confirmed"),
        "started_at": report.get("started_at"),
        "ended_at": report.get("ended_at"),
        "answer_text": report.get("answer_text"),
        "counts": report.get("counts"),
        "timing_ms": report.get("timing_ms"),
        "task": report.get("task"),
    }


def _first_text(*values: object) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded
