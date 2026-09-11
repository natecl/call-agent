from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from typing import Annotated, Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field
from websockets.asyncio.client import ClientConnection, connect

from .domain import (
    CLOSING_LINE,
    CallEvent,
    LiveClosedEvent,
    LiveErrorEvent,
    LiveInputTranscriptEvent,
    LiveOutputAudioEvent,
    LiveOutputTranscriptEvent,
    LiveSessionSpec,
    LiveStartedEvent,
    SampleRate,
)


class _WireModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class _PcmFormat(_WireModel):
    type: Literal["audio/pcm"]
    rate: Literal[16000]


class _PcmuFormat(_WireModel):
    type: Literal["audio/pcmu"]
    rate: Literal[8000]


_AudioFormat: TypeAlias = Annotated[
    _PcmFormat | _PcmuFormat,
    Field(discriminator="type"),
]


class _AudioOutput(_WireModel):
    voice: str


class _SessionAudio(_WireModel):
    format: _AudioFormat
    output: _AudioOutput


class _SessionConfig(_WireModel):
    model: str
    instructions: str
    audio: _SessionAudio


class _SessionStart(_WireModel):
    type: Literal["session.start"] = "session.start"
    session: _SessionConfig


class _StartedSession(_WireModel):
    id: str


class _SessionStarted(_WireModel):
    type: Literal["session.started"]
    session: _StartedSession


class _InputTranscriptDelta(_WireModel):
    type: Literal["session.input_transcript.delta"]
    delta: str


class _OutputTranscriptDelta(_WireModel):
    type: Literal["session.output_transcript.delta"]
    delta: str


class _OutputAudioDelta(_WireModel):
    type: Literal["session.output_audio.delta"]
    delta: str


class _ErrorBody(_WireModel):
    message: str


class _ErrorEvent(_WireModel):
    type: Literal["error"]
    error: _ErrorBody


class _SessionClosed(_WireModel):
    type: Literal["session.closed"]


class GptLive(Protocol):
    async def start(self, spec: LiveSessionSpec) -> None: ...

    async def speak_first(self, line: str) -> None: ...

    async def append_audio(self, payload_b64: str) -> None: ...

    async def close(self) -> None: ...


def serialize_session_start(spec: LiveSessionSpec) -> dict[str, Any]:
    payload = _SessionStart(
        session=_SessionConfig(
            model=spec.model,
            instructions=spec.instructions,
            audio=_SessionAudio(
                format=spec.audio.model_dump(),
                output=_AudioOutput(voice=spec.voice),
            ),
        )
    )
    return payload.model_dump(mode="json")


def parse_event(raw: str) -> CallEvent | None:
    decoded = json.loads(raw)
    event_type = decoded.get("type")
    if not isinstance(event_type, str):
        raise ValueError("GPT-Live event has no string type")
    if event_type == "session.started":
        event = _SessionStarted.model_validate(decoded)
        return LiveStartedEvent(session_id=event.session.id)
    if event_type == "session.input_transcript.delta":
        event = _InputTranscriptDelta.model_validate(decoded)
        return LiveInputTranscriptEvent(delta=event.delta)
    if event_type == "session.output_transcript.delta":
        event = _OutputTranscriptDelta.model_validate(decoded)
        return LiveOutputTranscriptEvent(delta=event.delta)
    if event_type == "session.output_audio.delta":
        event = _OutputAudioDelta.model_validate(decoded)
        return LiveOutputAudioEvent(delta_b64=event.delta)
    if event_type == "error":
        event = _ErrorEvent.model_validate(decoded)
        return LiveErrorEvent(message=event.error.message)
    if event_type == "session.closed":
        _SessionClosed.model_validate(decoded)
        return LiveClosedEvent()
    if event_type == "session.usage.updated":
        return None
    if (
        event_type in {"session.update", "session.created"}
        or event_type.startswith("conversation.item.")
        or event_type.startswith("response.")
        or event_type.startswith("input_audio_buffer.")
    ):
        raise ValueError(f"Realtime event is invalid for GPT-Live: {event_type}")
    return None


Submit = Callable[[CallEvent], None]
WriteJson = Callable[[str, dict[str, Any]], None]


def _ignore_event(event: CallEvent) -> None:
    return None


def _ignore_json(name: str, payload: dict[str, Any]) -> None:
    return None


class LiveGptLive:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        extra_headers: str | None,
        submit: Submit,
        write_json: WriteJson = _ignore_json,
    ) -> None:
        self._endpoint = endpoint
        self._headers = {"Authorization": f"Bearer {api_key}"}
        if extra_headers:
            name, separator, value = extra_headers.partition(":")
            if not separator or not name.strip() or not value.strip():
                raise ValueError(
                    "OPENAI_LIVE_EXTRA_HEADERS must be 'Name: value'"
                )
            self._headers[name.strip()] = value.strip()
        self._submit = submit
        self._write_json = write_json
        self._connection: ClientConnection | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._closing = False

    async def start(self, spec: LiveSessionSpec) -> None:
        payload = serialize_session_start(spec)
        self._write_json("session_start.json", payload)
        self._connection = await connect(
            self._endpoint,
            additional_headers=self._headers,
        )
        await self._connection.send(json.dumps(payload))
        self._reader_task = asyncio.create_task(self._read_events())

    async def speak_first(self, line: str) -> None:
        await self._send(
            {
                "type": "session.instructions.append",
                "delegation_id": None,
                "content": f"Your first spoken line is, verbatim: {line}",
            }
        )
        await self._send(
            {
                "type": "session.commentary.append",
                "delegation_id": None,
                "content": line,
            }
        )

    async def append_audio(self, payload_b64: str) -> None:
        await self._send(
            {
                "type": "session.input_audio.append",
                "audio": payload_b64,
            }
        )

    async def close(self) -> None:
        if self._connection is None or self._closing:
            return
        self._closing = True
        await self._connection.send(json.dumps({"type": "session.close"}))
        await self._connection.close()
        if (
            self._reader_task is not None
            and self._reader_task is not asyncio.current_task()
        ):
            await self._reader_task

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._connection is None:
            raise RuntimeError("GPT-Live session has not started")
        await self._connection.send(json.dumps(payload))

    async def _read_events(self) -> None:
        if self._connection is None:
            return
        try:
            async for raw in self._connection:
                event = parse_event(str(raw))
                if event is not None:
                    self._submit(event)
        except Exception as error:
            if not self._closing:
                self._submit(LiveErrorEvent(message=str(error)))


class StubScenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    canned_answer: str | None = None
    skip_disclosure: bool = False
    dial_override: dict[str, Any] = {}
    far_party_hangs_up: bool = False
    media_sample_rate: SampleRate = 16000


class StubGptLive:
    def __init__(
        self,
        submit: Submit = _ignore_event,
        write_json: WriteJson = _ignore_json,
        scenario: StubScenario | None = None,
    ) -> None:
        self._submit = submit
        self._write_json = write_json
        self._scenario = scenario or StubScenario()
        self._closed = False
        self.appended_audio: list[str] = []
        self.spoken_lines: list[str] = []

    async def start(self, spec: LiveSessionSpec) -> None:
        self._write_json(
            "session_start.json", serialize_session_start(spec)
        )
        self._submit(
            self._parse(
                {
                    "type": "session.started",
                    "session": {"id": "stub-live-0001"},
                }
            )
        )
        lines = spec.instructions.splitlines()
        first_line = lines[1]
        ask = lines[2]
        opening = (
            f"I have one quick question. {ask}"
            if self._scenario.skip_disclosure
            else f"{first_line} {ask}"
        )
        first_half, second_half = self._halves(opening)
        self._submit(
            self._parse(
                {
                    "type": "session.output_transcript.delta",
                    "delta": first_half,
                }
            )
        )
        self._submit(
            self._parse(
                {
                    "type": "session.output_transcript.delta",
                    "delta": second_half,
                }
            )
        )
        answer = self._scenario.canned_answer or self._answer_for(ask)
        first_half, second_half = self._halves(answer)
        self._submit(
            self._parse(
                {
                    "type": "session.input_transcript.delta",
                    "delta": first_half,
                }
            )
        )
        self._submit(
            self._parse(
                {
                    "type": "session.input_transcript.delta",
                    "delta": second_half,
                }
            )
        )
        audio = base64.b64encode(bytes(640)).decode()
        for _ in range(2):
            self._submit(
                self._parse(
                    {
                        "type": "session.output_audio.delta",
                        "delta": audio,
                    }
                )
            )
        self._submit(
            self._parse(
                {
                    "type": "session.output_transcript.delta",
                    "delta": CLOSING_LINE,
                }
            )
        )

    async def speak_first(self, line: str) -> None:
        self.spoken_lines.append(line)

    async def append_audio(self, payload_b64: str) -> None:
        self.appended_audio.append(payload_b64)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._submit(self._parse({"type": "session.closed"}))

    @staticmethod
    def _halves(text: str) -> tuple[str, str]:
        split = len(text) // 2
        return text[:split], text[split:]

    @staticmethod
    def _answer_for(ask: str) -> str:
        lowered = ask.casefold()
        if "aisle" in lowered:
            return "That's in aisle 7."
        if "stock" in lowered:
            return "Yes, we have that in stock."
        return "We're open Monday through Friday, 9 AM to 5 PM."

    @staticmethod
    def _parse(payload: dict[str, Any]) -> CallEvent:
        event = parse_event(json.dumps(payload))
        if event is None:
            raise ValueError("stub fixture produced an ignored event")
        return event
