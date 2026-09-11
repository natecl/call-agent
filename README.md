# call-agent

Consumer AI phone call agent that bridges **Telnyx Media Streaming** with the
**OpenAI GPT-Live (Realtime) API**. Telnyx handles the phone call and streams the
caller's audio over a WebSocket; the agent relays that audio to GPT-Live and
streams the model's spoken response back to the caller in near real time.

Both sides speak **G.711 μ-law @ 8 kHz**, so audio is forwarded without
transcoding.

## Highlights

- **Telnyx Call Control webhooks** — answers inbound calls and starts
  bidirectional RTP media streaming (`src/telnyx/`).
- **Media-stream bridge** — WebSocket endpoint that pipes caller audio to the
  model and model audio back to the caller (`src/telnyx/mediaStream.ts`).
- **GPT-Live client** — OpenAI Realtime (GA) session over WebSocket using the
  nested `g711_ulaw` audio schema and server VAD (`src/realtime/openai.ts`).
- **Runs without credentials** — when `OPENAI_API_KEY` / `TELNYX_API_KEY` are
  absent, in-process **mock backends** simulate the model and Telnyx so the full
  call flow is runnable and testable (`src/realtime/mock.ts`).
- **Demo console** — a web UI at `/` that places a fully simulated inbound call
  and plays back the agent's audio (`public/`).

## Requirements

- Node.js >= 20.10 (developed on Node 22)

## Getting started

```bash
npm install
npm run dev        # start the server (http://localhost:3000)
```

Open http://localhost:3000 and click **Place simulated call** to run an inbound
call end-to-end with no phone or credentials required.

Run a simulated call from the command line:

```bash
npm run demo
```

## Scripts

| Script              | Purpose                                       |
| ------------------- | --------------------------------------------- |
| `npm run dev`       | Start the server with hot reload (tsx).       |
| `npm run build`     | Bundle to `dist/` with tsup.                  |
| `npm start`         | Run the built server.                         |
| `npm run typecheck` | Type-check with `tsc --noEmit`.               |
| `npm run lint`      | Lint with ESLint.                             |
| `npm run test`      | Run the Vitest suite.                         |
| `npm run demo`      | Run a simulated inbound call in the terminal. |

## Configuration

All configuration is via environment variables (see `.env.example`). Everything
is optional — without credentials the app uses mock backends.

| Variable              | Default            | Description                                               |
| --------------------- | ------------------ | --------------------------------------------------------- |
| `PORT`                | `3000`             | HTTP/WebSocket port.                                      |
| `PUBLIC_HOST`         | —                  | Public host Telnyx streams media to (e.g. an ngrok host). |
| `TELNYX_API_KEY`      | —                  | Enables the live Telnyx Call Control client.              |
| `TELNYX_STREAM_TRACK` | `inbound_track`    | Which track(s) to stream.                                 |
| `OPENAI_API_KEY`      | —                  | Enables the live GPT-Live backend.                        |
| `OPENAI_MODEL`        | `gpt-realtime`     | Realtime model.                                           |
| `OPENAI_VOICE`        | `marin`            | Output voice.                                             |
| `SYSTEM_PROMPT`       | see `.env.example` | Agent persona.                                            |
| `GREETING`            | see `.env.example` | Opening line.                                             |
| `REALTIME_BACKEND`    | `auto`             | `auto` \| `openai` \| `mock`.                             |
| `TELNYX_BACKEND`      | `auto`             | `auto` \| `live` \| `mock`.                               |

## HTTP endpoints

| Method | Path              | Purpose                                                   |
| ------ | ----------------- | --------------------------------------------------------- |
| `GET`  | `/`               | Demo console.                                             |
| `GET`  | `/healthz`        | Health check.                                             |
| `GET`  | `/api/status`     | Backend/config status.                                    |
| `POST` | `/telnyx/webhook` | Telnyx Call Control webhooks.                             |
| `POST` | `/demo/simulate`  | Run a simulated inbound call, returns transcript + audio. |
| `WS`   | `/telnyx/media`   | Telnyx Media Streaming endpoint.                          |

## Going live

1. Set `TELNYX_API_KEY`, `OPENAI_API_KEY`, and `PUBLIC_HOST` (a public wss host).
2. Point a Telnyx Call Control application's webhook at `POST /telnyx/webhook`.
3. Call your Telnyx number — the agent answers and starts streaming automatically.

## Architecture

```
Caller ── PSTN ──> Telnyx ──(webhook: call.initiated/answered)──> /telnyx/webhook
                     │                                              │ answer + streaming_start
                     └──(WebSocket media, μ-law)──> /telnyx/media ──┤
                                                                    ▼
                                                   CallSession (bridge + transcript)
                                                                    │  append audio (μ-law)
                                                                    ▼
                                                   GPT-Live (OpenAI Realtime)  ── audio deltas ──┐
                                                                                                  │
                     ◀───────────────── media frames (μ-law) ◀──────────────────────────────────┘
```
