from __future__ import annotations

import asyncio
from typing import NamedTuple

import uvicorn
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)

from .config import Settings
from .domain import MediaStartEvent, MediaStopEvent
from .linq import LinqWebhookAuthError, parse_inbound, verify_linq_signature
from .runtime import BOOK
from .telnyx import LiveTelnyxMedia, parse_media_frame, parse_webhook


app = FastAPI()


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/webhooks/telnyx")
async def telnyx_webhook(request: Request) -> dict[str, bool]:
    event = parse_webhook(await request.body())
    try:
        handle = BOOK.by_call_control_id(event.call_control_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="unknown call") from error
    handle.submit(event)
    return {"ok": True}


async def process_linq_webhook(request: Request) -> dict[str, bool]:
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
    inbound = parse_inbound(body)
    if inbound is None:
        return {"ok": True, "ignored": True}
    return {"ok": True}


app.add_api_route("/webhooks/linq", process_linq_webhook, methods=["POST"])
app.add_api_route("/linq/webhook", process_linq_webhook, methods=["POST"])


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

    async def stop(self) -> None:
        self.server.should_exit = True
        await self.server.shutdown()
        await self.task


async def serve_in_process(settings: Settings) -> RunningServer:
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="0.0.0.0",
            port=settings.port,
            log_level="warning",
        )
    )
    return RunningServer(server, asyncio.create_task(server.serve()))
