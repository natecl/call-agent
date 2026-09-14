from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .accounts import AccountWrite, UserRecord, open_accounts
from .config import Settings
from .domain import (
    BookRequest,
    ClientState,
    E164,
    FirstName,
    Mode,
    Question,
    QuestionType,
    QuickAskRequest,
    UserId,
)
from .kickoff import (
    CONFIRM_ASK_TEXT,
    DIAL_NOW_TEXT,
    NO_ACCOUNT_TEXT,
    NO_NAME_TEXT,
    PARTY_AND_TIME_ASK_TEXT,
    PHONE_ASK_TEXT,
    TIME_ASK_TEXT,
    KickoffBlocked,
    KickoffCollecting,
    KickoffFailed,
    KickoffParsed,
    KickoffSession,
    failed_reply_text,
    intake_kickoff,
    make_session,
    parse_kickoff,
    run_kickoff,
)
from .ledger import open_ledger
from .linq import StubLinq, parse_inbound
from .messaging import IMESSAGE
from .runtime import normalized_public_host
from .user_text import contains_banned_user_text
from .verify_message import (
    INBOUND_CHAT_ID,
    INBOUND_SENDER,
)

NATURAL_BOOK_TEXT = (
    "can you make a reservation with this restaurant at 7:30 "
    "for a party of two?"
)
POKE_PALACE_BOOK_TEXT = "can you make a reservation at poke palace for me?"
PARTY_FOLLOWUP_TEXT = "2 people"
TIME_FOLLOWUP_TEXT = "7:30"
BOOK_KICKOFF_TEXT = NATURAL_BOOK_TEXT
ONESHOT_BOOK_TEXT = (
    "book +14155552671 party 2 at Friday 7pm name Nathan "
    "notes outdoor if possible"
)
ASK_KICKOFF_TEXT = "ask +14155552671 hours name Nathan"
BAD_FOLLOWUP_TEXT = "nope"
PHONE_FOLLOWUP_TEXT = "+14155552671"
APPROVE_FOLLOWUP_TEXT = "approve"
BOOK_EVENT_ID = "kickoff-book-0001"
BAD_EVENT_ID = "kickoff-bad-0001"
STUB_FROM = E164("+15555550100")
SHARED_FROM_DID = E164("+12135690899")


class LinqCallVerifyInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    from_did: E164 = STUB_FROM
    to: E164 = E164("+14155552671")
    text: str = NATURAL_BOOK_TEXT


CheckName = Literal[
    "parse_book",
    "collect_time",
    "collect_phone",
    "confirm_before_dial",
    "parse_quick_ask",
    "parse_fail_loud",
    "account_lookup",
    "account_gate",
    "kickoff_reply",
    "call_ran",
    "cross_link",
    "same_host_dial",
    "shared_from_did",
    "no_number_order",
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
        LinqCallVerifyCheck,
        LinqCallVerifyCheck,
        LinqCallVerifyCheck,
        LinqCallVerifyCheck,
        LinqCallVerifyCheck,
        LinqCallVerifyCheck,
        LinqCallVerifyCheck,
    ]


def kickoff_inbound_bytes(
    text: str,
    event_id: str,
    *,
    message_id: str = "kickoff-msg-0001",
) -> bytes:
    return json.dumps(
        {
            "api_version": "v3",
            "webhook_version": "2026-02-03",
            "event_type": "message.received",
            "event_id": event_id,
            "data": {
                "chat": {"id": INBOUND_CHAT_ID},
                "id": message_id,
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
        telnyx_from_number=str(SHARED_FROM_DID),
        call_agent_db_path=root / "ledger.sqlite3",
        call_agent_artifacts_dir=artifacts,
        hangup_trailing_silence_s=0,
        public_base_url="https://stub.invalid",
        quick_ask_live=False,
        retain_call_artifacts=True,
    )
    nathan = open_accounts(settings).upsert(
        AccountWrite(
            phone=E164(INBOUND_SENDER),
            first_name="Nathan",
            user_id=UserId("acctnathan"),
            minutes_included=30,
        )
    ).record

    book_parsed = intake_kickoff(active.text, settings, account=nathan)
    collect_ok = (
        isinstance(book_parsed, KickoffCollecting)
        and book_parsed.verb == "book"
        and book_parsed.reply == PHONE_ASK_TEXT
        and book_parsed.pending.waiting_for == "to"
        and book_parsed.pending.party_size == 2
        and book_parsed.pending.time_text == "7:30"
        and book_parsed.pending.to is None
        and not (artifacts / "stub-call-0001").exists()
    )
    book_check = LinqCallVerifyCheck(
        name="parse_book",
        passed=collect_ok,
        detail=(
            f"kind={type(book_parsed).__name__} "
            f"waiting={getattr(getattr(book_parsed, 'pending', None), 'waiting_for', None)} "
            f"party={getattr(getattr(book_parsed, 'pending', None), 'party_size', None)} "
            f"time={getattr(getattr(book_parsed, 'pending', None), 'time_text', None)}"
        ),
    )

    poke_first = intake_kickoff(POKE_PALACE_BOOK_TEXT, settings, account=nathan)
    party_follow = None
    time_follow = None
    collect_time_ok = (
        isinstance(poke_first, KickoffCollecting)
        and poke_first.verb == "book"
        and poke_first.reply == PARTY_AND_TIME_ASK_TEXT
        and poke_first.pending.waiting_for == "party_and_time"
        and poke_first.pending.party_size is None
        and poke_first.pending.time_text is None
        and poke_first.reply != PHONE_ASK_TEXT
        and poke_first.reply != TIME_ASK_TEXT
        and not (artifacts / "stub-call-0001").exists()
    )
    if collect_time_ok and isinstance(poke_first, KickoffCollecting):
        party_follow = intake_kickoff(
            PARTY_FOLLOWUP_TEXT,
            settings,
            pending=poke_first.pending,
            account=nathan,
        )
        collect_time_ok = (
            isinstance(party_follow, KickoffCollecting)
            and party_follow.verb == "book"
            and party_follow.reply == TIME_ASK_TEXT
            and party_follow.pending.waiting_for == "time"
            and party_follow.pending.party_size == 2
            and party_follow.pending.time_text is None
            and party_follow.reply != PHONE_ASK_TEXT
            and not (artifacts / "stub-call-0001").exists()
        )
    if collect_time_ok and isinstance(party_follow, KickoffCollecting):
        time_follow = intake_kickoff(
            TIME_FOLLOWUP_TEXT,
            settings,
            pending=party_follow.pending,
            account=nathan,
        )
        collect_time_ok = (
            isinstance(time_follow, KickoffCollecting)
            and time_follow.verb == "book"
            and time_follow.reply == PHONE_ASK_TEXT
            and time_follow.pending.waiting_for == "to"
            and time_follow.pending.party_size == 2
            and time_follow.pending.time_text == "7:30"
            and not (artifacts / "stub-call-0001").exists()
        )
    time_check = LinqCallVerifyCheck(
        name="collect_time",
        passed=collect_time_ok,
        detail=(
            f"first={getattr(getattr(poke_first, 'pending', None), 'waiting_for', None)} "
            f"time={getattr(getattr(poke_first, 'pending', None), 'time_text', None)} "
            f"party={getattr(getattr(party_follow, 'pending', None), 'waiting_for', None)} "
            f"after_time={getattr(getattr(time_follow, 'pending', None), 'waiting_for', None)}"
        ),
    )

    expected_book = BookRequest(
        user_id=nathan.user_id,
        to=active.to,
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
        party_size=2,
        time_text="7:30",
    )
    phone_parsed = None
    phone_ok = False
    if isinstance(book_parsed, KickoffCollecting):
        phone_parsed = intake_kickoff(
            str(active.to), settings, pending=book_parsed.pending, account=nathan
        )
        phone_ok = (
            isinstance(phone_parsed, KickoffCollecting)
            and phone_parsed.verb == "book"
            and phone_parsed.reply == CONFIRM_ASK_TEXT
            and phone_parsed.pending.waiting_for == "confirm"
            and phone_parsed.pending.to == str(active.to)
            and phone_parsed.pending.party_size == 2
            and phone_parsed.pending.time_text == "7:30"
            and not (artifacts / "stub-call-0001").exists()
        )
    phone_check = LinqCallVerifyCheck(
        name="collect_phone",
        passed=phone_ok,
        detail=(
            f"kind={type(phone_parsed).__name__} "
            f"waiting={getattr(getattr(phone_parsed, 'pending', None), 'waiting_for', None)} "
            f"to={getattr(getattr(phone_parsed, 'pending', None), 'to', None)}"
        ),
    )

    confirm_parsed = None
    confirm_ok = False
    confirm_detail = "confirm gate did not run"
    try:
        oneshot = parse_kickoff(ONESHOT_BOOK_TEXT, settings, account=nathan)
        if isinstance(oneshot, KickoffParsed):
            raise ValueError("first message one-shot dialed")
        if (
            not isinstance(phone_parsed, KickoffCollecting)
            or phone_parsed.pending.waiting_for != "confirm"
        ):
            raise ValueError("phone follow-up did not wait for confirm")
        if (artifacts / "stub-call-0001").exists():
            raise ValueError("dial happened before confirm")
        bad_confirm = intake_kickoff(
            BAD_FOLLOWUP_TEXT,
            settings,
            pending=phone_parsed.pending,
            account=nathan,
        )
        if not isinstance(bad_confirm, KickoffFailed):
            raise ValueError("non-affirmative confirm did not fail")
        confirm_parsed = intake_kickoff(
            APPROVE_FOLLOWUP_TEXT,
            settings,
            pending=phone_parsed.pending,
            account=nathan,
        )
        yes_parsed = intake_kickoff(
            "yes", settings, pending=phone_parsed.pending, account=nathan
        )
        i_confirm_parsed = intake_kickoff(
            "I confirm", settings, pending=phone_parsed.pending, account=nathan
        )
        confirm_ok = (
            isinstance(oneshot, KickoffCollecting)
            and oneshot.pending.waiting_for == "confirm"
            and oneshot.reply == CONFIRM_ASK_TEXT
            and not isinstance(oneshot, KickoffParsed)
            and not contains_banned_user_text(
                failed_reply_text(bad_confirm.reason)
            )
            and "confirm" in bad_confirm.reason
            and isinstance(confirm_parsed, KickoffParsed)
            and confirm_parsed.verb == "book"
            and confirm_parsed.request == expected_book
            and isinstance(yes_parsed, KickoffParsed)
            and yes_parsed.request == expected_book
            and isinstance(i_confirm_parsed, KickoffParsed)
            and i_confirm_parsed.request == expected_book
            and not (artifacts / "stub-call-0001").exists()
        )
        confirm_detail = (
            f"oneshot={type(oneshot).__name__} "
            f"waiting={getattr(getattr(oneshot, 'pending', None), 'waiting_for', None)} "
            f"approve={type(confirm_parsed).__name__} "
            f"dial_before={ (artifacts / 'stub-call-0001').exists() }"
        )
    except (OSError, ValueError, TypeError) as error:
        confirm_ok = False
        confirm_detail = str(error)
    confirm_check = LinqCallVerifyCheck(
        name="confirm_before_dial",
        passed=confirm_ok,
        detail=confirm_detail,
    )

    ask_first = parse_kickoff(ASK_KICKOFF_TEXT, settings, account=nathan)
    expected_ask = QuickAskRequest(
        user_id=nathan.user_id,
        to=active.to,
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
    )
    ask_parsed = None
    ask_ok = (
        isinstance(ask_first, KickoffCollecting)
        and ask_first.verb == "ask"
        and ask_first.reply == CONFIRM_ASK_TEXT
        and ask_first.pending.waiting_for == "confirm"
        and ask_first.pending.question_kind == "hours"
        and ask_first.pending.to == str(active.to)
        and not isinstance(ask_first, KickoffParsed)
        and not (artifacts / "stub-call-0001").exists()
    )
    if ask_ok and isinstance(ask_first, KickoffCollecting):
        ask_parsed = intake_kickoff(
            APPROVE_FOLLOWUP_TEXT,
            settings,
            pending=ask_first.pending,
            account=nathan,
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
            f"first={type(ask_first).__name__} "
            f"waiting={getattr(getattr(ask_first, 'pending', None), 'waiting_for', None)} "
            f"verb={getattr(ask_parsed, 'verb', None)} "
            f"kind={getattr(getattr(getattr(ask_parsed, 'request', None), 'question', None), 'kind', None)}"
        ),
    )

    fail_ok = False
    fail_detail = "malformed parse did not run"
    try:
        if not isinstance(book_parsed, KickoffCollecting):
            raise ValueError("expected first text to collect the phone")
        fail_parsed = intake_kickoff(
            BAD_FOLLOWUP_TEXT,
            settings,
            pending=book_parsed.pending,
            account=nathan,
        )
        fail_inbound = parse_inbound(
            kickoff_inbound_bytes(BAD_FOLLOWUP_TEXT, BAD_EVENT_ID)
        )
        if not isinstance(fail_parsed, KickoffFailed) or fail_inbound is None:
            raise ValueError("expected KickoffFailed")
        fail_port = StubLinq()
        fail_session = make_session(fail_inbound, settings, fail_port)
        help_text = failed_reply_text(fail_parsed.reason)
        fail_session.text(help_text, kind="error")
        kickoff_marker = (
            artifacts / "linq" / BAD_EVENT_ID / "kickoff.json"
        )
        fail_ok = (
            not contains_banned_user_text(help_text)
            and "phone number" in fail_parsed.reason
            and "phone number" in help_text
            and not kickoff_marker.exists()
            and not (artifacts / "stub-call-0001").exists()
            and fail_port.last_path == f"/chats/{INBOUND_CHAT_ID}/messages"
        )
        fail_detail = (
            f"reason={fail_parsed.reason} kickoff_json="
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

    unnamed = UserRecord(
        user_id=UserId("noname"),
        phone=E164("+15555550199"),
        first_name=None,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    missing_did = UserRecord(
        user_id=UserId("nofromdid"),
        phone=E164("+15555550198"),
        first_name="Nathan",
        from_did=None,
        minutes_included=30,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    no_account = intake_kickoff(active.text, settings, account=None)
    no_name = intake_kickoff(active.text, settings, account=unnamed)
    no_from_did = intake_kickoff(active.text, settings, account=missing_did)
    gate_ok = (
        isinstance(no_account, KickoffBlocked)
        and no_account.reply == NO_ACCOUNT_TEXT
        and not contains_banned_user_text(no_account.reply)
        and isinstance(no_name, KickoffBlocked)
        and no_name.reply == NO_NAME_TEXT
        and not contains_banned_user_text(no_name.reply)
        and isinstance(no_from_did, KickoffCollecting)
        and no_from_did.verb == "book"
        and "missing caller name" not in no_account.reply
        and "missing caller name" not in no_name.reply
        and not (artifacts / "stub-call-0001").exists()
    )
    gate_check = LinqCallVerifyCheck(
        name="account_gate",
        passed=gate_ok,
        detail=(
            f"none={type(no_account).__name__} "
            f"unnamed={type(no_name).__name__} "
            f"no_from_did={type(no_from_did).__name__}"
        ),
    )

    inbound = parse_inbound(
        kickoff_inbound_bytes(
            APPROVE_FOLLOWUP_TEXT,
            BOOK_EVENT_ID,
            message_id="kickoff-msg-approve",
        )
    )
    reply_ok = False
    call_ok = False
    link_ok = False
    host_ok = False
    lookup_ok = False
    from_did_ok = False
    no_order_ok = False
    reply_detail = "kickoff did not run"
    call_detail = "call did not run"
    link_detail = "cross-link did not run"
    host_detail = "dial did not run"
    lookup_detail = "account lookup did not run"
    from_did_detail = "from-DID check did not run"
    no_order_detail = "no-order check did not run"
    try:
        if inbound is None or not isinstance(confirm_parsed, KickoffParsed):
            raise ValueError("confirm follow-up did not parse")
        port = StubLinq()
        session = KickoffSession(
            inbound=inbound,
            from_did=active.from_did,
            port=port,
            settings=settings,
        )
        receipt = asyncio.run(
            run_kickoff(
                session,
                confirm_parsed.request,
                from_did=confirm_parsed.from_did,
            )
        )
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
            and ack_row.get("text") == DIAL_NOW_TEXT
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
            and report.get("hangup", {}).get("path") == "tool"
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

        kickoff_payload = _read_json(
            artifacts / "linq" / BOOK_EVENT_ID / "kickoff.json"
        )
        account_blob = kickoff_payload.get("account") or {}
        lookup_ok = (
            isinstance(confirm_parsed, KickoffParsed)
            and confirm_parsed.request.caller == FirstName("Nathan")
            and confirm_parsed.request.user_id == nathan.user_id
            and account_blob.get("user_id") == str(nathan.user_id)
            and account_blob.get("phone") == INBOUND_SENDER
            and account_blob.get("from_did") is None
            and kickoff_payload.get("dial_from") == str(SHARED_FROM_DID)
            and "first_name" not in account_blob
            and "name" not in account_blob
        )
        lookup_detail = (
            f"caller={confirm_parsed.request.caller} "
            f"request_user={confirm_parsed.request.user_id} "
            f"account_user={account_blob.get('user_id')} "
            f"dial_from={kickoff_payload.get('dial_from')}"
        )

        client_state = ClientState.decode(str(dial.get("client_state") or ""))
        from_did_ok = (
            dial.get("from") == str(SHARED_FROM_DID)
            and dial.get("from") != INBOUND_SENDER
            and dial.get("from") != str(active.from_did)
            and confirm_parsed.from_did == SHARED_FROM_DID
            and nathan.from_did is None
            and client_state.user_id == nathan.user_id
        )
        from_did_detail = (
            f"dial_from={dial.get('from')} "
            f"linq_line={active.from_did} "
            f"sender={INBOUND_SENDER} "
            f"client_user={client_state.user_id}"
        )

        source = "".join(
            (Path(__file__).resolve().parent / name).read_text()
            for name in ("runtime.py", "kickoff.py", "server.py")
        )
        ledger_rows = open_ledger(settings).list()
        no_order_ok = (
            ledger_rows == []
            and "provision(" not in source
            and "search_and_order_number" not in source
            and "number_orders" not in source
            and "open_ledger" not in source
        )
        no_order_detail = (
            f"dids={len(ledger_rows)} "
            f"provision={'provision(' in source}"
        )
    except (OSError, ValueError, TypeError, KeyError, IndexError) as error:
        if not reply_ok:
            reply_detail = str(error)
        elif not call_ok:
            call_detail = str(error)
        elif not link_ok:
            link_detail = str(error)
        elif not host_ok:
            host_detail = str(error)
        elif not lookup_ok:
            lookup_detail = str(error)
        elif not from_did_ok:
            from_did_detail = str(error)
        else:
            no_order_detail = str(error)

    checks = (
        book_check,
        time_check,
        phone_check,
        confirm_check,
        ask_check,
        fail_check,
        LinqCallVerifyCheck(
            name="account_lookup", passed=lookup_ok, detail=lookup_detail
        ),
        gate_check,
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
        LinqCallVerifyCheck(
            name="shared_from_did",
            passed=from_did_ok,
            detail=from_did_detail,
        ),
        LinqCallVerifyCheck(
            name="no_number_order",
            passed=no_order_ok,
            detail=no_order_detail,
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
    lines.append(f"{passed}/{len(report.checks)} passed")
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
