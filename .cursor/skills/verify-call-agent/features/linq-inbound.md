# Linq inbound (closed alpha)

Closed alpha includes iMessage. L1 testers text Treena through Linq. The public Worker `POST /linq/webhook` is the inbound ingress. It acknowledges and does not send SMS. Telnyx always-on stays on `POST /webhooks/telnyx`.

## Sub-features

- `linq-register` Builder registers `https://call-agent-webhooks.blossom-linen.workers.dev/linq/webhook`.
- `linq-post` `POST /linq/webhook` and `POST /webhooks/linq` return `implemented: false`.
- `linq-ping` `GET` on those paths returns the same body.
- `no-sms` The hook does not send or receive SMS.

## How to get to it (user POV)

- Register `https://call-agent-webhooks.blossom-linen.workers.dev/linq/webhook` on the Linq subscription.
- Ping with `GET` or `POST` and expect `{"ok":true,"provider":"linq","implemented":false}`.
- Keep Telnyx on `https://call-agent-webhooks.blossom-linen.workers.dev/webhooks/telnyx`.
- Prove the hook with `call-agent verify-webhooks`.

## Driving it with call-agent

Preconditions:

- Node 22 is on `PATH`. Stub mode needs no Cloudflare account and no Linq key.

- **Stub hook.** Run `.venv/bin/call-agent verify-webhooks`. Exit code `0`. The `linq_hook` line is `[PASS]`.
- **Live register URL.** The string `https://call-agent-webhooks.blossom-linen.workers.dev/linq/webhook` is in `README.md`, `workers/webhooks/README.md`, and `docs/FEATURE_MAP.md`.
- **Proof.** Save `verify-webhooks` stdout as `verify-webhooks.txt`.

## Gotchas

- SMS is off. A 200 with `implemented: false` is the Worker ACK, not a message processor. FastAPI `POST /webhooks/linq` is the process path. Worker to app forward is a follow-up.
- There is no `call-agent-web` map in this repository. The Worker name is `call-agent-webhooks`.
- Pointing `PUBLIC_BASE_URL` at the Worker sends Telnyx media to `/media-stream` and gets 501.
- A wrangler preview edge may challenge some datacenter POSTs. The register URL stays the Worker hostname.
