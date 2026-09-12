# Always-on webhooks versus local tunnel

Production Telnyx Call Control posts go to the Cloudflare Worker inbox. Closed alpha iMessage inbound uses the same Worker at `POST /linq/webhook`. The laptop tunnel is the local-dev path and the media host. `verify-webhooks` is the lever for the Worker. This file is the map of the two paths. Update it when the Worker routes, the env split, or the Linq hook change. There is no `call-agent-web` map in this repository.

## Sub-features

- `prod-worker` Telnyx posts to `PUBLIC_WEBHOOK_URL/webhooks/telnyx` on `call-agent-webhooks`.
- `dev-tunnel` empty `PUBLIC_WEBHOOK_URL` keeps webhook and media on `PUBLIC_BASE_URL`.
- `media-host` `stream_url` always comes from `PUBLIC_BASE_URL`. The Worker `/media-stream` is 501.
- `linq-hook` `POST /linq/webhook` and `POST /webhooks/linq` return `implemented: false` and do not send SMS.

## How to get to it (user POV)

- The live Linq register URL is `https://call-agent-webhooks.blossom-linen.workers.dev/linq/webhook`.
- Set the Telnyx Call Control webhook and `PUBLIC_WEBHOOK_URL` to `https://call-agent-webhooks.blossom-linen.workers.dev`.
- Leave `PUBLIC_WEBHOOK_URL` empty and set `PUBLIC_BASE_URL` to a tunnel for laptop-only webhook tests.
- Run `call-agent verify-webhooks` to prove the Worker inbox. The recipe is [verify-webhooks.md](./verify-webhooks.md).

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. Node 22 is on `PATH` for the Worker lever. No Cloudflare account is required for stub proof.

- **Worker lever.** Run `.venv/bin/call-agent verify-webhooks`. Exit code `0`. Five `[PASS]` lines in order `health`, `signature`, `idempotency`, `linq_hook`, `media_gap`, then `5/5 passed`.
- **Tunnel default.** With `PUBLIC_WEBHOOK_URL` unset, `webhook_and_stream_urls` uses `PUBLIC_BASE_URL` for both origins. `tests/test_verify_webhooks.py` `test_webhook_url_follows_public_base_when_unset` is the proof.
- **Split origins.** With `PUBLIC_WEBHOOK_URL=https://call-agent-webhooks.blossom-linen.workers.dev` and `PUBLIC_BASE_URL=https://tunnel.example.test`, webhook is `https://call-agent-webhooks.blossom-linen.workers.dev/webhooks/telnyx` and stream is `wss://tunnel.example.test/media-stream`. `test_webhook_url_splits_from_media_origin` is the proof.
- **Map rows.** `docs/FEATURE_MAP.md` section `Always-on webhooks` lists the Worker routes next to `local FastAPI tunnel`.

## Gotchas

- There is no `call-agent-web` map in this repository. Do not invent one. The Worker name is `call-agent-webhooks`.
- Pointing `PUBLIC_BASE_URL` at the Worker sends media to `/media-stream` and gets 501. Keep media on the tunnel.
- The Linq 200 is a hook, not SMS.
- The live hostname is `call-agent-webhooks.blossom-linen.workers.dev`. A wrangler temporary preview can expire unless claimed. Claiming keeps that hostname.
