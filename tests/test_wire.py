import asyncio
import json
from typing import Any

import pytest
from pydantic import ValidationError

from call_agent.domain import (
    CallEvent,
    ClientState,
    Codec,
    DialSpec,
    E164,
    LiveAudioFormat,
    LiveClosedEvent,
    LiveInputTranscriptEvent,
    LiveOutputAudioEvent,
    LiveOutputTranscriptEvent,
    LiveSessionSpec,
    LiveStartedEvent,
    MediaChunkEvent,
    MediaStartEvent,
    MediaStopEvent,
    RunId,
    StreamId,
    UserId,
    WebhookEvent,
)
from call_agent.gpt_live import LiveGptLive, parse_event, serialize_session_start
from call_agent.telnyx import (
    parse_media_frame,
    parse_webhook,
    serialize_dial,
)


def test_serialize_dial_matches_call_control_payload() -> None:
    spec = DialSpec(
        connection_id="connection-1",
        to=E164("+14155552671"),
        from_did=E164("+15555550100"),
        stream_url="wss://calls.example.test/media-stream",
        webhook_url="https://calls.example.test/webhooks/telnyx",
        codec=Codec.L16,
        client_state=ClientState(
            run_id=RunId("run-1"),
            user_id=UserId("user-1"),
        ),
    )

    payload = serialize_dial(spec)

    assert payload == {
        "connection_id": "connection-1",
        "to": "+14155552671",
        "from": "+15555550100",
        "stream_url": "wss://calls.example.test/media-stream",
        "webhook_url": "https://calls.example.test/webhooks/telnyx",
        "client_state": (
            "eyJydW5faWQiOiJydW4tMSIsInVzZXJfaWQiOiJ1c2VyLTEifQ=="
        ),
        "stream_track": "inbound_track",
        "stream_bidirectional_mode": "rtp",
        "stream_bidirectional_target_legs": "self",
        "stream_bidirectional_codec": "L16",
        "stream_bidirectional_sampling_rate": 16000,
        "stream_codec": "L16",
    }


def test_parse_webhook_returns_domain_event() -> None:
    raw = json.dumps(
        {
            "data": {
                "event_type": "call.answered",
                "id": "webhook-1",
                "occurred_at": "2026-09-11T00:00:00Z",
                "record_type": "event",
                "payload": {
                    "call_control_id": "v3:abc",
                    "call_leg_id": "leg-1",
                    "call_session_id": "session-1",
                    "connection_id": "connection-1",
                    "client_state": "opaque",
                    "from": "+15555550100",
                    "to": "+14155552671",
                    "direction": "outgoing",
                    "state": "answered",
                },
            }
        }
    ).encode()

    event = parse_webhook(raw)

    assert event == WebhookEvent(
        webhook_id="webhook-1",
        event_type="call.answered",
        call_control_id="v3:abc",
        hangup_cause=None,
        hangup_source=None,
    )


def test_parse_webhook_streaming_failed_carries_failure_reason() -> None:
    raw = json.dumps(
        {
            "data": {
                "event_type": "streaming.failed",
                "id": "webhook-2",
                "occurred_at": "2026-09-11T00:00:00Z",
                "record_type": "event",
                "payload": {
                    "call_control_id": "v3:abc",
                    "call_leg_id": "leg-1",
                    "call_session_id": "session-1",
                    "connection_id": "connection-1",
                    "client_state": "opaque",
                    "failure_reason": "connection_failed",
                    "stream_id": "stream-1",
                    "stream_type": "websocket",
                    "stream_params": {
                        "stream_url": "wss://calls.example.test/media-stream",
                        "track": "inbound_track",
                    },
                },
            }
        }
    ).encode()

    event = parse_webhook(raw)

    assert event == WebhookEvent(
        webhook_id="webhook-2",
        event_type="streaming.failed",
        call_control_id="v3:abc",
        hangup_cause=None,
        hangup_source=None,
        failure_reason="connection_failed",
    )


def test_parse_media_frames_returns_domain_events() -> None:
    start = json.dumps(
        {
            "event": "start",
            "stream_id": "stream-1",
            "start": {
                "call_control_id": "v3:abc",
                "media_format": {
                    "encoding": "L16",
                    "sample_rate": 16000,
                    "channels": 1,
                },
            },
        }
    )
    media = json.dumps(
        {
            "event": "media",
            "stream_id": "stream-1",
            "media": {
                "payload": "AAE=",
                "track": "inbound",
                "chunk": 1,
                "timestamp": "20",
            },
        }
    )
    stop = json.dumps({"event": "stop", "stream_id": "stream-1"})

    parsed = (
        parse_media_frame(start),
        parse_media_frame(media),
        parse_media_frame(stop),
    )

    assert parsed == (
        MediaStartEvent(
            stream_id=StreamId("stream-1"),
            call_control_id="v3:abc",
            codec=Codec.L16,
            sample_rate=16000,
        ),
        MediaChunkEvent(
            stream_id=StreamId("stream-1"),
            payload_b64="AAE=",
        ),
        MediaStopEvent(stream_id=StreamId("stream-1")),
    )


def test_parse_media_mark_echo_returns_domain_event() -> None:
    mark = json.dumps(
        {
            "event": "mark",
            "stream_id": "stream-1",
            "sequence_number": "5",
            "mark": {"name": "hangup-1"},
        }
    )

    parsed = parse_media_frame(mark)

    assert parsed.model_dump(mode="json") == {
        "kind": "media_mark",
        "stream_id": "stream-1",
        "name": "hangup-1",
    }


def test_parse_media_frame_skips_connected_and_dtmf_and_rejects_error() -> None:
    connected = json.dumps({"event": "connected", "version": "1.0.0"})
    dtmf = json.dumps(
        {
            "event": "dtmf",
            "stream_id": "stream-1",
            "occurred_at": "2026-09-11T00:00:00Z",
            "sequence_number": "5",
            "dtmf": {"digit": "1"},
        }
    )
    start = json.dumps(
        {
            "event": "start",
            "sequence_number": "1",
            "stream_id": "stream-1",
            "start": {
                "call_control_id": "v3:abc",
                "media_format": {
                    "encoding": "PCMU",
                    "sample_rate": 8000,
                    "channels": 1,
                },
            },
        }
    )
    error = json.dumps(
        {
            "event": "error",
            "stream_id": "stream-1",
            "payload": {
                "code": 100004,
                "title": "invalid_media",
                "detail": "Media provided was not base64 encoded",
            },
        }
    )

    parsed = (
        parse_media_frame(connected),
        parse_media_frame(dtmf),
        parse_media_frame(start),
    )

    assert parsed == (
        None,
        None,
        MediaStartEvent(
            stream_id=StreamId("stream-1"),
            call_control_id="v3:abc",
            codec=Codec.PCMU,
            sample_rate=8000,
        ),
    )
    with pytest.raises(ValidationError, match="'error'"):
        parse_media_frame(error)


def _start_frame(encoding: str, sample_rate: int) -> str:
    return json.dumps(
        {
            "event": "start",
            "stream_id": "stream-1",
            "start": {
                "call_control_id": "v3:abc",
                "media_format": {
                    "encoding": encoding,
                    "sample_rate": sample_rate,
                    "channels": 1,
                },
            },
        }
    )


def test_parse_media_start_accepts_l16_at_8000_and_rejects_other_rates() -> None:
    parsed = parse_media_frame(_start_frame("L16", 8000))

    assert parsed == MediaStartEvent(
        stream_id=StreamId("stream-1"),
        call_control_id="v3:abc",
        codec=Codec.L16,
        sample_rate=8000,
    )
    with pytest.raises(ValidationError, match="sample_rate"):
        parse_media_frame(_start_frame("L16", 48000))
    with pytest.raises(ValidationError, match="sample_rate"):
        parse_media_frame(_start_frame("PCMU", 16000))


def test_serialize_session_start_matches_gpt_live_payload() -> None:
    spec = LiveSessionSpec(
        model="gpt-live-1",
        instructions="Call on behalf of Nathan.",
        audio=LiveAudioFormat(type="audio/pcm", rate=16000),
        voice="marin",
    )

    payload = serialize_session_start(spec)

    assert payload == {
        "type": "session.start",
        "session": {
            "model": "gpt-live-1",
            "instructions": "Call on behalf of Nathan.",
            "audio": {
                "format": {"type": "audio/pcm", "rate": 16000},
                "output": {"voice": "marin"},
            },
        },
    }


def test_parse_gpt_live_events_returns_domain_events() -> None:
    parsed = (
        parse_event(
            json.dumps(
                {
                    "type": "session.started",
                    "session": {"id": "live-1"},
                }
            )
        ),
        parse_event(
            json.dumps(
                {
                    "type": "session.input_transcript.delta",
                    "delta": "Open until five.",
                }
            )
        ),
        parse_event(
            json.dumps(
                {
                    "type": "session.output_transcript.delta",
                    "delta": "Thanks.",
                }
            )
        ),
        parse_event(
            json.dumps(
                {
                    "type": "session.output_audio.delta",
                    "delta": "AAA=",
                }
            )
        ),
        parse_event(json.dumps({"type": "session.closed"})),
    )

    assert parsed == (
        LiveStartedEvent(session_id="live-1"),
        LiveInputTranscriptEvent(delta="Open until five."),
        LiveOutputTranscriptEvent(delta="Thanks."),
        LiveOutputAudioEvent(delta_b64="AAA="),
        LiveClosedEvent(),
    )


class FlushingLiveSocket:
    def __init__(self, replies_after_close: list[dict[str, Any]]) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._replies_after_close = replies_after_close
        self._frames: asyncio.Queue[str | None] = asyncio.Queue()

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        if message["type"] == "session.close":
            for reply in self._replies_after_close:
                self._frames.put_nowait(json.dumps(reply))
            self._frames.put_nowait(None)

    async def close(self) -> None:
        self.closed = True
        self._frames.put_nowait(None)

    def __aiter__(self) -> "FlushingLiveSocket":
        return self

    async def __anext__(self) -> str:
        frame = await self._frames.get()
        if frame is None:
            raise StopAsyncIteration
        return frame


@pytest.mark.parametrize(
    "replies_after_close",
    [
        [{"type": "session.output_audio.delta", "delta": "AAA="}],
        [
            {"type": "session.output_audio.delta", "delta": "AAA="},
            {"type": "session.closed"},
        ],
    ],
    ids=["server_closes_silently", "server_sends_session_closed"],
)
async def test_live_gpt_live_close_flushes_audio_then_reports_closed_once(
    monkeypatch: pytest.MonkeyPatch,
    replies_after_close: list[dict[str, Any]],
) -> None:
    socket = FlushingLiveSocket(replies_after_close)

    async def fake_connect(endpoint: str, additional_headers: dict[str, str]):
        return socket

    monkeypatch.setattr("call_agent.gpt_live.connect", fake_connect)
    events: list[CallEvent] = []
    live = LiveGptLive(
        endpoint="wss://live.example.test/v1/live/sessions",
        api_key="key",
        extra_headers=None,
        submit=events.append,
    )

    await live.start(
        LiveSessionSpec(
            model="gpt-live-1",
            instructions="Call on behalf of Nathan.",
            audio=LiveAudioFormat(type="audio/pcm", rate=16000),
            voice="marin",
        )
    )
    await live.close()
    await live.close()

    assert [message["type"] for message in socket.sent] == [
        "session.start",
        "session.close",
    ]
    assert events == [LiveOutputAudioEvent(delta_b64="AAA="), LiveClosedEvent()]
    assert socket.closed is True


def test_parse_gpt_live_rejects_realtime_and_ignores_usage() -> None:
    with pytest.raises(ValueError, match="Realtime event"):
        parse_event(json.dumps({"type": "session.update"}))
    with pytest.raises(ValueError, match="Realtime event"):
        parse_event(json.dumps({"type": "conversation.item.create"}))
    with pytest.raises(ValueError, match="Realtime event"):
        parse_event(json.dumps({"type": "response.create"}))

    assert parse_event(json.dumps({"type": "session.usage.updated"})) is None
