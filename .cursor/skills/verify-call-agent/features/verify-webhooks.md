# Verify webhooks

`verify-webhooks` is the always-on Worker lever. It drives the Cloudflare Worker inbox the way Telnyx and Linq will. Stub mode imports `createHandler` in-process with a generated Ed25519 key and needs no deploy. `--url` hits a deployed Worker and fails loud when unsigned Telnyx POST returns 200.

## Sub-features

- `verify-pass` prints five `[PASS]` lines and `5/5 passed` in stub mode.
- `verify-exit` exits `0` on pass and `1` on any failure.
- `verify-signature` accepts a signed Telnyx body and rejects an unsigned one with 401.
- `verify-idempotency` returns `duplicate: true` on a second POST with the same `data.id`.
- `verify-remote` with `--url` checks health, fail-closed unsigned POST, the Linq hook, and the media 501.

## How to get to it (user POV)

- Run `call-agent verify-webhooks` in a terminal.
- Run `call-agent verify-webhooks --url https://call-agent-webhooks.usetreena.workers.dev` against the live Worker.
- CI runs `npm run verify` in `workers/webhooks` and `call-agent verify-webhooks`.
- `pytest tests/test_verify_webhooks.py` calls the same function.

## Driving it with call-agent

Preconditions:

- Node 22 is on `PATH`. `workers/webhooks/scripts/verify.mjs` exists. Stub mode needs no Cloudflare account and no Telnyx key.

- **CI command.** Run `.venv/bin/call-agent verify-webhooks`. Exit code `0`. Stdout has exactly five lines that start with `[PASS]`, in this order: `health`, `signature`, `idempotency`, `linq_hook`, `media_gap`. The last line is `5/5 passed`.
- **Broken wiring.** Run `.venv/bin/call-agent verify-webhooks --url http://127.0.0.1:9`. Exit code `1`. Stdout has `[FAIL]` lines. This is the fail-loud path when the Worker is unreachable.
- **Deployed Worker.** Run `.venv/bin/call-agent verify-webhooks --url https://call-agent-webhooks.usetreena.workers.dev`. Unsigned `POST /webhooks/telnyx` must be 401. If it is 200, `signature` fails because `TELNYX_PUBLIC_KEY` is unwired. A Cloudflare challenge page fails as `cloudflare_challenge`. Remote mode cannot mint a real Telnyx account signature. Use stub mode for the signed-accept and duplicate checks.
- **Proof.** Save stdout as `verify-webhooks.txt` in the evidence directory.

## Gotchas

- The stub verb does not start wrangler and does not listen on a port. It constructs `createHandler` in the same Node process.
- `PUBLIC_BASE_URL` is the media host. Pointing it at the Worker makes Telnyx open `/media-stream` there and get 501. Set `PUBLIC_WEBHOOK_URL` for the Worker and keep media on the tunnel.
- Linq stub `POST /webhooks/linq` is 501 `missing_app_base_url`. GET ping is 200 `provider:linq`. That is the hook, not SMS. A live Worker that still returns `implemented: false` fails `linq_hook`. Prod Worker `APP_BASE_URL` is the always-on Fly host `https://hidden-beacon-080.fly.dev` (no path).
