# Linq and Telnyx

Telnyx places the PSTN call. Linq Partner API V3 owns the peer iMessage kickoff thread (Instinct/Orchid class). That is not Apple Messages for Business. SMS stays off.

Closed-alpha testers text Treena on this Linq path. The sending line is the one shared Treena iMessage bot (`LINQ_FROM_E164`; Cap/ops sandbox `+12138653024` until ~Sep 18). That is not a unique DID per user, not a public landing claim, and not a blue-bubble marketing claim. Builder-box outbound prove (not this VM): `message.parts` text from `+12138653024` landed on `+17272701288`.

## Settings

Read these from env. Do not hardcode the sandbox line as the only source. `.env.example` documents the Cap-confirmed sandbox value.

- `LINQ_API_TOKEN`. Bearer token. Required for live send and `linq register-webhook`.
- `LINQ_FROM_E164`. The one shared Treena iMessage bot (Text Treena). Sitewide. Not a per-user DID and not Telnyx FROM (`TELNYX_FROM_NUMBER`).
- `LINQ_WEBHOOK_SIGNING_SECRET`. Standard Webhooks secret (`whsec_...`) from the create-subscription response. Lives on Builder's box. Not in this VM.
- `LINQ_WEBHOOK_URL` or `PUBLIC_WEBHOOK_URL`. Always-on HTTPS URL for inbound. This is the Cloudflare Workers target, not `PUBLIC_BASE_URL`.

Stub mode needs none of them. `LiveLinqHttp` fails loud if the token is missing.

## Outbound

`POST https://api.linqapp.com/api/partner/v3/chats` with `from`, `to[]`, and `message.parts` text. `preferred_service` is pinned to `iMessage`. Follow-up is `POST /chats/{chatId}/messages`.

## Inbound

Linq posts to the registered Workers URL. The Worker forwards POST to `APP_BASE_URL/linq/webhook` with the raw body and Standard Webhooks headers. Unset `APP_BASE_URL` is 501 `missing_app_base_url`. FastAPI in this process verifies HMAC and maps iMessage `message.received` into `InboundMessage`. Non-kickoff chat sends a `Received: {text}` ack on the same chat via MessagingPort (`POST /chats/{chatId}/messages`). Other subscription events (`message.sent`, `message.delivered`, `message.read`, `message.failed`) and SMS are ignored. Processed inbound writes `inbound.json` (inbound text, ids, timing) and `reply.json` under `CALL_AGENT_ARTIFACTS_DIR/linq/<event_id>/` for echo. Collect writes `linq/pending/<chat_id>.json` plus that event's `collect.json` and `replies.jsonl`. Kickoff writes `kickoff.json`, `replies.jsonl`, and `call.json` there, plus `<call_control_id>/linq.json` in the call run dir. Missing artifact dir fails loud (500), not `{ok:true}` without a send.

## Kickoff

L1 testers text a natural book on the same chat. Restaurant book is the prove job, not a forked agent.

```
User: can you make a reservation with this restaurant at 7:30 for a party of two?
Treena: okay, sounds good. What's the restaurant's phone number?
User: +14155552671
Treena: Okay, I'm going to call this number. Do you confirm? Reply "approve"
User: approve
Treena: Okay, I'm going to call them now.
```

Party and time come from the first text when present. If time is missing, Treena asks for time (or party and time) before the restaurant phone number. `at <restaurant>` is not a time. "2 people" alone does not unlock the phone ask. After the number she always asks to confirm before dial. Confirm / approve / yes are accepted. The first message never dials, even when it already includes `+E164`. Caller name comes from the `users` row for the inbound phone, never from iMessage and never from `LINQ_KICKOFF_CALLER`. Website `/start` collects that name; after signup the copy is in settings. `BookRequest.user_id` is that row's `user_id`. The outbound Telnyx caller ID is the shared env (`TELNYX_FROM_NUMBER` / `TELNYX_PHONE_NUMBER`), not `users.from_did`, not the shared Linq bot, and not `ledger.provision`. No row or a row with no first name texts a plain line (see [accounts.md](accounts.md)). Empty `users.from_did` still collects. Anything else stays on the `Received:` echo. Collect and confirm reply 200. Approve returns 202 `{ok, kicked_off, event_id}` and runs `run_call` in this process. After hangup it texts the result on `POST /chats/{chat_id}/messages`. Failed talk and place-exceptions use friendly `user_text` (`I couldn't finish the call` / `I couldn't place the call`) and never echo raw R2, env, or HTTP. Mid-call chat is not shipped; clarification needed texts a plain intake line. Cap recorded `live-prove: PASS (#34)`. qa owns live-prove sign-off (TRE-19). This tree does not redial.

Prove the stub path:

```bash
call-agent verify-linq-call
```

Expect fourteen `[PASS]` lines and `14/14 passed`.

`POST /webhooks/linq` and `POST /linq/webhook` on FastAPI require `LINQ_WEBHOOK_SIGNING_SECRET`. They verify `webhook-id`, `webhook-timestamp`, and `webhook-signature` on the raw body. Missing secret or bad signature is 401. Live reply needs `LINQ_API_TOKEN` and `LINQ_FROM_E164`. Signature checks, parse, stub reply, and artifacts are stub-testable with no public URL and no box secret. Prod `APP_BASE_URL` is the always-on Fly origin `https://hidden-beacon-080.fly.dev` (no path). Nathan's laptop is not that host.

Registered subscription (already live):

- `target_url`: `https://call-agent-webhooks.usetreena.workers.dev/linq/webhook?version=2026-02-03`
- `subscription_id`: `4c1ad6c6-d674-4fa2-b63a-ca947f2d9b7c`
- events: `message.received`, `message.sent`, `message.delivered`, `message.read`, `message.failed`

Polling `GET https://api.linqapp.com/api/partner/v3/chats/{chatId}/messages` remains a backup while the FastAPI host is down. Prod `APP_BASE_URL` is `https://hidden-beacon-080.fly.dev`. A temp tunnel can be `APP_BASE_URL` for laptop-only processing.

## Register the webhook

The closed-alpha subscription above is already registered. To register again after a URL change:

```bash
export LINQ_API_TOKEN=...
export LINQ_WEBHOOK_URL=https://call-agent-webhooks.usetreena.workers.dev/linq/webhook?version=2026-02-03
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

Expect six `[PASS]` lines and `6/6 passed`. Pytest covers the same lever. The `inbound_reply` line names the inbound text.

## Privacy

Do not log the token or raw message bodies. Share `call_report.redacted.json` for calls. Messaging fixtures in tests use synthetic text only.
