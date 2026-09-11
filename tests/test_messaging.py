import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from call_agent.domain import E164
from call_agent.linq import (
    LinqConfigError,
    LinqWebhookAuthError,
    LiveLinqHttp,
    StubLinq,
    parse_inbound,
    serialize_outbound,
    sign_linq_webhook,
    verify_linq_signature,
)
from call_agent.messaging import (
    IMESSAGE,
    InboundMessage,
    TextPart,
    text_chat,
)
from call_agent.server import app
from call_agent.verify_message import (
    INBOUND_CHAT_ID,
    INBOUND_EVENT_ID,
    INBOUND_MESSAGE_ID,
    INBOUND_SENDER,
    STUB_WEBHOOK_SECRET,
    inbound_fixture,
    verify_message,
)


def test_text_chat_is_parts_not_sms_body() -> None:
    message = text_chat(E164("+15555550100"), E164("+14155552671"), "kickoff")
    payload = serialize_outbound(message)

    assert payload == {
        "from": "+15555550100",
        "to": ["+14155552671"],
        "message": {
            "parts": [{"type": "text", "value": "kickoff"}],
            "preferred_service": "iMessage",
        },
    }
    assert "SMS" not in str(payload)


def test_stub_send_and_receive_round_trip() -> None:
    port = StubLinq()
    receipt = port.send(
        text_chat(E164("+15555550100"), E164("+14155552671"), "kickoff")
    )
    inbound = port.receive(inbound_fixture())

    assert receipt.chat_id == "stub-chat-0001"
    assert receipt.message_id == "stub-msg-0001"
    assert inbound == InboundMessage(
        event_id=INBOUND_EVENT_ID,
        chat_id=INBOUND_CHAT_ID,
        message_id=INBOUND_MESSAGE_ID,
        sender=INBOUND_SENDER,
        parts=(TextPart(value="reply"),),
        service=IMESSAGE,
    )


def test_receive_ignores_sms_and_non_message_events() -> None:
    port = StubLinq()

    assert port.receive(inbound_fixture(service="SMS")) is None
    assert port.receive(b'{"event_type":"message.sent","event_id":"x","data":{}}') is None


def test_parse_inbound_accepts_2025_shape() -> None:
    raw = (
        b'{"event_type":"message.received","event_id":"evt-1","data":{'
        b'"chat_id":"chat-1","from":"+14155552671","service":"iMessage",'
        b'"message":{"id":"msg-1","parts":[{"type":"text","value":"hi"}]}}}'
    )

    inbound = parse_inbound(raw)

    assert inbound is not None
    assert inbound.chat_id == "chat-1"
    assert inbound.message_id == "msg-1"
    assert inbound.sender == "+14155552671"
    assert inbound.parts == (TextPart(value="hi"),)


def test_signature_accepts_good_and_rejects_bad() -> None:
    body = inbound_fixture()
    timestamp = str(int(time.time()))
    headers = sign_linq_webhook(
        STUB_WEBHOOK_SECRET,
        body,
        msg_id="evt-1",
        timestamp=timestamp,
    )

    verify_linq_signature(STUB_WEBHOOK_SECRET, body, headers)
    with pytest.raises(LinqWebhookAuthError):
        verify_linq_signature(
            STUB_WEBHOOK_SECRET,
            body,
            {**headers, "webhook-signature": "v1,AAAAAAAAAAAAAAAAAAAAAA=="},
        )


def test_live_constructor_fails_without_token() -> None:
    with pytest.raises(LinqConfigError, match="LINQ_API_TOKEN"):
        LiveLinqHttp("")


def test_live_send_posts_chats_parts_shape() -> None:
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200,
            json={
                "chat": {
                    "id": "chat-live-1",
                    "message": {"id": "msg-live-1"},
                }
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.linqapp.com/api/partner/v3",
    )
    port = LiveLinqHttp("token-from-env", client=client)
    receipt = port.send(
        text_chat(E164("+12138653024"), E164("+17272701288"), "hello")
    )

    assert receipt.chat_id == "chat-live-1"
    assert receipt.message_id == "msg-live-1"
    assert recorded[0].url.path.endswith("/chats")
    assert recorded[0].headers["Authorization"] == "Bearer token-from-env"
    assert json.loads(recorded[0].content) == {
        "from": "+12138653024",
        "to": ["+17272701288"],
        "message": {
            "parts": [{"type": "text", "value": "hello"}],
            "preferred_service": "iMessage",
        },
    }


def test_linq_webhook_route_verifies_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINQ_WEBHOOK_SECRET", STUB_WEBHOOK_SECRET)
    body = inbound_fixture()
    headers = sign_linq_webhook(
        STUB_WEBHOOK_SECRET,
        body,
        msg_id=INBOUND_EVENT_ID,
        timestamp=str(int(time.time())),
    )
    client = TestClient(app)

    ok = client.post("/webhooks/linq", content=body, headers=headers)
    denied = client.post(
        "/webhooks/linq",
        content=body,
        headers={**headers, "webhook-signature": "v1,AAAAAAAAAAAAAAAAAAAAAA=="},
    )
    ignored = client.post(
        "/webhooks/linq",
        content=inbound_fixture(service="SMS"),
        headers=sign_linq_webhook(
            STUB_WEBHOOK_SECRET,
            inbound_fixture(service="SMS"),
            msg_id="sms-1",
            timestamp=str(int(time.time())),
        ),
    )

    assert ok.status_code == 200
    assert ok.json() == {"ok": True}
    assert denied.status_code == 401
    assert ignored.status_code == 200
    assert ignored.json() == {"ok": True, "ignored": True}


def test_register_webhook_posts_subscription() -> None:
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200,
            json={
                "id": "sub-1",
                "signing_secret": "whsec_c2VjcmV0",
                "target_url": "https://workers.example/webhooks/linq",
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.linqapp.com/api/partner/v3",
    )
    subscription = LiveLinqHttp("token-from-env", client=client).register_webhook(
        "https://workers.example/webhooks/linq"
    )

    assert subscription.subscription_id == "sub-1"
    assert json.loads(recorded[0].content) == {
        "subscribed_events": ["message.received"],
        "target_url": "https://workers.example/webhooks/linq",
    }


def test_verify_message_passes() -> None:
    report = verify_message()

    assert report.passed
    assert [check.name for check in report.checks] == [
        "stub_send",
        "inbound_map",
        "signature",
        "live_token",
        "imessage_only",
    ]
    assert all(check.passed for check in report.checks)
