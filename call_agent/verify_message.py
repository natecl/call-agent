from __future__ import annotations

import json
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .domain import E164
from .linq import (
    LinqConfigError,
    LinqWebhookAuthError,
    LiveLinqHttp,
    StubLinq,
    sign_linq_webhook,
    verify_linq_signature,
)
from .messaging import IMESSAGE, InboundMessage, TextPart, text_chat

STUB_FROM = E164("+15555550100")
STUB_TO = E164("+14155552671")
STUB_TEXT = "kickoff"
STUB_WEBHOOK_SECRET = "whsec_dGVzdC1saW5xLXdlYmhvb2stc2VjcmV0"
INBOUND_EVENT_ID = "2915e81c-5068-4796-ace2-21d2c94ad298"
INBOUND_CHAT_ID = "8f392755-6865-4b18-880a-227f9d8b458f"
INBOUND_MESSAGE_ID = "89e3566e-1d13-49e5-a8ee-48490d5bfeb7"
INBOUND_SENDER = "+14155552671"


class MessageVerifyInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    from_did: E164 = STUB_FROM
    to: E164 = STUB_TO
    text: str = STUB_TEXT


CheckName = Literal[
    "stub_send",
    "inbound_map",
    "signature",
    "live_token",
    "imessage_only",
]


class MessageVerifyCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: CheckName
    passed: bool
    detail: str


class MessageVerifyReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    checks: tuple[
        MessageVerifyCheck,
        MessageVerifyCheck,
        MessageVerifyCheck,
        MessageVerifyCheck,
        MessageVerifyCheck,
    ]


def inbound_fixture(*, service: str = IMESSAGE) -> bytes:
    return json.dumps(
        {
            "api_version": "v3",
            "webhook_version": "2026-02-03",
            "event_type": "message.received",
            "event_id": INBOUND_EVENT_ID,
            "data": {
                "chat": {"id": INBOUND_CHAT_ID},
                "id": INBOUND_MESSAGE_ID,
                "direction": "inbound",
                "sender_handle": {
                    "handle": INBOUND_SENDER,
                    "service": service,
                },
                "parts": [{"type": "text", "value": "reply"}],
                "service": service,
            },
        }
    ).encode()


def verify_message(
    verify_input: MessageVerifyInput | None = None,
) -> MessageVerifyReport:
    active = verify_input or MessageVerifyInput()
    port = StubLinq()
    outbound = text_chat(active.from_did, active.to, active.text)
    receipt = port.send(outbound)
    payload = port.last_payload or {}
    message = payload.get("message") or {}
    parts = message.get("parts") or []
    send_ok = (
        port.last_path == "/chats"
        and payload.get("from") == str(active.from_did)
        and payload.get("to") == [str(active.to)]
        and message.get("preferred_service") == IMESSAGE
        and parts == [{"type": "text", "value": active.text}]
        and "SMS" not in json.dumps(payload)
        and receipt.chat_id == "stub-chat-0001"
        and receipt.message_id == "stub-msg-0001"
    )
    send_check = MessageVerifyCheck(
        name="stub_send",
        passed=send_ok,
        detail=(
            f"path={port.last_path} from={payload.get('from')} "
            f"to={payload.get('to')} preferred_service="
            f"{message.get('preferred_service')}"
        ),
    )

    raw = inbound_fixture()
    inbound = port.receive(raw)
    inbound_ok = inbound == InboundMessage(
        event_id=INBOUND_EVENT_ID,
        chat_id=INBOUND_CHAT_ID,
        message_id=INBOUND_MESSAGE_ID,
        sender=INBOUND_SENDER,
        parts=(TextPart(value="reply"),),
        service=IMESSAGE,
    )
    inbound_check = MessageVerifyCheck(
        name="inbound_map",
        passed=bool(inbound_ok),
        detail=(
            f"event_id={getattr(inbound, 'event_id', None)} "
            f"chat_id={getattr(inbound, 'chat_id', None)} "
            f"service={getattr(inbound, 'service', None)}"
        ),
    )

    timestamp = str(int(time.time()))
    headers = sign_linq_webhook(
        STUB_WEBHOOK_SECRET,
        raw,
        msg_id=INBOUND_EVENT_ID,
        timestamp=timestamp,
    )
    try:
        verify_linq_signature(STUB_WEBHOOK_SECRET, raw, headers)
        good_sig = True
    except LinqWebhookAuthError:
        good_sig = False
    bad_headers = {**headers, "webhook-signature": "v1,AAAAAAAAAAAAAAAAAAAAAA=="}
    try:
        verify_linq_signature(STUB_WEBHOOK_SECRET, raw, bad_headers)
        bad_rejected = False
    except LinqWebhookAuthError:
        bad_rejected = True
    signature_check = MessageVerifyCheck(
        name="signature",
        passed=good_sig and bad_rejected,
        detail="standard webhooks v1 accept/reject",
    )

    try:
        LiveLinqHttp("")
        token_failed = False
    except LinqConfigError as error:
        token_failed = "LINQ_API_TOKEN" in str(error)
    token_check = MessageVerifyCheck(
        name="live_token",
        passed=token_failed,
        detail="LiveLinqHttp fails loud without LINQ_API_TOKEN",
    )

    sms_ignored = port.receive(inbound_fixture(service="SMS")) is None
    imessage_check = MessageVerifyCheck(
        name="imessage_only",
        passed=sms_ignored and message.get("preferred_service") == IMESSAGE,
        detail="SMS inbound ignored, outbound pinned to iMessage",
    )

    checks = (
        send_check,
        inbound_check,
        signature_check,
        token_check,
        imessage_check,
    )
    return MessageVerifyReport(
        passed=all(check.passed for check in checks),
        checks=checks,
    )


def format_message_report(report: MessageVerifyReport) -> str:
    lines = [
        (
            f"[{'PASS' if check.passed else 'FAIL'}] "
            f"{check.name:<18} {check.detail}"
        )
        for check in report.checks
    ]
    passed = sum(check.passed for check in report.checks)
    lines.append(f"{passed}/5 passed")
    return "\n".join(lines)
