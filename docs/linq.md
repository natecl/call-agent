# Linq and Telnyx

Telnyx places the PSTN call. Linq Partner API V3 owns the peer iMessage kickoff thread (Instinct/Orchid class). That is not Apple Messages for Business. SMS stays off.

Closed-alpha testers text Treena on this Linq path. The sending line is the sandbox DID (`LINQ_FROM_E164`; sandbox has used `+12138653024`). That is not a public landing claim and not a blue-bubble marketing claim. Builder-box outbound prove (not this VM): `message.parts` text from `+12138653024` landed on `+17272701288`.

## Settings

Read these from env. Do not hardcode the sandbox line as the only source.

- `LINQ_API_TOKEN`. Bearer token. Required for live send and `linq register-webhook`.
- `LINQ_FROM_E164`. Sending line.
- `LINQ_WEBHOOK_SECRET`. Standard Webhooks secret (`whsec_...`) from the create-subscription response.
- `LINQ_WEBHOOK_URL` or `PUBLIC_WEBHOOK_URL`. Always-on HTTPS URL for inbound. This comes from the Cloudflare Workers track, not from `PUBLIC_BASE_URL`.

Stub mode needs none of them. `LiveLinqHttp` fails loud if the token is missing.

## Outbound

`POST https://api.linqapp.com/api/partner/v3/chats` with `from`, `to[]`, and `message.parts` text. `preferred_service` is pinned to `iMessage`. Follow-up is `POST /chats/{chatId}/messages`.

## Inbound

`POST /webhooks/linq` requires `LINQ_WEBHOOK_SECRET`. It verifies `webhook-id`, `webhook-timestamp`, and `webhook-signature` on the raw body, then maps `message.received` into `InboundMessage`. Missing secret or bad signature is 401. Signature checks and parse are stub-testable with no public URL.

Always-on public URL is the Workers track. Until that URL exists:

1. Poll `GET https://api.linqapp.com/api/partner/v3/chats/{chatId}/messages` with the same bearer token.
2. A temp tunnel may be used to catch webhooks. Do not treat a tunnel as production.

## Register the webhook after Workers lands

```bash
export LINQ_API_TOKEN=...
export LINQ_WEBHOOK_URL=https://<workers-host>/webhooks/linq
call-agent linq register-webhook
```

The command fails loud if the token or URL is missing. Store the printed `signing_secret` as `LINQ_WEBHOOK_SECRET`. Linq will not return that secret again.

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
