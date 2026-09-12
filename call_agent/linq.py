from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from .config import Settings
from .domain import E164
from .messaging import (
    IMESSAGE,
    LINQ_PARTNER_V3,
    ChatId,
    EventId,
    Handle,
    InboundMessage,
    MessageId,
    MessagingPort,
    OutboundChat,
    SendReceipt,
    TextPart,
    ack_reply_text,
    followup_chat,
    inbound_plain_text,
    text_chat,
)

STUB_CHAT_ID = ChatId("stub-chat-0001")
STUB_MESSAGE_ID = MessageId("stub-msg-0001")
SIGNATURE_MAX_AGE_S = 300


class LinqConfigError(ValueError):
    pass


class LinqWebhookAuthError(ValueError):
    pass


class _WireModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        populate_by_name=True,
    )


class _SentMessage(_WireModel):
    id: str


class _ChatBody(_WireModel):
    id: str
    message: _SentMessage


class _CreateChatResponse(_WireModel):
    chat: _ChatBody


class _SendMessageResponse(_WireModel):
    chat_id: str | None = None
    message: _SentMessage
    chat: _ChatBody | None = None


class _WebhookSubscriptionResponse(_WireModel):
    id: str
    signing_secret: str
    target_url: str


class WebhookSubscription(_WireModel):
    subscription_id: str
    signing_secret: str
    target_url: str


def serialize_outbound(message: OutboundChat) -> dict[str, Any]:
    body: dict[str, Any] = {
        "message": {
            "parts": [
                {"type": "text", "value": part.value} for part in message.parts
            ],
            "preferred_service": IMESSAGE,
        }
    }
    if message.chat_id is None:
        body["from"] = str(message.from_did)
        body["to"] = [str(recipient) for recipient in message.to]
    return body


def outbound_path(message: OutboundChat) -> str:
    if message.chat_id is None:
        return "/chats"
    return f"/chats/{message.chat_id}/messages"


def parse_send_response(raw: dict[str, Any]) -> SendReceipt:
    if "chat" in raw and isinstance(raw["chat"], dict):
        parsed = _CreateChatResponse.model_validate(raw)
        return SendReceipt(
            chat_id=ChatId(parsed.chat.id),
            message_id=MessageId(parsed.chat.message.id),
        )
    parsed_send = _SendMessageResponse.model_validate(raw)
    chat_id = parsed_send.chat_id
    if chat_id is None and parsed_send.chat is not None:
        chat_id = parsed_send.chat.id
    if chat_id is None:
        raise ValueError("Linq send response missing chat id")
    return SendReceipt(
        chat_id=ChatId(chat_id),
        message_id=MessageId(parsed_send.message.id),
    )


def _text_parts(raw_parts: list[Any]) -> tuple[TextPart, ...]:
    parts: list[TextPart] = []
    for item in raw_parts:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        value = item.get("value")
        if not isinstance(value, str) or not value:
            continue
        parts.append(TextPart(value=value))
    return tuple(parts)


def parse_inbound(raw: bytes) -> InboundMessage | None:
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        return None
    if envelope.get("event_type") != "message.received":
        return None
    data = envelope.get("data")
    if not isinstance(data, dict):
        return None
    service = data.get("service")
    nested = data.get("message")
    if service is None and isinstance(nested, dict):
        service = nested.get("service")
    if service != IMESSAGE:
        return None
    direction = data.get("direction")
    if direction is None and isinstance(nested, dict):
        direction = nested.get("direction")
    if direction is not None and direction != "inbound":
        return None
    event_id = envelope.get("event_id")
    if not isinstance(event_id, str):
        return None
    chat = data.get("chat")
    chat_id = data.get("chat_id")
    if isinstance(chat, dict) and isinstance(chat.get("id"), str):
        chat_id = chat["id"]
    sender_handle = data.get("sender_handle") or data.get("from_handle")
    sender = data.get("from")
    if isinstance(sender_handle, dict) and isinstance(
        sender_handle.get("handle"), str
    ):
        sender = sender_handle["handle"]
    message_id = data.get("id")
    parts_raw = data.get("parts")
    if isinstance(nested, dict):
        if message_id is None:
            message_id = nested.get("id")
        if parts_raw is None:
            parts_raw = nested.get("parts")
    if not isinstance(chat_id, str) or not isinstance(message_id, str):
        return None
    if not isinstance(sender, str) or not isinstance(parts_raw, list):
        return None
    parts = _text_parts(parts_raw)
    if not parts:
        return None
    return InboundMessage(
        event_id=EventId(event_id),
        chat_id=ChatId(chat_id),
        message_id=MessageId(message_id),
        sender=Handle(sender),
        parts=parts,
        service=IMESSAGE,
    )


def _header(headers: Mapping[str, str], name: str) -> str:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    raise LinqWebhookAuthError(f"missing {name} header")


def sign_linq_webhook(
    secret: str,
    body: bytes,
    *,
    msg_id: str,
    timestamp: str,
) -> dict[str, str]:
    secret_str = secret.removeprefix("whsec_")
    key = base64.b64decode(secret_str)
    signed = f"{msg_id}.{timestamp}.".encode() + body
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode()
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": f"v1,{signature}",
    }


def verify_linq_signature(
    secret: str,
    body: bytes,
    headers: Mapping[str, str],
    *,
    now: float | None = None,
    max_age_s: float = SIGNATURE_MAX_AGE_S,
) -> None:
    msg_id = _header(headers, "webhook-id")
    timestamp = _header(headers, "webhook-timestamp")
    signature = _header(headers, "webhook-signature")
    try:
        ts = float(timestamp)
    except ValueError as error:
        raise LinqWebhookAuthError("webhook-timestamp is not a unix time") from error
    clock = time.time() if now is None else now
    if abs(clock - ts) > max_age_s:
        raise LinqWebhookAuthError("webhook timestamp is outside the replay window")
    expected = sign_linq_webhook(
        secret, body, msg_id=msg_id, timestamp=timestamp
    )["webhook-signature"][3:]
    matched = False
    for part in signature.split(" "):
        if not part.startswith("v1,"):
            continue
        if hmac.compare_digest(expected, part[3:]):
            matched = True
            break
    if not matched:
        raise LinqWebhookAuthError("webhook signature mismatch")


class StubLinq:
    def __init__(self) -> None:
        self.last_path: str | None = None
        self.last_payload: dict[str, Any] | None = None

    def send(self, message: OutboundChat) -> SendReceipt:
        self.last_path = outbound_path(message)
        self.last_payload = serialize_outbound(message)
        return SendReceipt(
            chat_id=message.chat_id or STUB_CHAT_ID,
            message_id=STUB_MESSAGE_ID,
        )

    def receive(self, raw: bytes) -> InboundMessage | None:
        return parse_inbound(raw)


class LiveLinqHttp:
    def __init__(
        self,
        api_token: str,
        base_url: str = LINQ_PARTNER_V3,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_token:
            raise LinqConfigError("live Linq requires LINQ_API_TOKEN")
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30.0)
        self._client.headers["Authorization"] = f"Bearer {api_token}"

    def send(self, message: OutboundChat) -> SendReceipt:
        response = self._client.post(
            f"{self._base_url}{outbound_path(message)}",
            json=serialize_outbound(message),
        )
        response.raise_for_status()
        return parse_send_response(response.json())

    def receive(self, raw: bytes) -> InboundMessage | None:
        return parse_inbound(raw)

    def register_webhook(self, target_url: str) -> WebhookSubscription:
        if not target_url.startswith("https://"):
            raise LinqConfigError("Linq webhook target_url must be https")
        response = self._client.post(
            f"{self._base_url}/webhook-subscriptions",
            json={
                "subscribed_events": ["message.received"],
                "target_url": target_url,
            },
        )
        response.raise_for_status()
        raw = response.json()
        if "id" not in raw and isinstance(raw.get("data"), dict):
            raw = raw["data"]
        parsed = _WebhookSubscriptionResponse.model_validate(raw)
        return WebhookSubscription(
            subscription_id=parsed.id,
            signing_secret=parsed.signing_secret,
            target_url=parsed.target_url,
        )


def validate_linq_live_token(api_token: str | None) -> None:
    if not api_token:
        raise LinqConfigError("live Linq requires LINQ_API_TOKEN")


def linq_from(settings: Settings) -> E164:
    raw = settings.linq_from_e164
    if not raw:
        raise LinqConfigError("live Linq send requires LINQ_FROM_E164")
    return E164(raw)


STUB_FROM_DID = E164("+15555550100")


def reply_from_did(settings: Settings) -> E164:
    if settings.linq_from_e164:
        return E164(settings.linq_from_e164)
    if settings.linq_api_token:
        raise LinqConfigError("live Linq send requires LINQ_FROM_E164")
    return STUB_FROM_DID


def messaging_port(settings: Settings) -> MessagingPort:
    token = settings.linq_api_token
    if token:
        return LiveLinqHttp(token)
    return StubLinq()


def linq_artifact_dir(root: Path, event_id: EventId | str) -> Path:
    if not root.exists():
        raise FileNotFoundError(f"CALL_AGENT_ARTIFACTS_DIR is missing: {root}")
    if not root.is_dir():
        raise NotADirectoryError(
            f"CALL_AGENT_ARTIFACTS_DIR is not a directory: {root}"
        )
    path = root / "linq" / str(event_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_linq_message_artifacts(
    root: Path,
    inbound: InboundMessage,
    outbound: OutboundChat,
    receipt: SendReceipt,
    *,
    received_at: datetime,
    sent_at: datetime,
) -> Path:
    path = linq_artifact_dir(root, inbound.event_id)
    inbound_text = inbound_plain_text(inbound)
    reply_text = " ".join(part.value for part in outbound.parts)
    inbound_payload = {
        "event_id": str(inbound.event_id),
        "chat_id": str(inbound.chat_id),
        "message_id": str(inbound.message_id),
        "sender": str(inbound.sender),
        "service": inbound.service,
        "parts": [part.model_dump(mode="json") for part in inbound.parts],
        "text": inbound_text,
        "received_at": received_at.isoformat(),
    }
    reply_payload = {
        "chat_id": str(receipt.chat_id),
        "message_id": str(receipt.message_id),
        "inbound_message_id": str(inbound.message_id),
        "outbound_path": outbound_path(outbound),
        "preferred_service": IMESSAGE,
        "parts": [part.model_dump(mode="json") for part in outbound.parts],
        "text": reply_text,
        "sent_at": sent_at.isoformat(),
    }
    (path / "inbound.json").write_text(
        json.dumps(inbound_payload, indent=2, sort_keys=True) + "\n"
    )
    (path / "reply.json").write_text(
        json.dumps(reply_payload, indent=2, sort_keys=True) + "\n"
    )
    return path


def handle_inbound(
    inbound: InboundMessage,
    settings: Settings,
    port: MessagingPort | None = None,
) -> SendReceipt | None:
    from_did = reply_from_did(settings)
    if str(inbound.sender) == str(from_did):
        return None
    active = port or messaging_port(settings)
    outbound = followup_chat(from_did, inbound, ack_reply_text(inbound))
    received_at = datetime.now(UTC)
    receipt = active.send(outbound)
    sent_at = datetime.now(UTC)
    write_linq_message_artifacts(
        settings.call_agent_artifacts_dir,
        inbound,
        outbound,
        receipt,
        received_at=received_at,
        sent_at=sent_at,
    )
    return receipt


def kickoff_from_settings(settings: Settings, to: E164, text: str) -> OutboundChat:
    return text_chat(linq_from(settings), to, text)


def resolve_webhook_url(
    linq_webhook_url: str | None,
    public_webhook_url: str | None,
) -> str:
    url = (linq_webhook_url or public_webhook_url or "").rstrip("/")
    if not url:
        raise LinqConfigError(
            "register-webhook requires LINQ_WEBHOOK_URL or PUBLIC_WEBHOOK_URL"
        )
    return url
