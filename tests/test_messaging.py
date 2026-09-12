import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from call_agent.domain import E164
from call_agent.config import Settings
from call_agent.linq import (
    LinqConfigError,
    LinqWebhookAuthError,
    LiveLinqHttp,
    StubLinq,
    handle_inbound,
    kickoff_from_settings,
    parse_inbound,
    serialize_outbound,
    sign_linq_webhook,
    verify_linq_signature,
)
from call_agent.messaging import (
    IMESSAGE,
    ChatId,
    InboundMessage,
    OutboundChat,
    TextPart,
    ack_reply_text,
    followup_chat,
    text_chat,
)
from call_agent.server import app
from call_agent.verify_message import (
    INBOUND_CHAT_ID,
    INBOUND_EVENT_ID,
    INBOUND_FIXTURE_TEXT,
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


def test_followup_chat_serializes_parts_on_chat_path() -> None:
    inbound = parse_inbound(inbound_fixture())
    assert inbound is not None
    message = followup_chat(
        E164("+15555550100"), inbound, ack_reply_text(inbound)
    )
    payload = serialize_outbound(message)

    assert message.chat_id == INBOUND_CHAT_ID
    assert payload == {
        "message": {
            "parts": [{"type": "text", "value": "Received: reply"}],
            "preferred_service": "iMessage",
        }
    }
    assert "SMS" not in str(payload)
    assert "from" not in payload
    assert "to" not in payload


def test_followup_allows_empty_to_when_chat_id_is_set() -> None:
    message = OutboundChat(
        from_did=E164("+15555550100"),
        to=(),
        parts=(TextPart(value="Received: hi"),),
        chat_id=ChatId("chat-email-1"),
    )

    assert serialize_outbound(message)["message"]["preferred_service"] == IMESSAGE


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
    for event_type in (
        "message.sent",
        "message.delivered",
        "message.read",
        "message.failed",
    ):
        raw = json.dumps(
            {"event_type": event_type, "event_id": "x", "data": {}}
        ).encode()
        assert port.receive(raw) is None
    outbound_direction = json.dumps(
        {
            "event_type": "message.received",
            "event_id": INBOUND_EVENT_ID,
            "data": {
                "chat": {"id": INBOUND_CHAT_ID},
                "id": INBOUND_MESSAGE_ID,
                "direction": "outbound",
                "sender_handle": {
                    "handle": INBOUND_SENDER,
                    "service": IMESSAGE,
                },
                "parts": [{"type": "text", "value": "Received: reply"}],
                "service": IMESSAGE,
            },
        }
    ).encode()
    assert port.receive(outbound_direction) is None


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


def test_kickoff_from_settings_requires_from(settings: Settings) -> None:
    with pytest.raises(LinqConfigError, match="LINQ_FROM_E164"):
        kickoff_from_settings(settings, E164("+14155552671"), "hello")
    filled = settings.model_copy(update={"linq_from_e164": "+12138653024"})
    message = kickoff_from_settings(filled, E164("+17272701288"), "hello")
    assert message.from_did == "+12138653024"
    assert serialize_outbound(message)["from"] == "+12138653024"


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


def test_live_followup_posts_chat_messages_parts_shape() -> None:
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200,
            json={"chat_id": "chat-live-1", "message": {"id": "msg-live-2"}},
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://api.linqapp.com/api/partner/v3",
    )
    inbound = parse_inbound(inbound_fixture())
    assert inbound is not None
    port = LiveLinqHttp("token-from-env", client=client)
    receipt = port.send(
        followup_chat(
            E164("+12138653024"), inbound, ack_reply_text(inbound)
        )
    )

    assert receipt.chat_id == "chat-live-1"
    assert receipt.message_id == "msg-live-2"
    assert recorded[0].url.path.endswith(f"/chats/{INBOUND_CHAT_ID}/messages")
    assert json.loads(recorded[0].content) == {
        "message": {
            "parts": [{"type": "text", "value": "Received: reply"}],
            "preferred_service": "iMessage",
        }
    }


def _signed_headers(body: bytes, msg_id: str = INBOUND_EVENT_ID) -> dict[str, str]:
    return sign_linq_webhook(
        STUB_WEBHOOK_SECRET,
        body,
        msg_id=msg_id,
        timestamp=str(int(time.time())),
    )


def _webhook_env(
    monkeypatch: pytest.MonkeyPatch, artifacts: Path
) -> None:
    artifacts.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LINQ_WEBHOOK_SIGNING_SECRET", STUB_WEBHOOK_SECRET)
    monkeypatch.setenv("LINQ_FROM_E164", "+15555550100")
    monkeypatch.delenv("LINQ_API_TOKEN", raising=False)
    monkeypatch.setenv("CALL_AGENT_ARTIFACTS_DIR", str(artifacts))


def test_linq_webhook_route_verifies_signature(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _webhook_env(monkeypatch, tmp_path / "artifacts")
    body = inbound_fixture()
    headers = _signed_headers(body)
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
        headers=_signed_headers(inbound_fixture(service="SMS"), msg_id="sms-1"),
    )

    worker = client.post("/linq/webhook", content=body, headers=headers)

    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert ok.json()["replied"] is True
    assert worker.status_code == 200
    assert worker.json()["replied"] is True
    assert denied.status_code == 401
    assert ignored.status_code == 200
    assert ignored.json() == {"ok": True, "ignored": True}


def test_linq_webhook_replies_and_writes_inbound_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifacts = tmp_path / "artifacts"
    _webhook_env(monkeypatch, artifacts)
    body = inbound_fixture()
    client = TestClient(app)

    response = client.post(
        "/linq/webhook", content=body, headers=_signed_headers(body)
    )

    run_dir = artifacts / "linq" / INBOUND_EVENT_ID
    inbound_artifact = json.loads((run_dir / "inbound.json").read_text())
    reply_artifact = json.loads((run_dir / "reply.json").read_text())

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "replied": True,
        "chat_id": INBOUND_CHAT_ID,
        "message_id": "stub-msg-0001",
    }
    assert inbound_artifact["text"] == INBOUND_FIXTURE_TEXT
    assert inbound_artifact["event_id"] == INBOUND_EVENT_ID
    assert inbound_artifact["message_id"] == INBOUND_MESSAGE_ID
    assert inbound_artifact["sender"] == INBOUND_SENDER
    assert reply_artifact["text"] == f"Received: {INBOUND_FIXTURE_TEXT}"
    assert reply_artifact["outbound_path"] == f"/chats/{INBOUND_CHAT_ID}/messages"
    assert reply_artifact["preferred_service"] == IMESSAGE


def test_linq_webhook_fails_when_artifacts_dir_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "missing-artifacts"
    monkeypatch.setenv("LINQ_WEBHOOK_SIGNING_SECRET", STUB_WEBHOOK_SECRET)
    monkeypatch.setenv("LINQ_FROM_E164", "+15555550100")
    monkeypatch.delenv("LINQ_API_TOKEN", raising=False)
    monkeypatch.setenv("CALL_AGENT_ARTIFACTS_DIR", str(missing))
    body = inbound_fixture()
    client = TestClient(app)

    response = client.post(
        "/linq/webhook", content=body, headers=_signed_headers(body)
    )

    assert response.status_code == 500
    assert "CALL_AGENT_ARTIFACTS_DIR is missing" in response.text
    assert not missing.exists()


def test_handle_inbound_records_echo_on_stub_port(tmp_path: Path) -> None:
    inbound = parse_inbound(inbound_fixture())
    assert inbound is not None
    port = StubLinq()
    settings = Settings(
        _env_file=None,
        linq_api_token=None,
        linq_from_e164="+15555550100",
        call_agent_artifacts_dir=tmp_path / "artifacts",
    )
    settings.call_agent_artifacts_dir.mkdir(parents=True, exist_ok=True)

    receipt = handle_inbound(inbound, settings, port)

    assert receipt is not None
    assert port.last_path == f"/chats/{INBOUND_CHAT_ID}/messages"
    assert port.last_payload == {
        "message": {
            "parts": [{"type": "text", "value": "Received: reply"}],
            "preferred_service": "iMessage",
        }
    }


def test_linq_webhook_route_fails_without_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINQ_WEBHOOK_SIGNING_SECRET", "")
    client = TestClient(app)
    denied = client.post("/webhooks/linq", content=inbound_fixture())

    assert denied.status_code == 401


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
        "inbound_reply",
        "signature",
        "live_token",
        "imessage_only",
    ]
    assert all(check.passed for check in report.checks)
