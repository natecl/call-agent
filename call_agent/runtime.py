from __future__ import annotations

import asyncio
import base64
import json
import shutil
import time
import uuid
import wave
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from .config import Settings
from .domain import (
    Call,
    CallControlId,
    CallEvent,
    ClientState,
    CloseLiveEffect,
    Codec,
    ContinueResponseEffect,
    DialEffect,
    DialPlacedEvent,
    DialSpec,
    E164,
    Effect,
    FinishEffect,
    ForwardAudioEffect,
    HangupEffect,
    LiveAudioFormat,
    MarkEffect,
    MediaChunkEvent,
    MediaPlayFailedEvent,
    MediaStartEvent,
    MediaStreaming,
    MediaStopEvent,
    Mode,
    OpenLiveEffect,
    PlayAudioEffect,
    BookRequest,
    CallResult,
    QuickAskRequest,
    ReduceSettings,
    ReplyDelegationEffect,
    RunId,
    RuntimeFailureEvent,
    SampleRate,
    ScheduleEffect,
    SpeakFirstEffect,
    SteerLiveEffect,
    StreamId,
    TimeoutEvent,
    ToolResultEffect,
    Transcript,
    WriteTaskEffect,
    exception_reason,
    normalize_responses_effort,
    reduce,
)
from .gpt_live import (
    GptLive,
    LiveGptLive,
    StubGptLive,
    StubScenario,
    serialize_session_start,
)
from .artifacts import (
    ArtifactStore,
    artifact_slug,
    open_artifact_store,
    publish_durable_artifacts,
    wipe_run_dir,
)
from .report import JournalRow, Refs, build_call_report, redact_call_report
from .telnyx import (
    LiveTelnyxHttp,
    StubTelnyxHttp,
    StubTelnyxMedia,
    TelnyxHttp,
    TelnyxMedia,
    serialize_dial,
)

TRACKS = ("dial", "telnyx", "media", "live", "conversation", "hangup")


def _journal_payload(model: BaseModel) -> dict[str, Any]:
    payload = model.model_dump(mode="json")
    for key in ("payload_b64", "delta_b64"):
        if key in payload:
            payload[key.replace("_b64", "_bytes")] = len(
                base64.b64decode(payload.pop(key))
            )
    return payload


class RunDir:
    def __init__(
        self,
        root: Path,
        call_control_id: str,
        codec: Codec = Codec.L16,
    ) -> None:
        self.root = root
        self.call_control_id = call_control_id
        self.artifact_prefix = artifact_slug(call_control_id)
        self.path = root / self.artifact_prefix
        self.started_at = datetime.now(UTC)
        self.rows: list[JournalRow] = []
        self._clock_start = time.monotonic()
        self._codec = codec
        self._sample_rate: SampleRate = LiveAudioFormat.for_codec(codec).rate
        self._inbound = bytearray()
        self._outbound = bytearray()
        self.path.mkdir(parents=True, exist_ok=True)
        self._events = (self.path / "events.jsonl").open("a")
        self._last_transcript: Transcript | None = None

    def bind(self, call_control_id: CallControlId) -> None:
        self._close_events()
        target = self.root / artifact_slug(str(call_control_id))
        if target != self.path:
            if target.exists():
                shutil.rmtree(target)
            self.path.rename(target)
            self.path = target
        self.call_control_id = str(call_control_id)
        self.artifact_prefix = artifact_slug(self.call_control_id)
        self._events = (self.path / "events.jsonl").open("a")

    def write_json(self, name: str, payload: dict[str, Any]) -> None:
        (self.path / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n"
        )

    def journal_event(
        self,
        sequence: int,
        event: CallEvent,
        transitions: dict[str, str],
    ) -> None:
        if isinstance(event, MediaStartEvent):
            self._codec = event.codec
            self._sample_rate = event.sample_rate
            self._write_audio()
        if isinstance(event, MediaChunkEvent):
            self.append_inbound(event.payload_b64)
        row = _journal_payload(event)
        if transitions:
            row["transitions"] = transitions
        self._append_row(sequence, row)

    def journal_effect(self, sequence: int, effect: Effect) -> None:
        row = _journal_payload(effect)
        row["effect"] = row.pop("kind")
        self._append_row(sequence, row)

    def _append_row(self, sequence: int, payload: dict[str, Any]) -> None:
        row = {
            "seq": sequence,
            "t_ms": round((time.monotonic() - self._clock_start) * 1000),
            **payload,
        }
        self.rows.append(row)
        self._events.write(json.dumps(row, sort_keys=True) + "\n")
        self._events.flush()

    def _close_events(self) -> None:
        events = getattr(self, "_events", None)
        if events is not None and not events.closed:
            events.close()
        self._events = None

    def write_transcript(self, transcript: Transcript) -> None:
        if self._last_transcript == transcript:
            return
        self._last_transcript = transcript
        self.write_json(
            "transcript.json",
            {
                "turns": [
                    turn.model_dump(mode="json")
                    for turn in transcript.turns
                ]
            },
        )

    def append_inbound(self, payload_b64: str) -> None:
        self._inbound.extend(base64.b64decode(payload_b64))

    def append_outbound(self, payload_b64: str) -> None:
        self._outbound.extend(base64.b64decode(payload_b64))

    def finish(
        self,
        result: CallResult,
        call: Call,
        settings: ReduceSettings,
    ) -> CallResult:
        self.write_transcript(call.conversation.transcript)
        self._write_audio()
        self._close_events()
        prefix = self.artifact_prefix
        refs = Refs(
            result=f"{prefix}/result.json",
            transcript=f"{prefix}/transcript.json",
            events=f"{prefix}/events.jsonl",
            inbound_audio=(
                f"{prefix}/audio/{self._audio_name('inbound')}"
            ),
            outbound_audio=(
                f"{prefix}/audio/{self._audio_name('outbound')}"
            ),
        )
        final_result = result.model_copy(
            update={
                "transcript_ref": refs.transcript,
                "audio_ref": refs.inbound_audio,
            }
        )
        self.write_json(
            "result.json", final_result.model_dump(mode="json")
        )
        report = build_call_report(
            call,
            final_result,
            self.rows,
            self.started_at,
            datetime.now(UTC),
            settings,
            refs,
        )
        self.write_json("call_report.json", report.model_dump(mode="json"))
        self.write_json(
            "call_report.redacted.json",
            redact_call_report(
                report,
                caller_first_name=call.request.caller,
            ).model_dump(mode="json"),
        )
        return final_result

    def _write_audio(self) -> None:
        audio_dir = self.path / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        if self._codec is Codec.PCMU:
            (audio_dir / "inbound.pcmu").write_bytes(self._inbound)
            (audio_dir / "outbound.pcmu").write_bytes(self._outbound)
            return
        self._write_wav(audio_dir / "inbound.wav", self._inbound)
        self._write_wav(audio_dir / "outbound.wav", self._outbound)

    def _write_wav(self, path: Path, audio: bytearray) -> None:
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(self._sample_rate)
            output.writeframes(audio)

    def audio_names(self) -> tuple[str, str]:
        return (self._audio_name("inbound"), self._audio_name("outbound"))

    def _audio_name(self, direction: str) -> str:
        extension = "wav" if self._codec is Codec.L16 else "pcmu"
        return f"{direction}.{extension}"


class _UnboundTelnyxMedia:
    async def play(self, payload_b64: str) -> None:
        raise RuntimeError("Telnyx media WebSocket is not bound")

    async def mark(self, name: str) -> None:
        raise RuntimeError("Telnyx media WebSocket is not bound")

    async def close(self) -> None:
        return None


@dataclass(slots=True)
class Ports:
    """Network adapters for one call. Mid-call user chat is not a port yet."""

    telnyx_http: TelnyxHttp
    telnyx_media: TelnyxMedia
    gpt_live: GptLive


def build_ports(
    mode: Mode,
    settings: Settings,
    submit: Any,
    run_dir: RunDir,
    scenario: StubScenario | None = None,
) -> Ports:
    if mode is Mode.STUB:
        faults = scenario or StubScenario()
        media = StubTelnyxMedia(
            submit=submit,
            sample_rate=faults.media_sample_rate,
            echo_marks=not faults.playback_never_drains,
            drop_after_plays=(
                1 if faults.media_drops_before_hangup else None
            ),
        )
        http = StubTelnyxHttp(
            submit=submit,
            media=media,
            write_json=run_dir.write_json,
            dial_override=faults.dial_override,
            far_party_hangs_up=faults.far_party_hangs_up,
            placed_call_control_id=faults.placed_call_control_id,
        )
        return Ports(
            telnyx_http=http,
            telnyx_media=media,
            gpt_live=StubGptLive(
                submit=submit,
                write_json=run_dir.write_json,
                scenario=scenario,
                far_party_hangup=http.far_party_hangup,
            ),
        )
    return Ports(
        telnyx_http=LiveTelnyxHttp(settings.telnyx_api_key or ""),
        telnyx_media=_UnboundTelnyxMedia(),
        gpt_live=LiveGptLive(
            endpoint="wss://api.openai.com/v1/live/sessions",
            api_key=settings.openai_api_key or "",
            extra_headers=settings.openai_live_extra_headers,
            submit=submit,
            write_json=run_dir.write_json,
        ),
    )


class CallHandle:
    def __init__(
        self,
        call: Call,
        queue: asyncio.Queue[CallEvent],
        ports: Ports,
        run_dir: RunDir,
        settings: Settings,
        dial_spec: DialSpec,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self.call = call
        self.queue = queue
        self.ports = ports
        self.run_dir = run_dir
        self.settings = settings
        self.dial_spec = dial_spec
        self.artifact_store = artifact_store
        self._sequence = 0
        self._result: CallResult | None = None
        self._reduce_settings = ReduceSettings(
            live_model=settings.openai_live_model,
            voice=settings.openai_live_voice,
            responses_model=settings.openai_responses_model or "gpt-5.6-terra",
            responses_effort=normalize_responses_effort(
                settings.openai_responses_effort
            ),
            hangup_trailing_silence_s=settings.hangup_trailing_silence_s,
            hangup_drain_timeout_s=settings.hangup_drain_timeout_s,
            live_silence_failsafe_s=settings.live_silence_failsafe_s,
            farewell_idle_s=settings.farewell_idle_s,
            farewell_max_s=settings.farewell_max_s,
            hangup_confirm_timeout_s=settings.hangup_confirm_timeout_s,
        )

    def submit(self, event: CallEvent) -> None:
        self.queue.put_nowait(event)

    def bind_media(self, media: TelnyxMedia) -> None:
        self.ports.telnyx_media = media

    async def run(self) -> CallResult:
        await self._perform(DialEffect(spec=self.dial_spec))
        while self._result is None:
            try:
                event = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=self.settings.event_timeout_s,
                )
            except TimeoutError:
                self.submit(
                    TimeoutEvent(
                        webhook_host=public_url_host(
                            self.dial_spec.webhook_url
                        ),
                        stream_host=public_url_host(
                            self.dial_spec.stream_url
                        ),
                    )
                )
                continue
            try:
                await self._process_event(event)
            except Exception as error:
                if isinstance(event, RuntimeFailureEvent):
                    raise
                await self._process_event(
                    RuntimeFailureEvent(reason=exception_reason(error))
                )
        return self._result

    async def _process_event(self, event: CallEvent) -> None:
        before = self.call
        self.call, effects = reduce(
            before,
            event,
            self._reduce_settings,
        )
        if isinstance(event, DialPlacedEvent):
            self.run_dir.bind(event.call_control_id)
            BOOK.bind(event.call_control_id, self)
        transitions = {
            track: getattr(self.call, track).kind
            for track in TRACKS
            if getattr(self.call, track).kind != getattr(before, track).kind
        }
        self._sequence += 1
        self.run_dir.journal_event(self._sequence, event, transitions)
        self.run_dir.write_transcript(self.call.conversation.transcript)
        for effect in effects:
            await self._perform(effect)

    async def _perform(self, effect: Effect) -> None:
        self._sequence += 1
        self.run_dir.journal_effect(self._sequence, effect)
        if isinstance(effect, DialEffect):
            self.run_dir.write_json(
                "dial.json", serialize_dial(effect.spec)
            )
            placed = self.ports.telnyx_http.dial(effect.spec)
            await self._process_event(placed)
            return
        if isinstance(effect, OpenLiveEffect):
            self.run_dir.write_json(
                "session_start.json",
                serialize_session_start(effect.spec),
            )
            await self.ports.gpt_live.start(effect.spec)
            return
        if isinstance(effect, SpeakFirstEffect):
            await self.ports.gpt_live.speak_first(effect.line)
            return
        if isinstance(effect, ReplyDelegationEffect):
            await self.ports.gpt_live.reply_delegation(
                effect.delegation_id, effect.thinking, effect.commentary
            )
            return
        if isinstance(effect, SteerLiveEffect):
            await self.ports.gpt_live.steer(
                effect.instructions, effect.event_id
            )
            return
        if isinstance(effect, ToolResultEffect):
            await self.ports.gpt_live.send_tool_result(
                effect.call_id, effect.output, effect.event_id
            )
            return
        if isinstance(effect, ContinueResponseEffect):
            await self.ports.gpt_live.continue_response(effect.event_id)
            return
        if isinstance(effect, WriteTaskEffect):
            self.run_dir.write_json("task.json", effect.payload)
            return
        if isinstance(effect, ForwardAudioEffect):
            await self.ports.gpt_live.append_audio(effect.payload_b64)
            return
        if isinstance(effect, PlayAudioEffect):
            self.run_dir.append_outbound(effect.delta_b64)
            try:
                await self.ports.telnyx_media.play(effect.delta_b64)
            except (WebSocketDisconnect, RuntimeError) as error:
                await self._media_play_failed(error)
            return
        if isinstance(effect, MarkEffect):
            try:
                await self.ports.telnyx_media.mark(effect.name)
            except (WebSocketDisconnect, RuntimeError) as error:
                await self._media_play_failed(error)
            return
        if isinstance(effect, ScheduleEffect):
            asyncio.get_running_loop().call_later(
                effect.delay_s, self.submit, effect.event
            )
            return
        if isinstance(effect, HangupEffect):
            self.run_dir.write_json(
                "hangup.json",
                {"call_control_id": str(effect.call_control_id)},
            )
            self.ports.telnyx_http.hangup(effect.call_control_id)
            return
        if isinstance(effect, CloseLiveEffect):
            await self.ports.gpt_live.close()
            return
        if isinstance(effect, FinishEffect):
            self._result = self.run_dir.finish(
                effect.result,
                self.call,
                self._reduce_settings,
            )

    async def _media_play_failed(self, error: BaseException) -> None:
        stream_id = (
            self.call.media.stream_id
            if isinstance(self.call.media, MediaStreaming)
            else StreamId("unknown")
        )
        await self._process_event(
            MediaPlayFailedEvent(
                stream_id=stream_id,
                error_type=type(error).__name__,
            )
        )


class CallBook:
    def __init__(self) -> None:
        self._by_control: dict[CallControlId, CallHandle] = {}
        self._by_run: dict[RunId, CallHandle] = {}

    def register(self, run_id: RunId, handle: CallHandle) -> None:
        self._by_run[run_id] = handle

    def bind(
        self, call_control_id: CallControlId, handle: CallHandle
    ) -> None:
        self._by_control[call_control_id] = handle

    def by_call_control_id(
        self, call_control_id: CallControlId
    ) -> CallHandle:
        return self._by_control[call_control_id]

    def by_run_id(self, run_id: RunId) -> CallHandle:
        return self._by_run[run_id]

    def drop(self, call_control_id: CallControlId) -> None:
        handle = self._by_control.pop(call_control_id)
        self._drop_handle(handle)

    def _drop_handle(self, handle: CallHandle) -> None:
        self._by_control = {
            key: value
            for key, value in self._by_control.items()
            if value is not handle
        }
        self._by_run = {
            key: value
            for key, value in self._by_run.items()
            if value is not handle
        }


BOOK = CallBook()


def should_retain_call_artifacts(
    settings: Settings,
    request: QuickAskRequest | BookRequest,
) -> bool:
    if request.retain_call_artifacts is not None:
        return request.retain_call_artifacts
    return settings.retain_call_artifacts


def public_url_host(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return parsed.netloc or url
    if parsed.port:
        return f"{host}:{parsed.port}"
    return host


def normalized_public_host(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if not host:
        return (parsed.netloc or url).casefold()
    scheme = (parsed.scheme or "").casefold()
    port = parsed.port
    if port is None:
        return host
    if scheme in {"https", "wss"} and port == 443:
        return host
    if scheme in {"http", "ws"} and port == 80:
        return host
    return f"{host}:{port}"


def webhook_and_stream_urls(settings: Settings) -> tuple[str, str]:
    media_base = settings.public_base_url.rstrip("/")
    webhook_base = (
        settings.public_webhook_url or settings.public_base_url
    ).rstrip("/")
    return (
        f"{webhook_base}/webhooks/telnyx",
        f"{media_base.replace('https://', 'wss://', 1)}/media-stream",
    )


def shared_telnyx_from(settings: Settings) -> E164 | None:
    """Sitewide Telnyx FROM. One number for every Treena user.

    Driven by `TELNYX_FROM_NUMBER` or `TELNYX_PHONE_NUMBER`. Not a
    unique DID per user and not the Linq iMessage bot.
    """
    raw = (settings.telnyx_from_number or "").strip()
    if not raw:
        return None
    return E164(raw)


def resolve_telnyx_from(settings: Settings, *, live: bool | None = None) -> E164:
    from_did = shared_telnyx_from(settings)
    if from_did is not None:
        return from_did
    is_live = settings.quick_ask_live if live is None else live
    if is_live:
        raise TelnyxFromConfigError(
            "live dial requires TELNYX_FROM_NUMBER or TELNYX_PHONE_NUMBER"
        )
    return E164("+15555550100")


class TelnyxFromConfigError(ValueError):
    pass


def validate_live(settings: Settings) -> None:
    missing: list[str] = []
    if not settings.quick_ask_live:
        missing.append("QUICK_ASK_LIVE")
    if not settings.telnyx_api_key:
        missing.append("TELNYX_API_KEY")
    if not settings.telnyx_connection_id:
        missing.append("TELNYX_CONNECTION_ID")
    if not (settings.telnyx_from_number or "").strip():
        missing.append("TELNYX_FROM_NUMBER")
    if not settings.openai_api_key:
        missing.append("OPENAI_API_KEY")
    if not settings.openai_responses_model:
        missing.append("OPENAI_RESPONSES_MODEL")
    if (
        not settings.public_base_url
        or settings.public_base_url == "https://stub.invalid"
    ):
        missing.append("PUBLIC_BASE_URL")
    if missing:
        raise ValueError(
            f"live mode requires: {', '.join(missing)}"
        )


def assert_live_dial_origins(settings: Settings) -> None:
    if not (settings.public_webhook_url or "").strip():
        raise ValueError(
            "live dial requires PUBLIC_WEBHOOK_URL set to the same host "
            "as PUBLIC_BASE_URL. Process env overrides .env, so a leftover "
            "PUBLIC_WEBHOOK_URL can send webhooks to a stale host while "
            "media follows PUBLIC_BASE_URL."
        )
    webhook_url, stream_url = webhook_and_stream_urls(settings)
    webhook_host = normalized_public_host(webhook_url)
    stream_host = normalized_public_host(stream_url)
    if webhook_host != stream_host:
        raise ValueError(
            "live dial webhook host "
            f"{webhook_host} != stream host {stream_host}. "
            "Set PUBLIC_BASE_URL and PUBLIC_WEBHOOK_URL to the same "
            "origin. Process env overrides .env, so a stale "
            "PUBLIC_WEBHOOK_URL can diverge from PUBLIC_BASE_URL "
            "(Fly media + old tunnel webhook)."
        )


async def run_call(
    request: QuickAskRequest | BookRequest,
    settings: Settings,
    *,
    from_did: E164,
    ports: Ports | None = None,
    scenario: StubScenario | None = None,
) -> CallResult:
    if request.mode is Mode.LIVE:
        validate_live(settings)
        assert_live_dial_origins(settings)
    settings.ledger_config()
    artifact_store = open_artifact_store(settings)
    run_id = RunId(str(uuid.uuid4()))
    codec = Codec(settings.telnyx_stream_codec)
    run_dir = RunDir(
        settings.call_agent_artifacts_dir,
        str(run_id),
        codec,
    )
    queue: asyncio.Queue[CallEvent] = asyncio.Queue()
    active_ports = ports or build_ports(
        request.mode,
        settings,
        queue.put_nowait,
        run_dir,
        scenario,
    )
    connection_id = (
        settings.telnyx_connection_id or "stub-connection"
    )
    webhook_url, stream_url = webhook_and_stream_urls(settings)
    dial_spec = DialSpec(
        connection_id=connection_id,
        to=request.to,
        from_did=from_did,
        stream_url=stream_url,
        webhook_url=webhook_url,
        codec=codec,
        client_state=ClientState(
            run_id=run_id,
            user_id=request.user_id,
        ),
    )
    call = Call(
        run_id=run_id,
        request=request,
        from_did=from_did,
    )
    handle = CallHandle(
        call=call,
        queue=queue,
        ports=active_ports,
        run_dir=run_dir,
        settings=settings,
        dial_spec=dial_spec,
        artifact_store=artifact_store,
    )
    BOOK.register(run_id, handle)
    try:
        result = await handle.run()
        try:
            if should_retain_call_artifacts(settings, request):
                if handle.artifact_store is not None:
                    publish_durable_artifacts(
                        handle.run_dir.root,
                        handle.run_dir.call_control_id,
                        handle.run_dir.audio_names(),
                        handle.artifact_store,
                    )
            else:
                wipe_run_dir(handle.run_dir.path)
        except Exception as error:
            try:
                handle.run_dir.write_json(
                    "publish_error.json",
                    {"error": exception_reason(error)},
                )
            except OSError:
                pass
        return result
    finally:
        BOOK._drop_handle(handle)


run_quick_ask = run_call
