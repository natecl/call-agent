from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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
    InboundMessage,
    MessagingPort,
    SendReceipt,
    followup_chat,
)
from .persistence import PersistenceConfigError
from .runtime import normalized_public_host, run_call

BOOK_USAGE = (
    "book <+E164> party <n> at <time> [name <First>] [notes <text>]"
)
ASK_USAGE = (
    "ask <+E164> hours|stock <item>|aisle <item> [name <First>]"
)

_BOOK_RE = re.compile(
    r"^book\s+(?P<to>\+[1-9]\d{7,14})\s+party\s+(?P<party>\d+)\s+at\s+"
    r"(?P<time>.+?)(?:\s+name\s+(?P<name>.+?))?"
    r"(?:\s+notes\s+(?P<notes>.+))?$",
    re.IGNORECASE,
)
_ASK_HOURS_RE = re.compile(
    r"^ask\s+(?P<to>\+[1-9]\d{7,14})\s+hours"
    r"(?:\s+name\s+(?P<name>.+))?$",
    re.IGNORECASE,
)
_ASK_ITEM_RE = re.compile(
    r"^ask\s+(?P<to>\+[1-9]\d{7,14})\s+(?P<kind>stock|aisle)\s+"
    r"(?P<item>.+?)(?:\s+name\s+(?P<name>.+))?$",
    re.IGNORECASE,
)

ReplyKind = Literal["ack", "result", "error"]


@dataclass(frozen=True, slots=True)
class KickoffParsed:
    request: BookRequest | QuickAskRequest
    verb: Literal["book", "ask"]


@dataclass(frozen=True, slots=True)
class KickoffMalformed:
    verb: Literal["book", "ask"]
    missing: tuple[str, ...]
    usage: str


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


def parse_kickoff(
    text: str,
    settings: Settings,
    sender: str | None = None,
) -> KickoffParsed | KickoffMalformed | None:
    del sender
    normalized = " ".join(text.split())
    if not normalized:
        return None
    verb = normalized.split(" ", 1)[0].casefold()
    if verb not in {"book", "ask"}:
        return None
    if verb == "book":
        return _parse_book(normalized, settings)
    return _parse_ask(normalized, settings)


def malformed_reply_text(malformed: KickoffMalformed) -> str:
    missing = ", ".join(malformed.missing) if malformed.missing else "fields"
    return (
        f"Couldn't parse that. Missing: {missing}. Use: {malformed.usage}"
    )


def kickoff_ack_text(request: BookRequest | QuickAskRequest) -> str:
    if isinstance(request, BookRequest):
        return (
            f"Calling {request.to} to book a table for "
            f"{request.party_size} at {request.time_text}."
        )
    return f"Calling {request.to} to ask: {request.question.text}"


def result_reply_text(result: CallResult, to: E164) -> str:
    if result.status == "booked":
        return f"Booked: {result.confirmation_text}"
    if result.status == "answered":
        return f"Answer: {result.answer_text}"
    if result.status == "declined":
        return "They declined."
    if result.status == "no_answer":
        return f"No answer at {to}."
    return f"Call failed: {result.reason}"


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
    payload = {
        "event_id": str(inbound.event_id),
        "chat_id": str(inbound.chat_id),
        "message_id": str(inbound.message_id),
        "sender": str(inbound.sender),
        "verb": verb,
        "request": request.model_dump(mode="json"),
        "mode": request.mode.value,
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
) -> KickoffReceipt:
    write_inbound_artifact(
        session.settings.call_agent_artifacts_dir,
        session.inbound,
        received_at=datetime.now(UTC),
    )
    claim_kickoff(session.settings, session.inbound, request)
    ack_receipt = session.text(kickoff_ack_text(request), kind="ack")
    try:
        result = await run_call(request, session.settings)
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
        text = f"Call failed: {error}"
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
        session.inbound,
        result,
        result_receipt,
        text,
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


def _parse_book(
    text: str, settings: Settings
) -> KickoffParsed | KickoffMalformed:
    matched = _BOOK_RE.fullmatch(text)
    if matched is None:
        return KickoffMalformed(
            verb="book",
            missing=_book_missing(text),
            usage=BOOK_USAGE,
        )
    caller = _resolve_caller(matched.group("name"), settings)
    if caller is None:
        return KickoffMalformed(
            verb="book", missing=("name",), usage=BOOK_USAGE
        )
    user_id = _resolve_user_id(settings)
    if user_id is None:
        return KickoffMalformed(
            verb="book", missing=("user_id",), usage=BOOK_USAGE
        )
    try:
        request = BookRequest(
            user_id=user_id,
            to=E164(matched.group("to")),
            caller=caller,
            mode=_mode(settings),
            party_size=int(matched.group("party")),
            time_text=matched.group("time").strip(),
            constraints=_optional_text(matched.group("notes")),
        )
    except ValueError:
        return KickoffMalformed(
            verb="book",
            missing=_book_missing(text) or ("to", "party", "at"),
            usage=BOOK_USAGE,
        )
    return KickoffParsed(request=request, verb="book")


def _parse_ask(
    text: str, settings: Settings
) -> KickoffParsed | KickoffMalformed:
    hours = _ASK_HOURS_RE.fullmatch(text)
    item_match = _ASK_ITEM_RE.fullmatch(text)
    matched = hours or item_match
    if matched is None:
        return KickoffMalformed(
            verb="ask",
            missing=_ask_missing(text),
            usage=ASK_USAGE,
        )
    caller = _resolve_caller(matched.group("name"), settings)
    if caller is None:
        return KickoffMalformed(
            verb="ask", missing=("name",), usage=ASK_USAGE
        )
    user_id = _resolve_user_id(settings)
    if user_id is None:
        return KickoffMalformed(
            verb="ask", missing=("user_id",), usage=ASK_USAGE
        )
    kind_raw = "hours" if hours is not None else matched.group("kind")
    item = None if hours is not None else matched.group("item").strip()
    try:
        kind = QuestionType(kind_raw.casefold())
        request = QuickAskRequest(
            user_id=user_id,
            to=E164(matched.group("to")),
            question=Question.from_cli(kind, item),
            caller=caller,
            mode=_mode(settings),
        )
    except ValueError:
        return KickoffMalformed(
            verb="ask",
            missing=_ask_missing(text) or ("to", "kind"),
            usage=ASK_USAGE,
        )
    return KickoffParsed(request=request, verb="ask")


def _resolve_caller(
    name: str | None, settings: Settings
) -> FirstName | None:
    raw = (name or settings.linq_kickoff_caller or "").strip()
    if not raw:
        return None
    try:
        return FirstName(raw)
    except ValueError:
        return None


def _resolve_user_id(settings: Settings) -> UserId | None:
    try:
        return UserId(settings.linq_kickoff_user_id)
    except ValueError:
        return None


def _mode(settings: Settings) -> Mode:
    return Mode.LIVE if settings.quick_ask_live else Mode.STUB


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _book_missing(text: str) -> tuple[str, ...]:
    missing: list[str] = []
    if re.search(r"\+[1-9]\d{7,14}", text) is None:
        missing.append("to")
    if re.search(r"\bparty\s+[1-9]\d*\b", text, re.IGNORECASE) is None:
        missing.append("party")
    if re.search(r"\bat\s+\S", text, re.IGNORECASE) is None:
        missing.append("at")
    return tuple(missing) or ("to", "party", "at")


def _ask_missing(text: str) -> tuple[str, ...]:
    missing: list[str] = []
    if re.search(r"\+[1-9]\d{7,14}", text) is None:
        missing.append("to")
    lowered = text.casefold()
    if not any(token in lowered.split() for token in ("hours", "stock", "aisle")):
        missing.append("kind")
    elif "stock" in lowered.split() or "aisle" in lowered.split():
        if _ASK_ITEM_RE.fullmatch(text) is None:
            missing.append("item")
    return tuple(missing) or ("to", "kind")
