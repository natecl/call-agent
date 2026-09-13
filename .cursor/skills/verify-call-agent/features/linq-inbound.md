# Linq inbound (closed alpha)

Closed alpha includes iMessage. L1 testers text Treena through Linq. The public Worker `POST /linq/webhook` is the inbound ingress. It forwards the raw body and Standard Webhooks headers to the FastAPI process. Worker `APP_BASE_URL` is the always-on Fly origin `https://hidden-beacon-080.fly.dev` (no path). The laptop is not the prod FastAPI host. FastAPI verifies HMAC, maps `message.received` into `InboundMessage`, and sends a boring ack back on the chat via MessagingPort. The hook does not send SMS. Telnyx always-on stays on `POST /webhooks/telnyx`.

## Sub-features

- `linq-register` Builder registers `https://call-agent-webhooks.usetreena.workers.dev/linq/webhook`.
- `linq-post` `POST /linq/webhook` and `POST /webhooks/linq` forward to `APP_BASE_URL/linq/webhook`. Unset `APP_BASE_URL` is 501 `missing_app_base_url`.
- `fly-host` Worker `APP_BASE_URL` is the always-on Fly origin `https://hidden-beacon-080.fly.dev` (no path). The laptop is not the prod FastAPI host.
- `inbound-reply` iMessage `message.received` is mapped and answered on `POST /chats/{chat_id}/messages` with the same `message.parts` shape as outbound kickoff. Copy is `Received: {inbound text}` for chat that is not a book/ask collect or kickoff.
- `inbound-artifacts` writes `inbound.json` (inbound text, ids, timing) and `reply.json` under `CALL_AGENT_ARTIFACTS_DIR/linq/<event_id>/`. Missing artifact dir fails loud.
- `linq-ping` `GET` on those paths returns `{"ok":true,"provider":"linq"}`.
- `no-sms` The hook does not send or receive SMS. `message.sent` / `delivered` / `read` / `failed` are ignored.

## How to get to it (user POV)

- Register `https://call-agent-webhooks.usetreena.workers.dev/linq/webhook` on the Linq subscription.
- Ping with `GET` and expect `{"ok":true,"provider":"linq"}`.
- Keep Telnyx on `https://call-agent-webhooks.usetreena.workers.dev/webhooks/telnyx`.
- Set Worker secret `APP_BASE_URL` to the always-on Fly origin `https://hidden-beacon-080.fly.dev` (no path), then `npx wrangler deploy`. The laptop is not the prod host.
- Prove the hook with `call-agent verify-webhooks`.
- Prove inbound → outbound ack with `call-agent verify-message`. The `inbound_reply` line must pass.

## Driving it with call-agent

Preconditions:

- Node 22 is on `PATH`. Stub mode needs no Cloudflare account and no Linq key.

- **Stub hook.** Run `.venv/bin/call-agent verify-webhooks`. Exit code `0`. The `linq_hook` line is `[PASS]`. Stub POST is 501 `missing_app_base_url`. Stub GET ping is 200 `provider:linq`.
- **Stub reply.** Run `.venv/bin/call-agent verify-message`. Exit code `0`. The `inbound_reply` line is `[PASS]` and names the inbound text.
- **Stub kickoff.** Run `.venv/bin/call-agent verify-linq-call`. Exit code `0`. Thirteen `[PASS]` lines and `13/13 passed`.
- **Live register URL.** The string `https://call-agent-webhooks.usetreena.workers.dev/linq/webhook` is in `README.md`, `workers/webhooks/README.md`, and `docs/FEATURE_MAP.md`.
- **Proof.** Save `verify-webhooks` stdout as `verify-webhooks.txt`, `verify-message` stdout as `verify-message.txt`, and `verify-linq-call` stdout as `verify-linq-call.txt`.

## Gotchas

- SMS is off. A 501 `missing_app_base_url` means the Worker has no FastAPI origin. FastAPI `POST /linq/webhook` verifies HMAC, then replies via MessagingPort. Live reply needs `LINQ_API_TOKEN` and `LINQ_FROM_E164` on Fly.
- Prod FastAPI is the always-on Fly host `https://hidden-beacon-080.fly.dev` (no path) with `LINQ_WEBHOOK_SIGNING_SECRET`. Nathan's laptop is not that host.
- There is no `call-agent-web` map in this repository. The Worker name is `call-agent-webhooks`.
- Pointing `PUBLIC_BASE_URL` at the Worker sends Telnyx media to `/media-stream` and gets 501.
- A wrangler preview edge may challenge some datacenter POSTs. The register URL stays the Worker hostname.
- Missing `CALL_AGENT_ARTIFACTS_DIR` fails the inbound webhook. Do not treat `{ok:true}` without `inbound.json` as a processed message.
