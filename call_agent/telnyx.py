from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Annotated, Any, Literal, Protocol, TypeAlias

import httpx
from fastapi import WebSocket
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .domain import (
    CallControlId,
    CallEvent,
    ClientState,
    Codec,
    DialPlacedEvent,
    DialSpec,
    E164,
    MediaChunkEvent,
    MediaStartEvent,
    MediaStopEvent,
    StreamId,
    WebhookEvent,
)


class _WireModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        populate_by_name=True,
    )


class _DialPayload(_WireModel):
    connection_id: str
    to: E164
    from_did: E164 = Field(serialization_alias="from")
    stream_url: str
    webhook_url: str
    client_state: str
    stream_track: Literal["inbound_track"] = "inbound_track"
    stream_bidirectional_mode: Literal["rtp"] = "rtp"
    stream_bidirectional_target_legs: Literal["self"] = "self"
    stream_bidirectional_codec: Codec
    stream_codec: Codec


class _DialResponseData(_WireModel):
    call_control_id: CallControlId
    call_leg_id: str
    call_session_id: str


class _DialResponse(_WireModel):
    data: _DialResponseData


class _WebhookPayload(_WireModel):
    call_control_id: CallControlId
    hangup_cause: str | None = None
    hangup_source: str | None = None


class _WebhookData(_WireModel):
    event_type: Literal[
        "call.initiated",
        "call.answered",
        "streaming.started",
        "streaming.stopped",
        "call.hangup",
    ]
    id: str
    payload: _WebhookPayload


class _WebhookEnvelope(_WireModel):
    data: _WebhookData


class _L16Format(_WireModel):
    encoding: Literal[Codec.L16]
    sample_rate: Literal[16000]
    channels: Literal[1]


class _PcmuFormat(_WireModel):
    encoding: Literal[Codec.PCMU]
    sample_rate: Literal[8000]
    channels: Literal[1]


_MediaFormat: TypeAlias = Annotated[
    _L16Format | _PcmuFormat,
    Field(discriminator="encoding"),
]


class _StartBody(_WireModel):
    call_control_id: CallControlId
    media_format: _MediaFormat


class _StartFrame(_WireModel):
    event: Literal["start"]
    stream_id: StreamId
    start: _StartBody


class _MediaBody(_WireModel):
    payload: str


class _MediaFrame(_WireModel):
    event: Literal["media"]
    stream_id: StreamId
    media: _MediaBody


class _StopFrame(_WireModel):
    event: Literal["stop"]
    stream_id: StreamId


_InboundMediaFrame: TypeAlias = Annotated[
    _StartFrame | _MediaFrame | _StopFrame,
    Field(discriminator="event"),
]
_MEDIA_ADAPTER = TypeAdapter(_InboundMediaFrame)


class _AvailableNumber(_WireModel):
    phone_number: E164


class _AvailableNumbersResponse(_WireModel):
    data: tuple[_AvailableNumber, ...]


class _OrderedPhone(_WireModel):
    id: str | None = None
    phone_number: E164


class _NumberOrderData(_WireModel):
    id: str
    status: Literal["pending", "success"]
    phone_numbers: tuple[_OrderedPhone, ...]


class _NumberOrderResponse(_WireModel):
    data: _NumberOrderData


class OrderedNumber(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    order_id: str
    did: E164 | None
    telnyx_number_id: str | None


class TelnyxHttp(Protocol):
    def search_and_order_number(self, connection_id: str) -> OrderedNumber: ...

    def get_order(self, order_id: str) -> OrderedNumber: ...

    def dial(self, spec: DialSpec) -> DialPlacedEvent: ...

    def hangup(self, call_control_id: CallControlId) -> None: ...


class TelnyxMedia(Protocol):
    async def play(self, payload_b64: str) -> None: ...

    async def close(self) -> None: ...


def serialize_dial(spec: DialSpec) -> dict[str, Any]:
    payload = _DialPayload(
        connection_id=spec.connection_id,
        to=spec.to,
        from_did=spec.from_did,
        stream_url=spec.stream_url,
        webhook_url=spec.webhook_url,
        client_state=spec.client_state.encode(),
        stream_bidirectional_codec=spec.codec,
        stream_codec=spec.codec,
    )
    return payload.model_dump(mode="json", by_alias=True)


def parse_webhook(raw: bytes) -> WebhookEvent:
    envelope = _WebhookEnvelope.model_validate_json(raw)
    data = envelope.data
    return WebhookEvent(
        webhook_id=data.id,
        event_type=data.event_type,
        call_control_id=data.payload.call_control_id,
        hangup_cause=data.payload.hangup_cause,
        hangup_source=data.payload.hangup_source,
    )


def parse_media_frame(
    raw: str,
) -> MediaStartEvent | MediaChunkEvent | MediaStopEvent:
    frame = _MEDIA_ADAPTER.validate_json(raw)
    if isinstance(frame, _StartFrame):
        return MediaStartEvent(
            stream_id=frame.stream_id,
            call_control_id=frame.start.call_control_id,
            codec=frame.start.media_format.encoding,
        )
    if isinstance(frame, _MediaFrame):
        return MediaChunkEvent(
            stream_id=frame.stream_id,
            payload_b64=frame.media.payload,
        )
    return MediaStopEvent(stream_id=frame.stream_id)


class LiveTelnyxHttp:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.telnyx.com/v2",
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    def search_and_order_number(self, connection_id: str) -> OrderedNumber:
        available_response = self._client.get(
            f"{self._base_url}/available_phone_numbers",
            params={
                "filter[country_code]": "US",
                "filter[features][]": "voice",
                "filter[limit]": 1,
            },
        )
        available_response.raise_for_status()
        available = _AvailableNumbersResponse.model_validate(
            available_response.json()
        )
        if not available.data:
            raise RuntimeError("Telnyx returned no available voice number")
        response = self._client.post(
            f"{self._base_url}/number_orders",
            json={
                "phone_numbers": [
                    {"phone_number": str(available.data[0].phone_number)}
                ],
                "connection_id": connection_id,
            },
        )
        response.raise_for_status()
        return self._parse_order(response.json())

    def get_order(self, order_id: str) -> OrderedNumber:
        response = self._client.get(
            f"{self._base_url}/number_orders/{order_id}"
        )
        response.raise_for_status()
        return self._parse_order(response.json())

    def dial(self, spec: DialSpec) -> DialPlacedEvent:
        response = self._client.post(
            f"{self._base_url}/calls",
            json=serialize_dial(spec),
        )
        response.raise_for_status()
        parsed = _DialResponse.model_validate(response.json()).data
        return DialPlacedEvent(
            call_control_id=parsed.call_control_id,
            call_leg_id=parsed.call_leg_id,
            call_session_id=parsed.call_session_id,
        )

    def hangup(self, call_control_id: CallControlId) -> None:
        response = self._client.post(
            f"{self._base_url}/calls/{call_control_id}/actions/hangup",
            json={},
        )
        if response.status_code in (404, 409, 422):
            return
        response.raise_for_status()

    @staticmethod
    def _parse_order(raw: dict[str, Any]) -> OrderedNumber:
        order = _NumberOrderResponse.model_validate(raw).data
        phone = order.phone_numbers[0] if order.phone_numbers else None
        active = order.status == "success" and phone is not None
        return OrderedNumber(
            order_id=order.id,
            did=phone.phone_number if active else None,
            telnyx_number_id=phone.id if active else None,
        )


class LiveTelnyxMedia:
    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def play(self, payload_b64: str) -> None:
        await self._websocket.send_json(
            {"event": "media", "media": {"payload": payload_b64}}
        )

    async def close(self) -> None:
        await self._websocket.close()


Submit = Callable[[CallEvent], None]
WriteJson = Callable[[str, dict[str, Any]], None]
AppendAudio = Callable[[str], None]


def _ignore_event(event: CallEvent) -> None:
    return None


def _ignore_json(name: str, payload: dict[str, Any]) -> None:
    return None


def _ignore_audio(payload_b64: str) -> None:
    return None


class StubTelnyxMedia:
    def __init__(
        self,
        submit: Submit = _ignore_event,
        append_outbound: AppendAudio = _ignore_audio,
    ) -> None:
        self._submit = submit
        self._append_outbound = append_outbound

    def begin(self) -> None:
        start = json.dumps(
            {
                "event": "start",
                "stream_id": "stub-stream-0001",
                "start": {
                    "call_control_id": "stub-call-0001",
                    "media_format": {
                        "encoding": "L16",
                        "sample_rate": 16000,
                        "channels": 1,
                    },
                },
            }
        )
        self._submit(parse_media_frame(start))
        payload = base64.b64encode(bytes(640)).decode()
        for chunk in range(1, 4):
            raw = json.dumps(
                {
                    "event": "media",
                    "stream_id": "stub-stream-0001",
                    "media": {
                        "payload": payload,
                        "track": "inbound",
                        "chunk": chunk,
                        "timestamp": str(chunk * 20),
                    },
                }
            )
            self._submit(parse_media_frame(raw))

    def stop(self) -> None:
        self._submit(
            parse_media_frame(
                json.dumps(
                    {
                        "event": "stop",
                        "stream_id": "stub-stream-0001",
                    }
                )
            )
        )

    async def play(self, payload_b64: str) -> None:
        self._append_outbound(payload_b64)

    async def close(self) -> None:
        return None


class StubTelnyxHttp:
    def __init__(
        self,
        submit: Submit = _ignore_event,
        media: StubTelnyxMedia | None = None,
        write_json: WriteJson = _ignore_json,
        dial_override: dict[str, Any] | None = None,
        far_party_hangs_up: bool = False,
    ) -> None:
        self._submit = submit
        self._media = media or StubTelnyxMedia(submit)
        self._write_json = write_json
        self._dial_override = dial_override or {}
        self._far_party_hangs_up = far_party_hangs_up
        self._orders: dict[str, OrderedNumber] = {}
        self._number_counter = 0
        self._hung_up = False

    def search_and_order_number(self, connection_id: str) -> OrderedNumber:
        number = E164(f"+15555550{100 + self._number_counter:03d}")
        self._number_counter += 1
        order_id = f"stub-order-{self._number_counter:04d}"
        ordered = OrderedNumber(
            order_id=order_id,
            did=number,
            telnyx_number_id=f"stub-number-{self._number_counter:04d}",
        )
        self._orders[order_id] = ordered
        return ordered

    def get_order(self, order_id: str) -> OrderedNumber:
        return self._orders[order_id]

    def dial(self, spec: DialSpec) -> DialPlacedEvent:
        self._write_json(
            "dial.json", serialize_dial(spec) | self._dial_override
        )
        placed = DialPlacedEvent(
            call_control_id="stub-call-0001",
            call_leg_id="stub-leg-0001",
            call_session_id="stub-session-0001",
        )
        self._submit(
            parse_webhook(
                self._webhook(
                    "webhook-initiated-0001",
                    "call.initiated",
                )
            )
        )
        self._submit(
            parse_webhook(
                self._webhook(
                    "webhook-answered-0001",
                    "call.answered",
                )
            )
        )
        self._media.begin()
        self._submit(
            parse_webhook(
                self._webhook(
                    "webhook-streaming-started-0001",
                    "streaming.started",
                )
            )
        )
        if self._far_party_hangs_up:
            self._hung_up = True
            self._media.stop()
            self._submit(
                parse_webhook(
                    self._webhook(
                        "webhook-hangup-0001",
                        "call.hangup",
                        hangup_cause="normal_clearing",
                        hangup_source="callee",
                    )
                )
            )
        return placed

    def hangup(self, call_control_id: CallControlId) -> None:
        if self._hung_up:
            return
        self._hung_up = True
        self._write_json(
            "hangup.json", {"call_control_id": str(call_control_id)}
        )
        self._media.stop()
        self._submit(
            parse_webhook(
                self._webhook(
                    "webhook-streaming-stopped-0001",
                    "streaming.stopped",
                )
            )
        )
        self._submit(
            parse_webhook(
                self._webhook(
                    "webhook-hangup-0001",
                    "call.hangup",
                    hangup_cause="normal_clearing",
                    hangup_source="caller",
                )
            )
        )

    @staticmethod
    def _webhook(
        webhook_id: str,
        event_type: str,
        hangup_cause: str | None = None,
        hangup_source: str | None = None,
    ) -> bytes:
        payload: dict[str, Any] = {
            "call_control_id": "stub-call-0001",
            "call_leg_id": "stub-leg-0001",
            "call_session_id": "stub-session-0001",
            "connection_id": "stub-connection",
            "client_state": "stub",
            "from": "+15555550100",
            "to": "+14155552671",
            "direction": "outgoing",
        }
        if hangup_cause is not None:
            payload["hangup_cause"] = hangup_cause
        if hangup_source is not None:
            payload["hangup_source"] = hangup_source
        return json.dumps(
            {
                "data": {
                    "event_type": event_type,
                    "id": webhook_id,
                    "occurred_at": "2026-09-11T00:00:00Z",
                    "record_type": "event",
                    "payload": payload,
                }
            }
        ).encode()
