# Always-on webhook inbox

This Worker is the production HTTPS URL for Telnyx Call Control webhooks. It stays up when the laptop is off. It is not the call owner and it does not terminate media.

## Public URL shape

After `npx wrangler deploy`:

- Telnyx: `https://call-agent-webhooks.<SUBDOMAIN>.workers.dev/webhooks/telnyx`
- Linq: `https://call-agent-webhooks.<SUBDOMAIN>.workers.dev/webhooks/linq`
- Linq alias: `https://call-agent-webhooks.<SUBDOMAIN>.workers.dev/linq/webhook`
- Health: `https://call-agent-webhooks.<SUBDOMAIN>.workers.dev/health`

`<SUBDOMAIN>` is the Cloudflare account workers.dev subdomain printed by the first deploy. Put the Telnyx URL on the Call Control application and on `PUBLIC_WEBHOOK_URL`. Builder can register the Linq URL now. That route returns `implemented: false` and does not send SMS.

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

Against a deployed Worker:

```bash
call-agent verify-webhooks --url https://call-agent-webhooks.<SUBDOMAIN>.workers.dev
```

Remote proof checks wiring. Unsigned `POST /webhooks/telnyx` must be 401. If it is 200, the public key is missing and the check fails loud. A real Telnyx private key is not on this machine, so remote mode cannot mint a valid account signature. Run the stub verb for the signed-accept and duplicate checks.

## Media

`GET` or `POST /media-stream` returns 501 `media_not_terminated_here`. Telnyx still needs a reachable `wss://` host. Keep `PUBLIC_BASE_URL` on the laptop tunnel or a future always-on media host. Do not point media at this Worker.
