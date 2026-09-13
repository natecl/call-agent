from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .accounts import UserRecord, lookup_account
from .config import Settings
from .domain import (
    BookRequest,
    CallResult,
    E164,
    FirstName,
    Mode,
    Question,
    QuestionType,
    QuickAskRequest,
    UserId,
    exception_reason,
)
from .linq import (
    claim_path,
    linq_artifact_dir,
    outbound_path,
    reply_from_did,
    write_inbound_artifact,
)
from .messaging import (
    IMESSAGE,
    ChatId,
    InboundMessage,
    MessagingPort,
    SendReceipt,
    followup_chat,
)
from .persistence import PersistenceConfigError
from .runtime import normalized_public_host, run_call

PHONE_ASK_TEXT = "okay, sounds good. What's the restaurant's phone number?"
TIME_ASK_TEXT = "okay, sounds good. What time should I book?"
PARTY_ASK_TEXT = "okay, sounds good. How many people?"
PARTY_AND_TIME_ASK_TEXT = (
    "okay, sounds good. What time, and for how many people?"
)
KIND_ASK_TEXT = "okay, sounds good. What should I ask them?"
ITEM_ASK_TEXT = "okay, sounds good. Which item?"
CONFIRM_ASK_TEXT = (
    'Okay, I\'m going to call this number. Do you confirm? Reply "approve"'
)
DIAL_NOW_TEXT = "Okay, I'm going to call them now."
NO_ACCOUNT_TEXT = (
    "I don't have an account for this number yet. "
    "Set one up in your account settings, then text me again."
)
NO_NAME_TEXT = (
    "I need your name for the call. "
    "Add it in your account settings, then text me again."
)
NO_FROM_DID_TEXT = (
    "This account doesn't have a Treena calling number assigned yet. "
    "Ask support to assign one, then text me again."
)

_BOOK_RE = re.compile(
    r"^book\s+(?P<to>\+[1-9]\d{7,14})\s+party\s+(?P<party>\d+)\s+at\s+"
    r"(?P<time>.+?)(?:\s+name\s+[A-Za-z][A-Za-z' -]{0,31})?"
    r"(?:\s+notes\s+(?P<notes>.+))?$",
    re.IGNORECASE,
)
_ASK_HOURS_RE = re.compile(
    r"^ask\s+(?P<to>\+[1-9]\d{7,14})\s+hours"
    r"(?:\s+name\s+[A-Za-z][A-Za-z' -]{0,31})?$",
    re.IGNORECASE,
)
_ASK_ITEM_RE = re.compile(
    r"^ask\s+(?P<to>\+[1-9]\d{7,14})\s+(?P<kind>stock|aisle)\s+"
    r"(?P<item>.+?)(?:\s+name\s+[A-Za-z][A-Za-z' -]{0,31})?$",
    re.IGNORECASE,
)
_E164_RE = re.compile(r"\+[1-9]\d{7,14}")
_US_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s.-]*)?\(?([2-9]\d{2})\)?[\s.-]*([2-9]\d{2})[\s.-]*(\d{4})(?!\d)"
)
_PARTY_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_PARTY_TOKEN = r"\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
_PARTY_RE = re.compile(
    r"\b(?:party\s+(?:of\s+)?|table\s+for\s+)(" + _PARTY_TOKEN + r")\b",
    re.IGNORECASE,
)
_FOR_PEOPLE_RE = re.compile(
    r"\bfor\s+(" + _PARTY_TOKEN + r")\s+(?:people|guests|persons?)\b",
    re.IGNORECASE,
)
_BARE_PARTY_RE = re.compile(
    r"^\s*(" + _PARTY_TOKEN + r")\s*(?:people|guests|persons?)?\s*$",
    re.IGNORECASE,
)
_DAY_WORD = (
    r"today|tonight|tomorrow|"
    r"this\s+(?:morning|afternoon|evening)|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
)
_CLOCK = (
    r"(?:noon|midnight|midday)"
    r"|(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s*(?:a\.?m\.?|p\.?m\.?)"
    r"|(?:[01]?\d|2[0-3]):[0-5]\d"
    r"|(?:1[0-2]|0?[1-9])\s*o'?clock"
)
_TIME_LIKE_RE = re.compile(
    rf"(?:at\s+)?(?:(?P<day>{_DAY_WORD})\s+)?(?:at\s+)?(?P<clock>{_CLOCK})",
    re.IGNORECASE,
)
_AT_BARE_HOUR_RE = re.compile(
    r"\bat\s+(?P<clock>(?:1[0-2]|0?[1-9]|2[0-3]))\b(?!:)",
    re.IGNORECASE,
)
_NOTES_RE = re.compile(r"\bnotes\s+(.+)$", re.IGNORECASE)
_BOOK_INTENT_RE = re.compile(
    r"\b(reservations?|reserve|book(?:ing)?s?|table|party)\b",
    re.IGNORECASE,
)
_ASK_INTENT_RE = re.compile(
    r"\b(hours|stock|aisle|open)\b",
    re.IGNORECASE,
)

ReplyKind = Literal["ack", "result", "error"]
WaitingFor = Literal[
    "party", "time", "party_and_time", "to", "kind", "item", "confirm"
]
_AFFIRM_RE = re.compile(
    r"\b(?:i\s+)?(?:confirm|approve|yes|confirmed|approved)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class KickoffParsed:
    """Confirmed book/ask ready to dial.

    `from_did` is the Telnyx caller ID from the users row. Not
    `KickoffSession.from_did`, which is the Linq iMessage line.
    """

    request: BookRequest | QuickAskRequest
    verb: Literal["book", "ask"]
    from_did: E164


@dataclass(frozen=True, slots=True)
class PendingCollect:
    verb: Literal["book", "ask"]
    waiting_for: WaitingFor
    to: str | None = None
    party_size: int | None = None
    time_text: str | None = None
    constraints: str | None = None
    question_kind: str | None = None
    question_item: str | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> PendingCollect:
        party = raw.get("party_size")
        return cls(
            verb="ask" if raw.get("verb") == "ask" else "book",
            waiting_for=_waiting_for_value(raw.get("waiting_for")),
            to=_optional_str(raw.get("to")),
            party_size=party if isinstance(party, int) else None,
            time_text=_optional_str(raw.get("time_text")),
            constraints=_optional_str(raw.get("constraints")),
            question_kind=_optional_str(raw.get("question_kind")),
            question_item=_optional_str(raw.get("question_item")),
        )


@dataclass(frozen=True, slots=True)
class KickoffCollecting:
    reply: str
    pending: PendingCollect
    verb: Literal["book", "ask"]


@dataclass(frozen=True, slots=True)
class KickoffFailed:
    reason: str
    verb: Literal["book", "ask"] | None = None


@dataclass(frozen=True, slots=True)
class KickoffBlocked:
    reply: str
    verb: Literal["book", "ask"] | None = None


@dataclass(frozen=True, slots=True)
class KickoffReceipt:
    request: BookRequest | QuickAskRequest
    result: CallResult | None
    ack_receipt: SendReceipt
    result_receipt: SendReceipt | None
    call_control_id: str
    error: str | None = None


@dataclass(slots=True)
class KickoffSession:
    """Linq chat for one inbound event. L1 only texts; ask is reserved."""

    inbound: InboundMessage
    from_did: E164
    port: MessagingPort
    settings: Settings
    _seq: int = field(default=0, init=False)

    def text(
        self, reply: str, *, kind: ReplyKind = "result"
    ) -> SendReceipt:
        outbound = followup_chat(self.from_did, self.inbound, reply)
        sent_at = datetime.now(UTC)
        receipt = self.port.send(outbound)
        self._seq += 1
        path = linq_artifact_dir(
            self.settings.call_agent_artifacts_dir, self.inbound.event_id
        )
        row = {
            "seq": self._seq,
            "kind": kind,
            "text": reply,
            "chat_id": str(receipt.chat_id),
            "message_id": str(receipt.message_id),
            "outbound_path": outbound_path(outbound),
            "preferred_service": IMESSAGE,
            "sent_at": sent_at.isoformat(),
        }
        with (path / "replies.jsonl").open("a") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        return receipt

    async def ask(self, prompt: str) -> str:
        raise NotImplementedError(
            "mid-call chat is not shipped; L1 texts "
            "Call failed: instead of pausing the live call"
        )


@dataclass(frozen=True, slots=True)
class _Extracted:
    intent: Literal["book", "ask"] | None
    to: E164 | None
    party_size: int | None
    time_text: str | None
    constraints: str | None
    question_kind: QuestionType | None
    question_item: str | None


def collect_reply_text(waiting_for: WaitingFor) -> str:
    if waiting_for == "to":
        return PHONE_ASK_TEXT
    if waiting_for == "time":
        return TIME_ASK_TEXT
    if waiting_for == "party":
        return PARTY_ASK_TEXT
    if waiting_for == "party_and_time":
        return PARTY_AND_TIME_ASK_TEXT
    if waiting_for == "item":
        return ITEM_ASK_TEXT
    if waiting_for == "confirm":
        return CONFIRM_ASK_TEXT
    return KIND_ASK_TEXT


def failed_reply_text(reason: str) -> str:
    text = reason.strip() or "unknown"
    return f"Call failed: {text}"


def parse_kickoff(
    text: str,
    settings: Settings,
    sender: str | None = None,
    *,
    account: UserRecord | None = None,
) -> KickoffParsed | KickoffCollecting | KickoffFailed | KickoffBlocked | None:
    if account is None and sender is not None:
        account = lookup_account(settings, sender)
    return intake_kickoff(text, settings, account=account)


def intake_kickoff(
    text: str,
    settings: Settings,
    pending: PendingCollect | None = None,
    *,
    account: UserRecord | None = None,
) -> KickoffParsed | KickoffCollecting | KickoffFailed | KickoffBlocked | None:
    normalized = " ".join(text.split())
    if not normalized:
        if pending is None:
            return None
        return KickoffFailed("I didn't catch that.", pending.verb)
    extracted = _extract_fields(
        normalized, waiting_for=None if pending is None else pending.waiting_for
    )
    if pending is None:
        if extracted.intent is None:
            return None
        return _decide(extracted, settings, verb=extracted.intent, account=account)
    if pending.waiting_for == "confirm":
        if not _is_confirm_affirmative(normalized):
            return KickoffFailed(
                "I need you to confirm before I call.",
                pending.verb,
            )
        merged = _merge(pending, extracted)
        return _decide(
            merged, settings, verb=pending.verb, confirmed=True, account=account
        )
    if not _progressed(pending, extracted):
        return KickoffFailed(
            "I need the restaurant's phone number."
            if pending.waiting_for == "to"
            else "I need a bit more to place the call.",
            pending.verb,
        )
    merged = _merge(pending, extracted)
    return _decide(merged, settings, verb=pending.verb, account=account)


def kickoff_ack_text(request: BookRequest | QuickAskRequest) -> str:
    del request
    return DIAL_NOW_TEXT


def result_reply_text(result: CallResult, to: E164) -> str:
    if result.status == "booked":
        return f"Booked: {result.confirmation_text}"
    if result.status == "answered":
        return f"Answer: {result.answer_text}"
    if result.status == "declined":
        return "They declined."
    if result.status == "no_answer":
        return f"No answer at {to}."
    reason = (result.reason or "").strip() or "unknown"
    return f"Call failed: {reason}"


def kickoff_host_mismatch(
    settings: Settings, host_header: str | None
) -> str | None:
    if not settings.quick_ask_live:
        return None
    expected = normalized_public_host(settings.public_base_url)
    host = (host_header or "").strip()
    actual = (
        normalized_public_host(f"https://{host}") if host else ""
    )
    if expected == actual:
        return None
    return (
        "Call not placed: PUBLIC_BASE_URL host "
        f"{expected} != this host {actual}"
    )


def kickoff_json_path(root: Path, event_id: str) -> Path:
    return root / "linq" / event_id / "kickoff.json"


def pending_collect_path(root: Path, chat_id: ChatId | str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(chat_id)).strip("._") or "chat"
    return root / "linq" / "pending" / f"{safe}.json"


def load_pending_collect(
    root: Path, chat_id: ChatId | str
) -> PendingCollect | None:
    _require_artifacts_root(root)
    path = pending_collect_path(root, chat_id)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        return None
    return PendingCollect.from_dict(raw)


def save_pending_collect(
    root: Path,
    chat_id: ChatId | str,
    pending: PendingCollect,
    *,
    inbound: InboundMessage | None = None,
) -> Path:
    _require_artifacts_root(root)
    path = pending_collect_path(root, chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = pending.to_dict()
    payload["chat_id"] = str(chat_id)
    if inbound is not None:
        payload["event_id"] = str(inbound.event_id)
        payload["message_id"] = str(inbound.message_id)
        payload["sender"] = str(inbound.sender)
    payload["saved_at"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def clear_pending_collect(root: Path, chat_id: ChatId | str) -> None:
    path = pending_collect_path(root, chat_id)
    if path.is_file():
        path.unlink()


def claim_collect(
    settings: Settings,
    inbound: InboundMessage,
    pending: PendingCollect,
) -> bool:
    path = linq_artifact_dir(
        settings.call_agent_artifacts_dir, inbound.event_id
    ) / "collect.json"
    payload = {
        "event_id": str(inbound.event_id),
        "chat_id": str(inbound.chat_id),
        "message_id": str(inbound.message_id),
        "sender": str(inbound.sender),
        "verb": pending.verb,
        "waiting_for": pending.waiting_for,
        "pending": pending.to_dict(),
        "parsed_at": datetime.now(UTC).isoformat(),
    }
    return claim_path(path, payload)


def claim_kickoff(
    settings: Settings,
    inbound: InboundMessage,
    request: BookRequest | QuickAskRequest,
) -> bool:
    verb: Literal["book", "ask"] = (
        "book" if isinstance(request, BookRequest) else "ask"
    )
    path = linq_artifact_dir(
        settings.call_agent_artifacts_dir, inbound.event_id
    ) / "kickoff.json"
    account = lookup_account(settings, inbound.sender)
    payload = {
        "event_id": str(inbound.event_id),
        "chat_id": str(inbound.chat_id),
        "message_id": str(inbound.message_id),
        "sender": str(inbound.sender),
        "verb": verb,
        "request": request.model_dump(mode="json"),
        "mode": request.mode.value,
        "account": (
            {
                "user_id": str(account.user_id),
                "phone": str(account.phone),
                "from_did": (
                    str(account.from_did) if account.from_did else None
                ),
                "minutes_included": account.minutes_included,
                "minutes_used": account.minutes_used,
            }
            if account is not None
            else None
        ),
        "parsed_at": datetime.now(UTC).isoformat(),
    }
    return claim_path(path, payload)


def write_call_cross_links(
    settings: Settings,
    inbound: InboundMessage,
    result: CallResult | None,
    result_receipt: SendReceipt | None,
    result_text: str,
    error: str | None = None,
) -> None:
    call_control_id = (
        result.call_control_id if result is not None else ""
    )
    status = result.status if result is not None else "failed"
    result_ref = (
        f"{call_control_id}/result.json" if call_control_id else None
    )
    call_report_ref = (
        f"{call_control_id}/call_report.json" if call_control_id else None
    )
    reply_payload = None
    if result_receipt is not None:
        reply_payload = {
            "chat_id": str(result_receipt.chat_id),
            "message_id": str(result_receipt.message_id),
            "text": result_text,
        }
    linq_dir = linq_artifact_dir(
        settings.call_agent_artifacts_dir, inbound.event_id
    )
    call_payload = {
        "call_control_id": call_control_id,
        "status": status,
        "result": (
            result.model_dump(mode="json") if result is not None else None
        ),
        "result_ref": result_ref,
        "call_report_ref": call_report_ref,
        "result_reply": reply_payload,
        "error": error,
    }
    (linq_dir / "call.json").write_text(
        json.dumps(call_payload, indent=2, sort_keys=True) + "\n"
    )
    if not call_control_id:
        return
    call_dir = settings.call_agent_artifacts_dir / call_control_id
    if not call_dir.is_dir():
        return
    (call_dir / "linq.json").write_text(
        json.dumps(
            {
                "event_id": str(inbound.event_id),
                "chat_id": str(inbound.chat_id),
                "message_id": str(inbound.message_id),
                "sender": str(inbound.sender),
                "linq_ref": f"linq/{inbound.event_id}",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


async def run_kickoff(
    session: KickoffSession,
    request: BookRequest | QuickAskRequest,
    *,
    from_did: E164,
) -> KickoffReceipt:
    write_inbound_artifact(
        session.settings.call_agent_artifacts_dir,
        session.inbound,
        received_at=datetime.now(UTC),
    )
    claim_kickoff(session.settings, session.inbound, request)
    ack_receipt = session.text(kickoff_ack_text(request), kind="ack")
    try:
        result = await run_call(
            request, session.settings, from_did=from_did
        )
    except (ValueError, RuntimeError, OSError, PersistenceConfigError) as error:
        text = f"Call not placed: {error}"
        result_receipt = session.text(text, kind="result")
        write_call_cross_links(
            session.settings,
            session.inbound,
            None,
            result_receipt,
            text,
            error=str(error),
        )
        return KickoffReceipt(
            request=request,
            result=None,
            ack_receipt=ack_receipt,
            result_receipt=result_receipt,
            call_control_id="",
            error=str(error),
        )
    except Exception as error:
        text = f"Call failed: {exception_reason(error)}"
        result_receipt = session.text(text, kind="result")
        write_call_cross_links(
            session.settings,
            session.inbound,
            None,
            result_receipt,
            text,
            error=str(error),
        )
        return KickoffReceipt(
            request=request,
            result=None,
            ack_receipt=ack_receipt,
            result_receipt=result_receipt,
            call_control_id="",
            error=str(error),
        )
    text = result_reply_text(result, request.to)
    result_receipt = session.text(text, kind="result")
    write_call_cross_links(
        session.settings,
        inbound=session.inbound,
        result=result,
        result_receipt=result_receipt,
        result_text=text,
    )
    return KickoffReceipt(
        request=request,
        result=result,
        ack_receipt=ack_receipt,
        result_receipt=result_receipt,
        call_control_id=result.call_control_id,
    )


def make_session(
    inbound: InboundMessage,
    settings: Settings,
    port: MessagingPort,
) -> KickoffSession:
    return KickoffSession(
        inbound=inbound,
        from_did=reply_from_did(settings),
        port=port,
        settings=settings,
    )


def _decide(
    extracted: _Extracted,
    settings: Settings,
    *,
    verb: Literal["book", "ask"],
    confirmed: bool = False,
    account: UserRecord | None = None,
) -> KickoffParsed | KickoffCollecting | KickoffFailed | KickoffBlocked:
    blocked = _account_gate(account)
    if blocked is not None:
        return KickoffBlocked(blocked, verb)
    assert account is not None
    caller = FirstName(account.first_name or "")
    user_id = account.user_id
    assert account.from_did is not None
    if verb == "ask":
        return _decide_ask(
            extracted,
            settings,
            caller,
            user_id,
            account,
            account.from_did,
            confirmed=confirmed,
        )
    return _decide_book(
        extracted,
        settings,
        caller,
        user_id,
        account,
        account.from_did,
        confirmed=confirmed,
    )


def _decide_book(
    extracted: _Extracted,
    settings: Settings,
    caller: FirstName,
    user_id: UserId,
    account: UserRecord,
    from_did: E164,
    *,
    confirmed: bool,
) -> KickoffParsed | KickoffCollecting | KickoffFailed:
    missing: list[WaitingFor] = []
    time_text = _time_span(extracted.time_text or "")
    if extracted.party_size is None:
        missing.append("party")
    if not time_text:
        missing.append("time")
    if missing:
        waiting: WaitingFor = (
            "party_and_time" if missing == ["party", "time"] else missing[0]
        )
        return KickoffCollecting(
            reply=collect_reply_text(waiting),
            pending=_pending_from_extracted(extracted, "book", waiting),
            verb="book",
        )
    if extracted.to is None:
        return KickoffCollecting(
            reply=PHONE_ASK_TEXT,
            pending=_pending_from_extracted(extracted, "book", "to"),
            verb="book",
        )
    if not confirmed:
        return KickoffCollecting(
            reply=CONFIRM_ASK_TEXT,
            pending=_pending_from_extracted(extracted, "book", "confirm"),
            verb="book",
        )
    try:
        request = BookRequest(
            user_id=user_id,
            to=extracted.to,
            caller=caller,
            mode=_mode(settings),
            party_size=extracted.party_size or 0,
            time_text=time_text or "",
            constraints=extracted.constraints,
            retain_call_artifacts=(
                True if account.retain_call_artifacts else None
            ),
        )
    except ValueError:
        return KickoffFailed("I need a bit more to place the call.", "book")
    return KickoffParsed(request=request, verb="book", from_did=from_did)


def _decide_ask(
    extracted: _Extracted,
    settings: Settings,
    caller: FirstName,
    user_id: UserId,
    account: UserRecord,
    from_did: E164,
    *,
    confirmed: bool,
) -> KickoffParsed | KickoffCollecting | KickoffFailed:
    kind = extracted.question_kind
    if kind is None:
        return KickoffCollecting(
            reply=collect_reply_text("kind"),
            pending=_pending_from_extracted(extracted, "ask", "kind"),
            verb="ask",
        )
    if kind in {QuestionType.STOCK, QuestionType.AISLE} and not extracted.question_item:
        return KickoffCollecting(
            reply=collect_reply_text("item"),
            pending=_pending_from_extracted(extracted, "ask", "item"),
            verb="ask",
        )
    if extracted.to is None:
        return KickoffCollecting(
            reply=PHONE_ASK_TEXT,
            pending=_pending_from_extracted(extracted, "ask", "to"),
            verb="ask",
        )
    if not confirmed:
        return KickoffCollecting(
            reply=CONFIRM_ASK_TEXT,
            pending=_pending_from_extracted(extracted, "ask", "confirm"),
            verb="ask",
        )
    try:
        request = QuickAskRequest(
            user_id=user_id,
            to=extracted.to,
            question=Question.from_cli(kind, extracted.question_item),
            caller=caller,
            mode=_mode(settings),
            retain_call_artifacts=(
                True if account.retain_call_artifacts else None
            ),
        )
    except ValueError:
        return KickoffFailed("I need a bit more to place the call.", "ask")
    return KickoffParsed(request=request, verb="ask", from_did=from_did)


def _pending_from_extracted(
    extracted: _Extracted,
    verb: Literal["book", "ask"],
    waiting_for: WaitingFor,
) -> PendingCollect:
    return PendingCollect(
        verb=verb,
        waiting_for=waiting_for,
        to=str(extracted.to) if extracted.to is not None else None,
        party_size=extracted.party_size,
        time_text=_time_span(extracted.time_text or ""),
        constraints=extracted.constraints,
        question_kind=(
            extracted.question_kind.value
            if extracted.question_kind is not None
            else None
        ),
        question_item=extracted.question_item,
    )


def _extract_fields(
    text: str, *, waiting_for: WaitingFor | None
) -> _Extracted:
    cli = _extract_cli(text)
    if cli is not None:
        return cli
    return _Extracted(
        intent=_intent(text, waiting_for),
        to=_extract_e164(text),
        party_size=_extract_party(
            text, loose=waiting_for in {"party", "party_and_time"}
        ),
        time_text=_extract_time(
            text, loose=waiting_for in {"time", "party_and_time"}
        ),
        constraints=_extract_notes(text),
        question_kind=_extract_kind(text),
        question_item=_extract_item(text),
    )


def _extract_cli(text: str) -> _Extracted | None:
    book = _BOOK_RE.fullmatch(text)
    if book is not None:
        try:
            to = E164(book.group("to"))
            party = int(book.group("party"))
        except ValueError:
            return None
        return _Extracted(
            intent="book",
            to=to,
            party_size=party,
            time_text=book.group("time").strip(),
            constraints=_optional_text(book.group("notes")),
            question_kind=None,
            question_item=None,
        )
    hours = _ASK_HOURS_RE.fullmatch(text)
    item_match = _ASK_ITEM_RE.fullmatch(text)
    matched = hours or item_match
    if matched is None:
        return None
    try:
        to = E164(matched.group("to"))
        kind = QuestionType("hours" if hours is not None else matched.group("kind").casefold())
    except ValueError:
        return None
    item = None if hours is not None else matched.group("item").strip()
    return _Extracted(
        intent="ask",
        to=to,
        party_size=None,
        time_text=None,
        constraints=None,
        question_kind=kind,
        question_item=item,
    )


def _intent(
    text: str, waiting_for: WaitingFor | None
) -> Literal["book", "ask"] | None:
    if waiting_for is not None:
        return None
    first = text.split(" ", 1)[0].casefold()
    if first == "book" or _BOOK_INTENT_RE.search(text):
        return "book"
    if first == "ask" or _ASK_INTENT_RE.search(text):
        return "ask"
    return None


def _extract_e164(text: str) -> E164 | None:
    matched = _E164_RE.search(text)
    if matched is not None:
        try:
            return E164(matched.group(0))
        except ValueError:
            pass
    us = _US_PHONE_RE.search(text)
    if us is None:
        return None
    try:
        return E164(f"+1{us.group(1)}{us.group(2)}{us.group(3)}")
    except ValueError:
        return None


def _extract_party(text: str, *, loose: bool) -> int | None:
    for pattern in (_PARTY_RE, _FOR_PEOPLE_RE):
        matched = pattern.search(text)
        if matched is not None:
            value = _party_value(matched.group(1))
            if value is not None:
                return value
    if not loose:
        return None
    bare = _BARE_PARTY_RE.fullmatch(text)
    if bare is None:
        return None
    return _party_value(bare.group(1))


def _extract_time(text: str, *, loose: bool) -> str | None:
    del loose
    return _time_span(text)


def _time_span(text: str) -> str | None:
    """Clock, am/pm, noon, or `at <hour>`. Not `at <restaurant>`."""
    matched = _TIME_LIKE_RE.search(text)
    if matched is not None:
        day = " ".join((matched.group("day") or "").split())
        clock = " ".join((matched.group("clock") or "").split())
        if clock:
            return f"{day} {clock}".strip()
    bare = _AT_BARE_HOUR_RE.search(text)
    if bare is None:
        return None
    clock = (bare.group("clock") or "").strip()
    return clock or None


def _extract_notes(text: str) -> str | None:
    matched = _NOTES_RE.search(text)
    if matched is None:
        return None
    return _optional_text(matched.group(1))


def _extract_kind(text: str) -> QuestionType | None:
    lowered = text.casefold()
    tokens = set(lowered.split())
    if "hours" in tokens or "open" in tokens:
        return QuestionType.HOURS
    if "stock" in tokens:
        return QuestionType.STOCK
    if "aisle" in tokens:
        return QuestionType.AISLE
    return None


def _extract_item(text: str) -> str | None:
    matched = _ASK_ITEM_RE.fullmatch(text)
    if matched is not None:
        return matched.group("item").strip()
    item_match = re.search(
        r"\b(?:stock|aisle)\s+(.+?)(?:\s+name\s+.+)?$",
        text,
        re.IGNORECASE,
    )
    if item_match is None:
        return None
    return _optional_text(item_match.group(1))


def _merge(pending: PendingCollect, extracted: _Extracted) -> _Extracted:
    kind = extracted.question_kind
    if kind is None and pending.question_kind:
        try:
            kind = QuestionType(pending.question_kind)
        except ValueError:
            kind = None
    to = extracted.to
    if to is None and pending.to:
        try:
            to = E164(pending.to)
        except ValueError:
            to = None
    return _Extracted(
        intent=pending.verb,
        to=to,
        party_size=extracted.party_size or pending.party_size,
        time_text=_time_span(extracted.time_text or "")
        or _time_span(pending.time_text or ""),
        constraints=extracted.constraints or pending.constraints,
        question_kind=kind,
        question_item=extracted.question_item or pending.question_item,
    )


def _progressed(pending: PendingCollect, extracted: _Extracted) -> bool:
    waiting = pending.waiting_for
    if waiting == "to":
        return extracted.to is not None
    if waiting == "time":
        return bool(_time_span(extracted.time_text or ""))
    if waiting == "party":
        return extracted.party_size is not None
    if waiting == "party_and_time":
        return extracted.party_size is not None or bool(
            _time_span(extracted.time_text or "")
        )
    if waiting == "kind":
        return extracted.question_kind is not None
    if waiting == "item":
        return bool(extracted.question_item)
    return False


def _party_value(raw: str) -> int | None:
    lowered = raw.casefold()
    if lowered in _PARTY_WORDS:
        return _PARTY_WORDS[lowered]
    try:
        value = int(raw)
    except ValueError:
        return None
    if value < 1:
        return None
    return value


def _is_confirm_affirmative(text: str) -> bool:
    return _AFFIRM_RE.search(text) is not None


def _waiting_for_value(raw: object) -> WaitingFor:
    if raw in {
        "party",
        "time",
        "party_and_time",
        "to",
        "kind",
        "item",
        "confirm",
    }:
        return raw  # type: ignore[return-value]
    return "to"


def _account_gate(account: UserRecord | None) -> str | None:
    if account is None:
        return NO_ACCOUNT_TEXT
    raw = (account.first_name or "").strip()
    if not raw:
        return NO_NAME_TEXT
    try:
        FirstName(raw)
    except ValueError:
        return NO_NAME_TEXT
    if account.from_did is None:
        return NO_FROM_DID_TEXT
    return None


def _mode(settings: Settings) -> Mode:
    return Mode.LIVE if settings.quick_ask_live else Mode.STUB


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _require_artifacts_root(root: Path) -> Path:
    if not root.exists():
        raise FileNotFoundError(f"CALL_AGENT_ARTIFACTS_DIR is missing: {root}")
    if not root.is_dir():
        raise NotADirectoryError(
            f"CALL_AGENT_ARTIFACTS_DIR is not a directory: {root}"
        )
    return root
