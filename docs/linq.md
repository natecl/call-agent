# Linq and Telnyx

Telnyx places the PSTN call. Linq Partner API V3 owns the peer iMessage kickoff thread (Instinct/Orchid class). That is not Apple Messages for Business. SMS stays off.

Closed-alpha testers text Treena on this Linq path. The sending line is the sandbox DID (`LINQ_FROM_E164`; sandbox has used `+12138653024`). That is not a public landing claim and not a blue-bubble marketing claim. Builder-box outbound prove (not this VM): `message.parts` text from `+12138653024` landed on `+17272701288`.

## Settings

Read these from env. Do not hardcode the sandbox line as the only source.

- `LINQ_API_TOKEN`. Bearer token. Required for live send and `linq register-webhook`.
- `LINQ_FROM_E164`. Sending line.
- `LINQ_WEBHOOK_SIGNING_SECRET`. Standard Webhooks secret (`whsec_...`) from the create-subscription response. Lives on Builder's box. Not in this VM.
- `LINQ_WEBHOOK_URL` or `PUBLIC_WEBHOOK_URL`. Always-on HTTPS URL for inbound. This is the Cloudflare Workers target, not `PUBLIC_BASE_URL`.

Stub mode needs none of them. `LiveLinqHttp` fails loud if the token is missing.

## Outbound

`POST https://api.linqapp.com/api/partner/v3/chats` with `from`, `to[]`, and `message.parts` text. `preferred_service` is pinned to `iMessage`. Follow-up is `POST /chats/{chatId}/messages`.

## Inbound

Linq posts to the registered Workers URL. The Worker forwards POST to `APP_BASE_URL/linq/webhook` with the raw body and Standard Webhooks headers. Unset `APP_BASE_URL` is 501 `missing_app_base_url`. FastAPI in this process verifies HMAC, then maps `message.received` into `InboundMessage`. Other subscription events (`message.sent`, `message.delivered`, `message.read`, `message.failed`) and SMS are ignored.

`POST /webhooks/linq` and `POST /linq/webhook` on FastAPI require `LINQ_WEBHOOK_SIGNING_SECRET`. They verify `webhook-id`, `webhook-timestamp`, and `webhook-signature` on the raw body. Missing secret or bad signature is 401. Signature checks and parse are stub-testable with no public URL and no box secret. Live processing still needs an always-on FastAPI host with `LINQ_WEBHOOK_SIGNING_SECRET`. Nathan's laptop is not that host.

Registered subscription (already live):

- `target_url`: `https://call-agent-webhooks.blossom-linen.workers.dev/linq/webhook?version=2026-02-03`
- `subscription_id`: `4c1ad6c6-d674-4fa2-b63a-ca947f2d9b7c`
- events: `message.received`, `message.sent`, `message.delivered`, `message.read`, `message.failed`

Polling `GET https://api.linqapp.com/api/partner/v3/chats/{chatId}/messages` remains a backup while the FastAPI host is down. A temp tunnel can be `APP_BASE_URL` for laptop-only processing.

## Register the webhook

The closed-alpha subscription above is already registered. To register again after a URL change:

```bash
export LINQ_API_TOKEN=...
export LINQ_WEBHOOK_URL=https://call-agent-webhooks.blossom-linen.workers.dev/linq/webhook?version=2026-02-03
call-agent linq register-webhook
```

The command fails loud if the token or URL is missing. Store the printed `signing_secret` as `LINQ_WEBHOOK_SIGNING_SECRET`. Linq will not return that secret again.

Equivalent curl:

```bash
curl -X POST https://api.linqapp.com/api/partner/v3/webhook-subscriptions \
  -H "Authorization: Bearer $LINQ_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"subscribed_events\":[\"message.received\"],\"target_url\":\"$LINQ_WEBHOOK_URL\"}"
```

## Prove the stub path

```bash
call-agent verify-message
```

Expect five `[PASS]` lines and `5/5 passed`. Pytest covers the same lever.

## Privacy

Do not log the token or raw message bodies. Share `call_report.redacted.json` for calls. Messaging fixtures in tests use synthetic text only.
