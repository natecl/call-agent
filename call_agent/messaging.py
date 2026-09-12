from __future__ import annotations

import re
from typing import Literal, Protocol

from pydantic import field_validator, model_validator

from .domain import E164, FrozenModel, _Token

IMESSAGE = "iMessage"
LINQ_PARTNER_V3 = "https://api.linqapp.com/api/partner/v3"


class ChatId(_Token):
    _pattern = re.compile(r"^\S+$")
    _label = "chat id"


class MessageId(_Token):
    _pattern = re.compile(r"^\S+$")
    _label = "message id"


class EventId(_Token):
    _pattern = re.compile(r"^\S+$")
    _label = "event id"


class Handle(_Token):
    _pattern = re.compile(
        r"^(\+[1-9]\d{7,14}|[^@\s]+@[^@\s]+\.[^@\s]+)$"
    )
    _label = "handle"


class TextPart(FrozenModel):
    type: Literal["text"] = "text"
    value: str

    @field_validator("value")
    @classmethod
    def _value_present(cls, value: str) -> str:
        if not value:
            raise ValueError("text part is empty")
        if len(value) > 10_000:
            raise ValueError("text part exceeds 10000 characters")
        return value


class OutboundChat(FrozenModel):
    from_did: E164
    to: tuple[E164, ...]
    parts: tuple[TextPart, ...]
    chat_id: ChatId | None = None

    @model_validator(mode="after")
    def _recipients_and_parts(self) -> OutboundChat:
        if self.chat_id is None and not self.to:
            raise ValueError("outbound chat needs at least one recipient")
        if not self.parts:
            raise ValueError("outbound chat needs at least one text part")
        return self


class SendReceipt(FrozenModel):
    chat_id: ChatId
    message_id: MessageId


class InboundMessage(FrozenModel):
    event_id: EventId
    chat_id: ChatId
    message_id: MessageId
    sender: Handle
    parts: tuple[TextPart, ...]
    service: Literal["iMessage"]


class MessagingPort(Protocol):
    def send(self, message: OutboundChat) -> SendReceipt: ...

    def receive(self, raw: bytes) -> InboundMessage | None: ...


def text_chat(from_did: E164, to: E164, text: str) -> OutboundChat:
    return OutboundChat(
        from_did=from_did,
        to=(to,),
        parts=(TextPart(value=text),),
    )


def inbound_plain_text(inbound: InboundMessage) -> str:
    return " ".join(part.value for part in inbound.parts)


def ack_reply_text(inbound: InboundMessage) -> str:
    return f"Received: {inbound_plain_text(inbound)}"


def followup_chat(
    from_did: E164,
    inbound: InboundMessage,
    text: str,
) -> OutboundChat:
    to: tuple[E164, ...] = ()
    try:
        to = (E164(str(inbound.sender)),)
    except ValueError:
        to = ()
    return OutboundChat(
        from_did=from_did,
        to=to,
        parts=(TextPart(value=text),),
        chat_id=inbound.chat_id,
    )
