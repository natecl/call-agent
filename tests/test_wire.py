import json

import pytest
from pydantic import ValidationError

from call_agent.domain import (
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
from call_agent.gpt_live import parse_event, serialize_session_start
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
        ),
        MediaChunkEvent(
            stream_id=StreamId("stream-1"),
            payload_b64="AAE=",
        ),
        MediaStopEvent(stream_id=StreamId("stream-1")),
    )


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
        ),
    )
    with pytest.raises(ValidationError, match="'error'"):
        parse_media_frame(error)


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


def test_parse_gpt_live_rejects_realtime_and_ignores_usage() -> None:
    with pytest.raises(ValueError, match="Realtime event"):
        parse_event(json.dumps({"type": "session.update"}))
    with pytest.raises(ValueError, match="Realtime event"):
        parse_event(json.dumps({"type": "conversation.item.create"}))
    with pytest.raises(ValueError, match="Realtime event"):
        parse_event(json.dumps({"type": "response.create"}))

    assert parse_event(json.dumps({"type": "session.usage.updated"})) is None
