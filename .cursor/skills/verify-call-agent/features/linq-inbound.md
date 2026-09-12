# Linq inbound (closed alpha)

Closed alpha includes iMessage. L1 testers text Treena through Linq. The public Worker `POST /linq/webhook` is the inbound ingress. It forwards the raw body and Standard Webhooks headers to the FastAPI process. Worker `APP_BASE_URL` is the always-on Fly origin `https://<app>.fly.dev` (no path). The laptop is not the prod FastAPI host. The hook does not send SMS. Telnyx always-on stays on `POST /webhooks/telnyx`.

## Sub-features

- `linq-register` Builder registers `https://call-agent-webhooks.blossom-linen.workers.dev/linq/webhook`.
- `linq-post` `POST /linq/webhook` and `POST /webhooks/linq` forward to `APP_BASE_URL/linq/webhook`. Unset `APP_BASE_URL` is 501 `missing_app_base_url`.
- `fly-host` Worker `APP_BASE_URL` is the always-on Fly origin `https://<app>.fly.dev` (no path). The laptop is not the prod FastAPI host.
- `linq-ping` `GET` on those paths returns `{"ok":true,"provider":"linq"}`.
- `no-sms` The hook does not send or receive SMS.

## How to get to it (user POV)

- Register `https://call-agent-webhooks.blossom-linen.workers.dev/linq/webhook` on the Linq subscription.
- Ping with `GET` and expect `{"ok":true,"provider":"linq"}`.
- Keep Telnyx on `https://call-agent-webhooks.blossom-linen.workers.dev/webhooks/telnyx`.
- Set Worker secret `APP_BASE_URL` to the always-on Fly origin `https://<app>.fly.dev` (no path), then `npx wrangler deploy`. The laptop is not the prod host.
- Prove the hook with `call-agent verify-webhooks`.

## Driving it with call-agent

Preconditions:

- Node 22 is on `PATH`. Stub mode needs no Cloudflare account and no Linq key.

- **Stub hook.** Run `.venv/bin/call-agent verify-webhooks`. Exit code `0`. The `linq_hook` line is `[PASS]`. Stub POST is 501 `missing_app_base_url`. Stub GET ping is 200 `provider:linq`.
- **Live register URL.** The string `https://call-agent-webhooks.blossom-linen.workers.dev/linq/webhook` is in `README.md`, `workers/webhooks/README.md`, and `docs/FEATURE_MAP.md`.
- **Proof.** Save `verify-webhooks` stdout as `verify-webhooks.txt`.

## Gotchas

- SMS is off. A 501 `missing_app_base_url` means the Worker has no FastAPI origin. FastAPI `POST /linq/webhook` still verifies HMAC after the forward.
- Prod FastAPI is the always-on Fly host `https://<app>.fly.dev` (no path) with `LINQ_WEBHOOK_SIGNING_SECRET`. Nathan's laptop is not that host.
- There is no `call-agent-web` map in this repository. The Worker name is `call-agent-webhooks`.
- Pointing `PUBLIC_BASE_URL` at the Worker sends Telnyx media to `/media-stream` and gets 501.
- A wrangler preview edge may challenge some datacenter POSTs. The register URL stays the Worker hostname.
