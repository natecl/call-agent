from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from typing import Annotated, Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from .domain import (
    CLOSING_LINE,
    CallEvent,
    LiveClosedEvent,
    LiveDelegationEvent,
    LiveErrorEvent,
    LiveInputTranscriptEvent,
    LiveNoteEvent,
    LiveOutputAudioEvent,
    LiveOutputTranscriptEvent,
    LiveResponseEvent,
    LiveSessionSpec,
    LiveStartedEvent,
    LiveToolCallEvent,
    ResponsesEffort,
    SampleRate,
)
from .tools import SESSION_TOOLS


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


class _FunctionTool(_WireModel):
    type: Literal["function"] = "function"
    name: str
    description: str
    strict: bool = True
    parameters: dict[str, Any]


class _ReasoningConfig(_WireModel):
    effort: ResponsesEffort


class _ResponsesConfig(_WireModel):
    model: str
    instructions: str
    tools: tuple[_FunctionTool, ...]
    tool_choice: Literal["auto"] = "auto"
    parallel_tool_calls: Literal[False] = False
    reasoning: _ReasoningConfig


class _Delegation(_WireModel):
    type: Literal["responses"] = "responses"
    responses: _ResponsesConfig


class _SessionConfig(_WireModel):
    model: str
    instructions: str
    audio: _SessionAudio
    delegation: _Delegation


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

    async def reply_delegation(
        self, delegation_id: str, thinking: str, commentary: str
    ) -> None: ...

    async def send_tool_result(
        self, call_id: str, output: str, event_id: str
    ) -> None: ...

    async def continue_response(self, event_id: str) -> None: ...

    async def steer(self, instructions: str, event_id: str) -> None: ...

    async def close(self) -> None: ...


def serialize_session_start(spec: LiveSessionSpec) -> dict[str, Any]:
    tools = spec.tools or SESSION_TOOLS
    payload = _SessionStart(
        session=_SessionConfig(
            model=spec.model,
            instructions=spec.instructions,
            audio=_SessionAudio(
                format=spec.audio.model_dump(),
                output=_AudioOutput(voice=spec.voice),
            ),
            delegation=_Delegation(
                responses=_ResponsesConfig(
                    model=spec.responses_model,
                    instructions=spec.backend_instructions,
                    tools=tuple(
                        _FunctionTool.model_validate(tool.model_dump())
                        for tool in tools
                    ),
                    reasoning=_ReasoningConfig(effort=spec.responses_effort),
                )
            ),
        )
    )
    return payload.model_dump(mode="json")


def speak_first_frames(line: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {
            "type": "session.instructions.append",
            "event_id": "speak-first-1",
            "delegation_id": None,
            "content": (
                f"Speak first, then listen. Your first spoken line is: {line}"
            ),
        },
        {
            "type": "session.commentary.append",
            "event_id": "speak-first-2",
            "delegation_id": None,
            "content": line,
        },
    )


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
    if event_type == "session.delegation.created":
        delegation = decoded.get("delegation")
        if not isinstance(delegation, dict):
            raise ValueError("GPT-Live delegation has no object")
        delegation_id = delegation.get("id")
        if not isinstance(delegation_id, str) or not delegation_id.strip():
            raise ValueError("GPT-Live delegation has no id")
        target = delegation.get("target")
        if target not in {"client", "responses"}:
            raise ValueError("GPT-Live delegation has no target")
        response_id = delegation.get("response_id")
        return LiveDelegationEvent(
            delegation_id=delegation_id,
            target=target,
            response_id=response_id if isinstance(response_id, str) else None,
        )
    if event_type == "response.event":
        return _parse_response_event(decoded)
    if (
        event_type in {"session.update", "session.created"}
        or event_type.startswith("conversation.item.")
        or event_type.startswith("response.")
        or event_type.startswith("input_audio_buffer.")
    ):
        raise ValueError(f"Realtime event is invalid for GPT-Live: {event_type}")
    usage_seconds = None
    if event_type == "session.usage.updated":
        usage = decoded.get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("seconds"), (int, float)):
            usage_seconds = float(usage["seconds"])
    client_event_id = decoded.get("client_event_id")
    return LiveNoteEvent(
        event_type=event_type,
        client_event_id=(
            client_event_id if isinstance(client_event_id, str) else None
        ),
        usage_seconds=usage_seconds,
    )


def _parse_response_event(decoded: dict[str, Any]) -> CallEvent:
    inner = decoded.get("event")
    if not isinstance(inner, dict):
        raise ValueError("GPT-Live response.event has no event")
    inner_type = inner.get("type")
    if not isinstance(inner_type, str):
        raise ValueError("GPT-Live response.event has no event type")
    delegation_id = decoded.get("delegation_id")
    if not isinstance(delegation_id, str) or not delegation_id.strip():
        raise ValueError("GPT-Live response.event has no delegation_id")
    response_id = None
    inner_response = inner.get("response")
    if isinstance(inner_response, dict) and isinstance(
        inner_response.get("id"), str
    ):
        response_id = inner_response["id"]
    elif isinstance(inner.get("response_id"), str):
        response_id = inner["response_id"]
    if inner_type == "response.output_item.done":
        item = inner.get("item")
        if isinstance(item, dict) and item.get("type") == "function_call":
            call_id = item.get("call_id")
            name = item.get("name")
            arguments = item.get("arguments")
            if isinstance(call_id, str) and isinstance(name, str):
                if isinstance(arguments, str):
                    arguments_json = arguments
                elif arguments is None:
                    arguments_json = "{}"
                else:
                    arguments_json = json.dumps(arguments)
                return LiveToolCallEvent(
                    delegation_id=delegation_id,
                    response_id=response_id,
                    call_id=call_id,
                    name=name,
                    arguments_json=arguments_json,
                )
        return LiveNoteEvent(event_type=f"response.event/{inner_type}")
    if inner_type == "response.created":
        return LiveResponseEvent(
            response_kind="created",
            delegation_id=delegation_id,
            response_id=response_id,
        )
    if inner_type in {
        "response.completed",
        "response.failed",
        "response.incomplete",
    }:
        return LiveResponseEvent(
            response_kind=inner_type.removeprefix("response."),
            delegation_id=delegation_id,
            response_id=response_id,
        )
    return LiveNoteEvent(event_type=f"response.event/{inner_type}")


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
        close_timeout_s: float = 5.0,
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
        self._close_timeout_s = close_timeout_s
        self._connection: ClientConnection | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._closing = False
        self._closed_reported = False

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
        for frame in speak_first_frames(line):
            await self._send(frame)

    async def append_audio(self, payload_b64: str) -> None:
        await self._send(
            {
                "type": "session.input_audio.append",
                "audio": payload_b64,
            }
        )

    async def reply_delegation(
        self, delegation_id: str, thinking: str, commentary: str
    ) -> None:
        await self._send(
            {
                "type": "session.thinking.append",
                "event_id": f"delegation-{delegation_id}-thinking",
                "delegation_id": delegation_id,
                "content": thinking,
            }
        )
        await self._send(
            {
                "type": "session.commentary.append",
                "event_id": f"delegation-{delegation_id}-commentary",
                "delegation_id": delegation_id,
                "content": commentary,
            }
        )

    async def send_tool_result(
        self, call_id: str, output: str, event_id: str
    ) -> None:
        await self._send(
            {
                "type": "response.item.create",
                "event_id": event_id,
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                },
            }
        )

    async def continue_response(self, event_id: str) -> None:
        await self._send(
            {
                "type": "response.create",
                "event_id": event_id,
            }
        )

    async def steer(self, instructions: str, event_id: str) -> None:
        await self._send(
            {
                "type": "session.instructions.append",
                "event_id": event_id,
                "delegation_id": None,
                "content": instructions,
            }
        )

    async def close(self) -> None:
        if self._connection is None or self._closing:
            return
        self._closing = True
        try:
            await self._connection.send(json.dumps({"type": "session.close"}))
        except ConnectionClosed:
            pass
        # The server flushes the audio it still holds and then ends the
        # session. Waiting for the reader keeps that audio ahead of the
        # single playback mark the reducer sends on LiveClosedEvent.
        reader = self._reader_task
        if reader is None or reader is asyncio.current_task():
            await self._connection.close()
            return
        await asyncio.wait({reader}, timeout=self._close_timeout_s)
        await self._connection.close()
        await reader

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
                if event is None:
                    continue
                if isinstance(event, LiveClosedEvent):
                    self._closed_reported = True
                self._submit(event)
        except Exception as error:
            if not self._closing:
                self._submit(LiveErrorEvent(message=str(error)))
                return
        if not self._closed_reported:
            self._closed_reported = True
            self._submit(LiveClosedEvent())


class StubScenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    canned_answer: str | None = None
    skip_disclosure: bool = False
    dial_override: dict[str, Any] = {}
    far_party_hangs_up: bool = False
    far_party_hangs_up_after_record: bool = False
    far_party_hangs_up_no_tools: bool = False
    media_sample_rate: SampleRate = 16000
    playback_never_drains: bool = False
    audio_chunks_after_closing_line: int = Field(default=2, ge=0)
    decline_booking: bool = False
    one_shot_booking: bool = False
    stall_after_time_turn: bool = False
    never_resumes: bool = False
    media_drops_before_hangup: bool = False
    tool_args_invalid: bool = False
    skips_end_call: bool = False
    client_delegation: bool = False
    placed_call_control_id: str = "stub-call-0001"


class StubGptLive:
    def __init__(
        self,
        submit: Submit = _ignore_event,
        write_json: WriteJson = _ignore_json,
        scenario: StubScenario | None = None,
        far_party_hangup: Callable[[], None] | None = None,
    ) -> None:
        self._submit = submit
        self._write_json = write_json
        self._scenario = scenario or StubScenario()
        self._far_party_hangup = far_party_hangup or (lambda: None)
        self._closed = False
        self.appended_audio: list[str] = []
        self.spoken_lines: list[str] = []
        self.replied_delegations: list[tuple[str, str, str]] = []
        self.tool_results: list[tuple[str, str, str]] = []
        self.continues: list[str] = []
        self.steers: list[tuple[str, str]] = []
        self._resume_booking: Callable[[], None] | None = None
        self._retried_invalid_tool = False

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
        if "I'd like to book a table" in spec.instructions:
            self._play_booking(first_line, ask)
            return
        opening = (
            f"I have one quick question. {ask}"
            if self._scenario.skip_disclosure
            else f"{first_line} {ask}"
        )
        self._emit_output(opening)
        answer = self._scenario.canned_answer or self._answer_for(ask)
        self._emit_input(answer)
        self._emit_audio(2)
        self._emit_task_tools(
            name="quick-ask",
            outcome="answered",
            summary=answer,
            answer_text=answer,
        )

    async def speak_first(self, line: str) -> None:
        self.spoken_lines.append(line)

    async def append_audio(self, payload_b64: str) -> None:
        self.appended_audio.append(payload_b64)

    async def reply_delegation(
        self, delegation_id: str, thinking: str, commentary: str
    ) -> None:
        self.replied_delegations.append((delegation_id, thinking, commentary))
        self._resume_if_waiting()

    async def send_tool_result(
        self, call_id: str, output: str, event_id: str
    ) -> None:
        self.tool_results.append((call_id, output, event_id))

    async def continue_response(self, event_id: str) -> None:
        self.continues.append(event_id)
        self._resume_if_waiting()

    async def steer(self, instructions: str, event_id: str) -> None:
        self.steers.append((instructions, event_id))
        self._resume_if_waiting()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._emit_audio(1)
        self._submit(self._parse({"type": "session.closed"}))

    def _resume_if_waiting(self) -> None:
        if self._scenario.never_resumes:
            return
        resume = self._resume_booking
        self._resume_booking = None
        if resume is not None:
            resume()

    def _play_booking(self, first_line: str, facts: str) -> None:
        opening = (
            f"I have a booking question. {facts}"
            if self._scenario.skip_disclosure
            else first_line
        )
        if self._scenario.one_shot_booking:
            self._emit_output(f"{opening} Can we get a table?")
            self._emit_input(
                self._scenario.canned_answer or "Yes, that works."
            )
            self._emit_audio(2)
            self._emit_closing()
            self._emit_audio(self._scenario.audio_chunks_after_closing_line)
            return
        self._emit_output(f"{opening} We need a table for that party size.")
        self._emit_input(f"A table for that party size is fine. {facts}")
        self._emit_output(f"Does {facts} work for the time?")
        self._emit_input(f"{facts} works for us.")
        if self._scenario.never_resumes:
            return
        if self._scenario.far_party_hangs_up_no_tools:
            summary = (
                self._scenario.canned_answer
                or (
                    f"You're booked. We'll see you then. "
                    f"Reservation confirmed. {facts}"
                )
            )
            self._emit_output(f"Any issue with the constraints in {facts}?")
            self._emit_input(summary)
            self._emit_output("Great, take care.")
            self._emit_audio(2)
            self._far_party_hangup()
            return
        if self._scenario.client_delegation:
            self._submit(
                self._parse(
                    {
                        "type": "session.delegation.created",
                        "delegation": {
                            "id": "stub-delegation-0001",
                            "type": "delegation",
                            "target": "client",
                        },
                    }
                )
            )
            self._resume_booking = lambda: self._finish_booking(facts)
            return
        self._emit_output(f"Any issue with the constraints in {facts}?")
        outcome = "declined" if self._scenario.decline_booking else "confirmed"
        summary = (
            self._scenario.canned_answer
            or (
                "We're fully booked. We can't take that reservation."
                if self._scenario.decline_booking
                else (
                    f"You're booked. We'll see you then. "
                    f"Reservation confirmed. {facts}"
                )
            )
        )
        if self._scenario.decline_booking:
            self._emit_input(
                self._scenario.canned_answer
                or "We're fully booked. We can't take that reservation."
            )
        self._emit_task_tools(
            name="book",
            outcome=outcome,
            summary=summary,
            facts=facts,
        )

    def _finish_booking(self, facts: str) -> None:
        self._emit_output(f"Any issue with the constraints in {facts}?")
        if self._scenario.decline_booking:
            self._emit_input(
                self._scenario.canned_answer
                or "We're fully booked. We can't take that reservation."
            )
        else:
            self._emit_input(
                self._scenario.canned_answer
                or (
                    f"You're booked. We'll see you then. "
                    f"Reservation confirmed. {facts}"
                )
            )
        self._emit_audio(2)
        self._emit_closing()
        self._emit_audio(self._scenario.audio_chunks_after_closing_line)

    def _emit_task_tools(
        self,
        *,
        name: str,
        outcome: str,
        summary: str,
        answer_text: str | None = None,
        facts: str | None = None,
    ) -> None:
        self._submit(
            self._parse(
                {
                    "type": "session.delegation.created",
                    "delegation": {
                        "id": "stub-delegation-0001",
                        "type": "delegation",
                        "target": "responses",
                        "response_id": "stub-response-0001",
                    },
                }
            )
        )
        self._submit(
            self._parse(
                {
                    "type": "response.event",
                    "event_id": "event_response_created",
                    "delegation_id": "stub-delegation-0001",
                    "event": {
                        "type": "response.created",
                        "response": {"id": "stub-response-0001"},
                    },
                }
            )
        )
        record_outcome = "booked" if self._scenario.tool_args_invalid else outcome
        arguments = {
            "outcome": record_outcome,
            "summary": summary,
            "confirmed_fields": {
                "party_size": 2 if name == "book" else None,
                "time_text": "Friday 7pm" if name == "book" else None,
                "name_on_record": "Nathan" if name == "book" else None,
                "answer_text": answer_text,
            },
        }
        if facts and "Party size:" in facts:
            # keep party/time from the live prompt facts line
            pass
        self._submit(self._function_call("call_record_1", "record_task_result", arguments))
        self._resume_booking = lambda: self._after_record(name, outcome, summary, facts)

    def _after_record(
        self,
        name: str,
        outcome: str,
        summary: str,
        facts: str | None,
    ) -> None:
        if self._scenario.tool_args_invalid and not self._retried_invalid_tool:
            self._retried_invalid_tool = True
            arguments = {
                "outcome": outcome,
                "summary": summary,
                "confirmed_fields": {
                    "party_size": 2 if name == "book" else None,
                    "time_text": "Friday 7pm" if name == "book" else None,
                    "name_on_record": "Nathan" if name == "book" else None,
                    "answer_text": None if name == "book" else summary,
                },
            }
            self._submit(
                self._function_call(
                    "call_record_2", "record_task_result", arguments
                )
            )
            self._resume_booking = lambda: self._after_record(
                name, outcome, summary, facts
            )
            return
        self._emit_output(f"I'll wrap this up. {summary}")
        if self._scenario.far_party_hangs_up_after_record:
            self._emit_audio(self._scenario.audio_chunks_after_closing_line)
            self._far_party_hangup()
            return
        if self._scenario.skips_end_call:
            self._emit_closing()
            self._emit_audio(self._scenario.audio_chunks_after_closing_line)
            return
        reason = "declined" if outcome == "declined" else "task_done"
        self._submit(
            self._function_call(
                "call_end_1",
                "end_call",
                {"reason": reason},
            )
        )
        self._resume_booking = lambda: self._after_end_call()

    def _after_end_call(self) -> None:
        self._submit(
            self._parse(
                {
                    "type": "response.event",
                    "event_id": "event_response_completed",
                    "delegation_id": "stub-delegation-0001",
                    "event": {
                        "type": "response.completed",
                        "response": {"id": "stub-response-0001", "output": []},
                    },
                }
            )
        )
        self._emit_closing()
        self._emit_audio(self._scenario.audio_chunks_after_closing_line)

    def _function_call(
        self, call_id: str, name: str, arguments: dict[str, Any]
    ) -> CallEvent:
        return self._parse(
            {
                "type": "response.event",
                "event_id": f"event_{call_id}",
                "delegation_id": "stub-delegation-0001",
                "event": {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": json.dumps(arguments),
                    },
                },
            }
        )

    def _emit_closing(self) -> None:
        self._submit(
            self._parse(
                {
                    "type": "session.output_transcript.delta",
                    "delta": CLOSING_LINE,
                }
            )
        )

    def _emit_output(self, text: str) -> None:
        first_half, second_half = self._halves(text)
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

    def _emit_input(self, text: str) -> None:
        first_half, second_half = self._halves(text)
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

    def _emit_audio(self, chunks: int) -> None:
        audio = base64.b64encode(bytes(640)).decode()
        for _ in range(chunks):
            self._submit(
                self._parse(
                    {
                        "type": "session.output_audio.delta",
                        "delta": audio,
                    }
                )
            )

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
