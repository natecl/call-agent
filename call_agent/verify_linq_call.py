from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .config import Settings
from .domain import (
    BookRequest,
    E164,
    FirstName,
    Mode,
    Question,
    QuestionType,
    QuickAskRequest,
    UserId,
)
from .kickoff import (
    BOOK_USAGE,
    KickoffMalformed,
    KickoffParsed,
    KickoffSession,
    make_session,
    malformed_reply_text,
    parse_kickoff,
    run_kickoff,
)
from .linq import StubLinq, parse_inbound
from .messaging import IMESSAGE
from .runtime import normalized_public_host
from .verify_message import (
    INBOUND_CHAT_ID,
    INBOUND_SENDER,
)

BOOK_KICKOFF_TEXT = (
    "book +14155552671 party 2 at Friday 7pm name Nathan "
    "notes outdoor if possible"
)
ASK_KICKOFF_TEXT = "ask +14155552671 hours name Nathan"
BAD_KICKOFF_TEXT = "book +14155552671 party two"
BOOK_EVENT_ID = "kickoff-book-0001"
ASK_EVENT_ID = "kickoff-ask-0001"
BAD_EVENT_ID = "kickoff-bad-0001"
STUB_FROM = E164("+15555550100")


class LinqCallVerifyInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    from_did: E164 = STUB_FROM
    to: E164 = E164("+14155552671")
    text: str = BOOK_KICKOFF_TEXT


CheckName = Literal[
    "parse_book",
    "parse_quick_ask",
    "parse_fail_loud",
    "kickoff_reply",
    "call_ran",
    "cross_link",
    "same_host_dial",
]


class LinqCallVerifyCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: CheckName
    passed: bool
    detail: str


class LinqCallVerifyReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    checks: tuple[
        LinqCallVerifyCheck,
        LinqCallVerifyCheck,
        LinqCallVerifyCheck,
        LinqCallVerifyCheck,
        LinqCallVerifyCheck,
        LinqCallVerifyCheck,
        LinqCallVerifyCheck,
    ]


def kickoff_inbound_bytes(text: str, event_id: str) -> bytes:
    return json.dumps(
        {
            "api_version": "v3",
            "webhook_version": "2026-02-03",
            "event_type": "message.received",
            "event_id": event_id,
            "data": {
                "chat": {"id": INBOUND_CHAT_ID},
                "id": "kickoff-msg-0001",
                "direction": "inbound",
                "sender_handle": {
                    "handle": INBOUND_SENDER,
                    "service": IMESSAGE,
                },
                "parts": [{"type": "text", "value": text}],
                "service": IMESSAGE,
            },
        }
    ).encode()


def verify_linq_call(
    verify_input: LinqCallVerifyInput | None = None,
) -> LinqCallVerifyReport:
    active = verify_input or LinqCallVerifyInput()
    root = Path(tempfile.mkdtemp(prefix="verify-linq-call-"))
    artifacts = root / "artifacts"
    artifacts.mkdir()
    settings = Settings(
        _env_file=None,
        linq_api_token=None,
        linq_from_e164=str(active.from_did),
        linq_kickoff_caller="Nathan",
        call_agent_db_path=root / "ledger.sqlite3",
        call_agent_artifacts_dir=artifacts,
        hangup_trailing_silence_s=0,
        public_base_url="https://stub.invalid",
        quick_ask_live=False,
    )

    book_parsed = parse_kickoff(active.text, settings)
    expected_book = BookRequest(
        user_id=UserId("default"),
        to=active.to,
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
        party_size=2,
        time_text="Friday 7pm",
        constraints="outdoor if possible",
    )
    book_ok = (
        isinstance(book_parsed, KickoffParsed)
        and book_parsed.verb == "book"
        and book_parsed.request == expected_book
    )
    book_check = LinqCallVerifyCheck(
        name="parse_book",
        passed=book_ok,
        detail=(
            f"verb={getattr(book_parsed, 'verb', None)} "
            f"to={getattr(getattr(book_parsed, 'request', None), 'to', None)}"
        ),
    )

    ask_parsed = parse_kickoff(ASK_KICKOFF_TEXT, settings)
    expected_ask = QuickAskRequest(
        user_id=UserId("default"),
        to=active.to,
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
    )
    ask_ok = (
        isinstance(ask_parsed, KickoffParsed)
        and ask_parsed.verb == "ask"
        and ask_parsed.request == expected_ask
    )
    ask_check = LinqCallVerifyCheck(
        name="parse_quick_ask",
        passed=ask_ok,
        detail=(
            f"verb={getattr(ask_parsed, 'verb', None)} "
            f"kind={getattr(getattr(getattr(ask_parsed, 'request', None), 'question', None), 'kind', None)}"
        ),
    )

    bad_parsed = parse_kickoff(BAD_KICKOFF_TEXT, settings)
    bad_inbound = parse_inbound(
        kickoff_inbound_bytes(BAD_KICKOFF_TEXT, BAD_EVENT_ID)
    )
    fail_ok = False
    fail_detail = "malformed parse did not run"
    try:
        if not isinstance(bad_parsed, KickoffMalformed) or bad_inbound is None:
            raise ValueError("expected KickoffMalformed")
        fail_port = StubLinq()
        fail_session = make_session(bad_inbound, settings, fail_port)
        help_text = malformed_reply_text(bad_parsed)
        fail_session.text(help_text, kind="error")
        kickoff_marker = (
            artifacts / "linq" / BAD_EVENT_ID / "kickoff.json"
        )
        fail_ok = (
            "Couldn't parse that." in help_text
            and "party" in bad_parsed.missing
            and BOOK_USAGE.split(" [")[0] in help_text
            and not kickoff_marker.exists()
            and not (artifacts / "stub-call-0001").exists()
            and fail_port.last_path == f"/chats/{INBOUND_CHAT_ID}/messages"
        )
        fail_detail = (
            f"missing={bad_parsed.missing} kickoff_json="
            f"{kickoff_marker.exists()}"
        )
    except (OSError, ValueError, TypeError) as error:
        fail_ok = False
        fail_detail = str(error)
    fail_check = LinqCallVerifyCheck(
        name="parse_fail_loud",
        passed=fail_ok,
        detail=fail_detail,
    )

    inbound = parse_inbound(
        kickoff_inbound_bytes(active.text, BOOK_EVENT_ID)
    )
    reply_ok = False
    call_ok = False
    link_ok = False
    host_ok = False
    reply_detail = "kickoff did not run"
    call_detail = "call did not run"
    link_detail = "cross-link did not run"
    host_detail = "dial did not run"
    try:
        if inbound is None or not isinstance(book_parsed, KickoffParsed):
            raise ValueError("book fixture did not parse")
        port = StubLinq()
        session = KickoffSession(
            inbound=inbound,
            from_did=active.from_did,
            port=port,
            settings=settings,
        )
        receipt = asyncio.run(run_kickoff(session, book_parsed.request))
        rows = _read_jsonl(
            artifacts / "linq" / BOOK_EVENT_ID / "replies.jsonl"
        )
        ack_row = rows[0] if rows else {}
        result_row = rows[1] if len(rows) > 1 else {}
        expected_path = f"/chats/{INBOUND_CHAT_ID}/messages"
        reply_ok = (
            len(port.sent) == 2
            and all(path == expected_path for path, _ in port.sent)
            and all(
                "from" not in payload and "to" not in payload
                for _, payload in port.sent
            )
            and all(
                payload.get("message", {}).get("preferred_service") == IMESSAGE
                for _, payload in port.sent
            )
            and "SMS" not in json.dumps(port.sent)
            and ack_row.get("kind") == "ack"
            and result_row.get("kind") == "result"
            and str(result_row.get("text", "")).startswith("Booked:")
            and ack_row.get("chat_id") == INBOUND_CHAT_ID
            and result_row.get("chat_id") == INBOUND_CHAT_ID
            and receipt.result is not None
            and receipt.result.status == "booked"
        )
        reply_detail = (
            f"sends={len(port.sent)} ack={ack_row.get('kind')} "
            f"result={result_row.get('kind')} "
            f"status={getattr(receipt.result, 'status', None)}"
        )

        call_dir = artifacts / "stub-call-0001"
        report = _read_json(call_dir / "call_report.json")
        result_artifact = _read_json(call_dir / "result.json")
        hangup = call_dir / "hangup.json"
        call_ok = (
            result_artifact.get("status") == "booked"
            and hangup.is_file()
            and report.get("hangup", {}).get("path") == "trail"
            and receipt.call_control_id == "stub-call-0001"
        )
        call_detail = (
            f"status={result_artifact.get('status')} "
            f"hangup={report.get('hangup', {}).get('path')}"
        )

        call_link = _read_json(
            artifacts / "linq" / BOOK_EVENT_ID / "call.json"
        )
        linq_link = _read_json(call_dir / "linq.json")
        result_ref = artifacts / str(call_link.get("result_ref") or "")
        link_ok = (
            call_link.get("call_control_id") == receipt.call_control_id
            and result_ref.is_file()
            and linq_link.get("event_id") == BOOK_EVENT_ID
            and linq_link.get("chat_id") == INBOUND_CHAT_ID
            and linq_link.get("linq_ref") == f"linq/{BOOK_EVENT_ID}"
        )
        link_detail = (
            f"call.json ccid={call_link.get('call_control_id')} "
            f"linq.json event_id={linq_link.get('event_id')}"
        )

        dial = _read_json(call_dir / "dial.json")
        webhook_host = normalized_public_host(str(dial.get("webhook_url") or ""))
        stream_host = normalized_public_host(str(dial.get("stream_url") or ""))
        host_ok = (
            webhook_host == stream_host == "stub.invalid"
        )
        host_detail = (
            f"webhook_host={webhook_host} stream_host={stream_host}"
        )
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        if not reply_ok:
            reply_detail = str(error)
        elif not call_ok:
            call_detail = str(error)
        elif not link_ok:
            link_detail = str(error)
        else:
            host_detail = str(error)

    checks = (
        book_check,
        ask_check,
        fail_check,
        LinqCallVerifyCheck(
            name="kickoff_reply", passed=reply_ok, detail=reply_detail
        ),
        LinqCallVerifyCheck(
            name="call_ran", passed=call_ok, detail=call_detail
        ),
        LinqCallVerifyCheck(
            name="cross_link", passed=link_ok, detail=link_detail
        ),
        LinqCallVerifyCheck(
            name="same_host_dial", passed=host_ok, detail=host_detail
        ),
    )
    return LinqCallVerifyReport(
        passed=all(check.passed for check in checks),
        checks=checks,
    )


def format_linq_call_report(report: LinqCallVerifyReport) -> str:
    lines = [
        (
            f"[{'PASS' if check.passed else 'FAIL'}] "
            f"{check.name:<18} {check.detail}"
        )
        for check in report.checks
    ]
    passed = sum(check.passed for check in report.checks)
    lines.append(f"{passed}/7 passed")
    return "\n".join(lines)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
