from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import NamedTuple

import httpx
import uvicorn
import websockets
from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse

from .config import Settings
from .domain import MediaStartEvent, MediaStopEvent
from .kickoff import (
    KickoffMalformed,
    KickoffParsed,
    claim_kickoff,
    kickoff_host_mismatch,
    kickoff_json_path,
    make_session,
    malformed_reply_text,
    parse_kickoff,
    run_kickoff,
    write_call_cross_links,
)
from .linq import (
    LinqConfigError,
    LinqWebhookAuthError,
    handle_inbound,
    messaging_port,
    reply_from_did,
    verify_linq_signature,
    write_inbound_artifact,
)
from .messaging import inbound_plain_text
from .runtime import BOOK
from .telnyx import LiveTelnyxMedia, parse_media_frame, parse_webhook


app = FastAPI()

SERVER_START_TIMEOUT_S = 5.0
_serve_token: str | None = None


def current_serve_token() -> str | None:
    return _serve_token


def set_serve_token(token: str | None) -> None:
    global _serve_token
    _serve_token = token


@app.get("/health")
async def health() -> dict[str, bool | str]:
    payload: dict[str, bool | str] = {"ok": True}
    if _serve_token is not None:
        payload["serve_token"] = _serve_token
    return payload


@app.post("/webhooks/telnyx")
async def telnyx_webhook(request: Request) -> dict[str, bool]:
    event = parse_webhook(await request.body())
    try:
        handle = BOOK.by_call_control_id(event.call_control_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="unknown call") from error
    handle.submit(event)
    return {"ok": True}


async def process_linq_webhook(
    request: Request, background_tasks: BackgroundTasks
) -> dict[str, bool | str] | JSONResponse:
    body = await request.body()
    settings = Settings()
    if not settings.linq_webhook_signing_secret:
        raise HTTPException(status_code=401, detail="unauthorized")
    try:
        verify_linq_signature(
            settings.linq_webhook_signing_secret,
            body,
            request.headers,
        )
    except LinqWebhookAuthError as error:
        raise HTTPException(status_code=401, detail="unauthorized") from error
    port = messaging_port(settings)
    inbound = port.receive(body)
    if inbound is None:
        return {"ok": True, "ignored": True}
    try:
        from_did = reply_from_did(settings)
    except LinqConfigError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    if str(inbound.sender) == str(from_did):
        return {"ok": True, "ignored": True}
    parsed = parse_kickoff(inbound_plain_text(inbound), settings)
    if parsed is None:
        try:
            receipt = handle_inbound(inbound, settings, port)
        except (LinqConfigError, OSError) as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        if receipt is None:
            return {"ok": True, "ignored": True}
        return {
            "ok": True,
            "replied": True,
            "chat_id": str(receipt.chat_id),
            "message_id": str(receipt.message_id),
        }
    try:
        write_inbound_artifact(
            settings.call_agent_artifacts_dir,
            inbound,
            received_at=datetime.now(UTC),
        )
    except OSError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    session = make_session(inbound, settings, port)
    if isinstance(parsed, KickoffMalformed):
        try:
            receipt = session.text(
                malformed_reply_text(parsed), kind="error"
            )
        except (LinqConfigError, OSError) as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return {
            "ok": True,
            "replied": True,
            "parse_error": True,
            "chat_id": str(receipt.chat_id),
            "message_id": str(receipt.message_id),
        }
    assert isinstance(parsed, KickoffParsed)
    if kickoff_json_path(
        settings.call_agent_artifacts_dir, str(inbound.event_id)
    ).exists():
        return {"ok": True, "duplicate": True}
    mismatch = kickoff_host_mismatch(
        settings, request.headers.get("host")
    )
    if mismatch is not None:
        claim_kickoff(settings, inbound, parsed.request)
        try:
            receipt = session.text(mismatch, kind="error")
        except (LinqConfigError, OSError) as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        write_call_cross_links(
            settings,
            inbound,
            None,
            receipt,
            mismatch,
            error=mismatch,
        )
        return {
            "ok": True,
            "placed": False,
            "reason": mismatch,
            "chat_id": str(receipt.chat_id),
            "message_id": str(receipt.message_id),
        }
    if not claim_kickoff(settings, inbound, parsed.request):
        return {"ok": True, "duplicate": True}
    background_tasks.add_task(run_kickoff, session, parsed.request)
    return JSONResponse(
        status_code=202,
        content={
            "ok": True,
            "kicked_off": True,
            "event_id": str(inbound.event_id),
        },
    )


app.add_api_route(
    "/webhooks/linq",
    process_linq_webhook,
    methods=["POST"],
    response_model=None,
)
app.add_api_route(
    "/linq/webhook",
    process_linq_webhook,
    methods=["POST"],
    response_model=None,
)


@app.websocket("/media-stream")
async def media_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    handle = None
    try:
        while True:
            event = parse_media_frame(await websocket.receive_text())
            if event is None:
                continue
            if isinstance(event, MediaStartEvent):
                try:
                    handle = BOOK.by_call_control_id(
                        event.call_control_id
                    )
                except KeyError:
                    await websocket.close(code=4404)
                    return
                handle.bind_media(LiveTelnyxMedia(websocket))
            if handle is None:
                await websocket.close(code=4400)
                return
            handle.submit(event)
            if isinstance(event, MediaStopEvent):
                return
    except WebSocketDisconnect:
        return


class RunningServer(NamedTuple):
    server: uvicorn.Server
    task: asyncio.Task[None]
    serve_token: str

    async def stop(self) -> None:
        self.server.should_exit = True
        await self.server.shutdown()
        await self.task
        if current_serve_token() == self.serve_token:
            set_serve_token(None)


class LiveIngressError(Exception):
    """One failed attempt to reach this process via PUBLIC_BASE_URL."""


def health_url(public_base_url: str) -> str:
    return public_base_url.rstrip("/") + "/health"


def media_stream_url(public_base_url: str) -> str:
    base = public_base_url.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base.removeprefix("https://") + "/media-stream"
    if base.startswith("http://"):
        return "ws://" + base.removeprefix("http://") + "/media-stream"
    raise ValueError(
        "PUBLIC_BASE_URL must start with http:// or https://"
    )


async def probe_live_ingress(
    public_base_url: str,
    expected_token: str,
    timeout_s: float,
    *,
    client: httpx.AsyncClient | None = None,
    probe_websocket: bool = True,
) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = "no attempt"
    owns_client = client is None
    active_client = client or httpx.AsyncClient()
    try:
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                await _probe_health_once(
                    active_client,
                    public_base_url,
                    expected_token,
                    min(2.0, remaining),
                )
                if probe_websocket:
                    await _probe_media_stream_once(
                        media_stream_url(public_base_url),
                        min(2.0, remaining),
                    )
                return
            except LiveIngressError as error:
                last_error = str(error)
            await asyncio.sleep(min(0.15, max(0.0, deadline - time.monotonic())))
    finally:
        if owns_client:
            await active_client.aclose()
    raise ValueError(
        f"{health_url(public_base_url)} did not reach this process "
        f"within {timeout_s}s ({last_error}). CLI --live hosts CallHandle "
        "in this process; set PUBLIC_BASE_URL to a tunnel (ngrok, "
        "cloudflared) to CALL_AGENT_PORT. A printed trycloudflare URL is "
        "not enough: DNS can lag after the URL is advertised. A Fly "
        "origin is a different process with no BOOK entry."
    )


async def _probe_health_once(
    client: httpx.AsyncClient,
    public_base_url: str,
    expected_token: str,
    timeout_s: float,
) -> None:
    url = health_url(public_base_url)
    try:
        response = await client.get(url, timeout=timeout_s)
    except httpx.HTTPError as error:
        raise LiveIngressError(f"{url} unreachable: {error}") from error
    try:
        payload = response.json()
    except ValueError as error:
        raise LiveIngressError(
            f"{url} returned HTTP {response.status_code} non-JSON"
        ) from error
    if response.status_code != 200 or payload.get("ok") is not True:
        raise LiveIngressError(
            f"{url} returned HTTP {response.status_code} {payload!r}"
        )
    token = payload.get("serve_token")
    if token != expected_token:
        raise LiveIngressError(
            f"{url} is reachable but serve_token does not match this "
            "process. CLI --live cannot use a Fly PUBLIC_BASE_URL; "
            "Telnyx would send media/webhooks to that host, which has "
            "no BOOK entry."
        )


async def _probe_media_stream_once(stream_url: str, timeout_s: float) -> None:
    try:
        async with websockets.connect(
            stream_url,
            open_timeout=timeout_s,
            close_timeout=1,
        ) as connection:
            await connection.close()
    except Exception as error:
        raise LiveIngressError(
            f"{stream_url} WebSocket did not accept: {error}"
        ) from error


async def _wait_until_accepting(
    server: uvicorn.Server,
    task: asyncio.Task[None],
    port: int,
    timeout_s: float,
) -> None:
    deadline = time.monotonic() + timeout_s
    last_error = "server has not started"
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            if task.done():
                exception = task.exception()
                if exception is not None:
                    raise ValueError(
                        "in-process FastAPI failed to start on port "
                        f"{port}: {exception}"
                    ) from exception
                raise ValueError(
                    "in-process FastAPI exited before accepting on port "
                    f"{port}"
                )
            if server.started:
                try:
                    response = await client.get(
                        f"http://127.0.0.1:{port}/health",
                        timeout=0.3,
                    )
                    payload = response.json()
                    if (
                        response.status_code == 200
                        and payload.get("ok") is True
                    ):
                        return
                    last_error = (
                        f"HTTP {response.status_code} {payload!r}"
                    )
                except (httpx.HTTPError, ValueError) as error:
                    last_error = str(error)
            await asyncio.sleep(0.02)
    raise ValueError(
        f"in-process FastAPI did not accept /health on port {port} "
        f"within {timeout_s}s ({last_error})"
    )


async def serve_in_process(settings: Settings) -> RunningServer:
    token = uuid.uuid4().hex
    set_serve_token(token)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="0.0.0.0",
            port=settings.port,
            log_level="warning",
        )
    )
    task = asyncio.create_task(server.serve())
    running = RunningServer(server, task, token)
    try:
        await _wait_until_accepting(
            server,
            task,
            settings.port,
            SERVER_START_TIMEOUT_S,
        )
    except BaseException:
        await running.stop()
        raise
    return running
