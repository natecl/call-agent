# Always-on webhooks versus local tunnel

Production Telnyx Call Control posts go to the Cloudflare Worker inbox. Closed alpha iMessage inbound uses the same Worker at `POST /linq/webhook`. The Worker secret `APP_BASE_URL` is the always-on Fly FastAPI origin `https://hidden-beacon-080.fly.dev` (no path). The laptop is not the prod FastAPI host. The laptop tunnel is the local-dev path and the media host. `verify-webhooks` is the lever for the Worker. This file is the map of the two paths. Update it when the Worker routes, the env split, or the Linq hook change. There is no `call-agent-web` map in this repository.

## Sub-features

- `prod-worker` Telnyx Call Control application `webhook_event_url` is `https://call-agent-webhooks.usetreena.workers.dev/webhooks/telnyx`.
- `dev-tunnel` empty `PUBLIC_WEBHOOK_URL` keeps per-call webhook and media on `PUBLIC_BASE_URL`.
- `media-host` `stream_url` always comes from `PUBLIC_BASE_URL`. The Worker `/media-stream` is 501.
- `cli-live-webhook` CLI `--live` needs the tunnel for per-call `webhook_url` and `stream_url` even when the application webhook is the Worker.
- `unknown-call-404` FastAPI `POST /webhooks/telnyx` with an unknown `call_control_id` is 404. The Worker unsigned POST is 401.
- `linq-hook` `POST /linq/webhook` and `POST /webhooks/linq` forward to `APP_BASE_URL`. Unset `APP_BASE_URL` is 501 `missing_app_base_url`. They do not send SMS.
- `fly-host` Worker `APP_BASE_URL` is the always-on Fly origin `https://hidden-beacon-080.fly.dev` (no path). The laptop is not the prod FastAPI host.

## How to get to it (user POV)

- The live Linq register URL is `https://call-agent-webhooks.usetreena.workers.dev/linq/webhook`.
- Set the Telnyx Call Control application webhook to `https://call-agent-webhooks.usetreena.workers.dev/webhooks/telnyx`.
- For Mac CLI `--live`, leave `PUBLIC_WEBHOOK_URL` empty and set `PUBLIC_BASE_URL` to a tunnel to this process so per-call `webhook_url` and `stream_url` hit BOOK.
- Set Worker secret `APP_BASE_URL` to the always-on Fly origin `https://hidden-beacon-080.fly.dev` (no path). The laptop is not the prod host.
- Run `call-agent verify-webhooks` to prove the Worker inbox. The recipe is [verify-webhooks.md](./verify-webhooks.md).

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. Node 22 is on `PATH` for the Worker lever. No Cloudflare account is required for stub proof.

- **Worker lever.** Run `.venv/bin/call-agent verify-webhooks`. Exit code `0`. Five `[PASS]` lines in order `health`, `signature`, `idempotency`, `linq_hook`, `media_gap`, then `5/5 passed`.
- **Tunnel default.** With `PUBLIC_WEBHOOK_URL` unset, `webhook_and_stream_urls` uses `PUBLIC_BASE_URL` for both origins. `tests/test_verify_webhooks.py` `test_webhook_url_follows_public_base_when_unset` is the proof.
- **Split origins.** With `PUBLIC_WEBHOOK_URL=https://call-agent-webhooks.usetreena.workers.dev` and `PUBLIC_BASE_URL=https://tunnel.example.test`, webhook is `https://call-agent-webhooks.usetreena.workers.dev/webhooks/telnyx` and stream is `wss://tunnel.example.test/media-stream`. `test_webhook_url_splits_from_media_origin` is the proof. Do not use that split for Mac CLI `--live`; the Worker does not own BOOK.
- **Unknown call 404.** `pytest tests/test_server.py -k unknown_call_webhook`. FastAPI returns 404 `unknown call`. Through a working tunnel the same POST is 404. The Worker unsigned POST is 401. That 404 is the delivery proof that the tunnel reached BOOK lookup.
- **Map rows.** `docs/FEATURE_MAP.md` section `Always-on webhooks` lists the Worker routes, Fly Machines FastAPI, `live CLI ingress`, `unknown-call 404`, and `local FastAPI tunnel`.

## Gotchas

- There is no `call-agent-web` map in this repository. Do not invent one. The Worker name is `call-agent-webhooks`.
- Pointing `PUBLIC_BASE_URL` at the Worker sends media to `/media-stream` and gets 501. Keep media on the tunnel.
- CLI `book --live` and `quick-ask --live` host CallHandle in the laptop process. `PUBLIC_BASE_URL` must be a tunnel to that process for `stream_url` and per-call `webhook_url`. Leave `PUBLIC_WEBHOOK_URL` empty for CLI live. The Call Control application `webhook_event_url` can stay on the Worker. Fly `https://hidden-beacon-080.fly.dev` is a different process with no BOOK entry. The live verb probes `{PUBLIC_BASE_URL}/health` until HTTP 200 and `serve_token` match before it dials. A printed trycloudflare URL is not enough; DNS can lag.
- Through a working tunnel, `POST /webhooks/telnyx` with an unknown `call_control_id` returns 404 `unknown call`. That is this process. The Worker unsigned POST is 401.
- The Linq POST is a Worker forward to FastAPI, not SMS. Unset `APP_BASE_URL` is 501 `missing_app_base_url`. FastAPI `POST /linq/webhook` verifies HMAC and replies via MessagingPort. Prod `APP_BASE_URL` is `https://hidden-beacon-080.fly.dev` (no path).
- The live hostname is `call-agent-webhooks.usetreena.workers.dev`. A wrangler temporary preview can expire unless claimed. Claiming keeps that hostname.
