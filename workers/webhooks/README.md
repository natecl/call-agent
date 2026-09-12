# Always-on webhook inbox

This Worker is the production HTTPS URL for Telnyx Call Control webhooks. It stays up when the laptop is off. It is not the call owner and it does not terminate media.

## Public URL (register now)

Live Worker origin on the current wrangler preview account (Blossom Linen):

- **Linq (register this).** `https://call-agent-webhooks.blossom-linen.workers.dev/linq/webhook`
- Linq alias. `https://call-agent-webhooks.blossom-linen.workers.dev/webhooks/linq`
- Telnyx. `https://call-agent-webhooks.blossom-linen.workers.dev/webhooks/telnyx`
- Health. `https://call-agent-webhooks.blossom-linen.workers.dev/health`

Closed alpha L1 testers text Treena through Linq. Builder can register the Linq URL now. `POST` and `GET /linq/webhook` return `{"ok":true,"provider":"linq","implemented":false}` and do not send SMS. Telnyx always-on stays on `/webhooks/telnyx`.

Put the Telnyx URL on the Call Control application and on `PUBLIC_WEBHOOK_URL`. The laptop tunnel is interim only. This Worker origin is the prod webhook path.

A wrangler `--temporary` preview can expire unless the account is claimed. Claiming keeps this same `*.blossom-linen.workers.dev` hostname. Some datacenter POSTs currently hit a Cloudflare managed challenge before the Worker. Linq's origin may still land. `GET /health` with a normal client UA returns `{"ok":true}`.

## Deploy

```bash
cd workers/webhooks
npm install
npx wrangler secret put TELNYX_PUBLIC_KEY
npx wrangler deploy
```

`TELNYX_PUBLIC_KEY` is the Mission Control account public key. Do not put it in `wrangler.jsonc` or git. Optional `UPSTREAM_WEBHOOK_URL` forwards a first-seen Telnyx body to a live FastAPI process. The Worker still returns 2xx if that process is down.

Dry-run without an account:

```bash
npx wrangler deploy --dry-run
```

## Prove it

Stub, no deploy, generated Ed25519 key. This is the signature and idempotency proof:

```bash
cd workers/webhooks && npm run verify
# or from the repo root
call-agent verify-webhooks
```

You should see five `[PASS]` lines and `5/5 passed`.

Against the live Worker:

```bash
call-agent verify-webhooks --url https://call-agent-webhooks.blossom-linen.workers.dev
```

Remote proof checks wiring. Unsigned `POST /webhooks/telnyx` must be 401. If it is 200, the public key is missing and the check fails loud. A real Telnyx private key is not on this machine, so remote mode cannot mint a valid account signature. Run the stub verb for the signed-accept and duplicate checks. A Cloudflare challenge page is a loud fail (`cloudflare_challenge`), not a 200.

## Media

`GET` or `POST /media-stream` returns 501 `media_not_terminated_here`. Telnyx still needs a reachable `wss://` host. Keep `PUBLIC_BASE_URL` on the laptop tunnel or a future always-on media host. Do not point media at this Worker.
