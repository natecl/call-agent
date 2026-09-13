# Accounts sync contract

This is the contract for the follow-up **call-agent-web** PR. This repository (`call-agent`) owns the `users` table and is the sole writer. Stripe stays checkout and billing. Do not treat a Stripe Customer as the runtime account Fly reads. Neon is out of scope on the website today (TRE-20); the website must not open `NEON_DATABASE_URL`.

This tree does not contain `call-agent-web`. Builder’s second climb implements the POST. This tree does not claim that writer live.

## Runtime account

Fly reads `users` (same DB as `dids`: SQLite on a laptop, Neon when `NEON_DATABASE_URL` is set). Kickoff looks up `phone = inbound iMessage sender`. `BookRequest.caller` is `users.first_name`. `BookRequest.user_id` is `users.user_id`. The outbound Telnyx caller ID is `users.from_did` (the assigned Treena number). Confirm/dial never calls `ledger.provision` and never `POST /v2/number_orders`. `LINQ_KICKOFF_USER_ID` is gone. `kickoff.json` records `account.user_id`, phone, `from_did`, and minutes.

Columns (see `USERS_SCHEMA` in `call_agent/accounts.py`):

| column | role |
| --- | --- |
| `user_id` | lowercase `UserId` (uuid4 hex on insert if omitted). Kickoff `BookRequest.user_id`. |
| `phone` | E.164, unique, kickoff key (linked iMessage number). Not the Telnyx from-number. |
| `first_name` | nullable. Missing → Treena texts a plain account-settings line. Never collected in iMessage. Omitted on upsert keeps the existing value so Stripe webhooks without a name cannot wipe it. |
| `from_did` | nullable E.164 assigned Treena number. Telnyx `DialSpec.from_did`. Rejected when equal to `phone`. Not unique. Additive `ALTER TABLE` on existing SQLite and Neon `users` tables. Omitted on upsert keeps the existing value. |
| `stripe_customer_id` | unique billing link |
| `plan` | Stripe/TRE-20 plan metadata |
| `minutes_included` / `minutes_used` | recorded; kickoff does not refuse at zero or decrement. Omitted `minutes_used` keeps the existing value. Explicit `0` writes 0. |
| `retain_call_artifacts` | request flag on kickoff |
| `prefs` | JSON object of string values (other personal info jobs need, e.g. full name). Display name is `first_name` + `prefs.full_name`. There is no `users.name` column. Omitted on upsert keeps the existing object. |
| `stripe_synced_at` | last applied Stripe event time; older events skip |

## `POST /accounts/sync`

Fly origin: `https://hidden-beacon-080.fly.dev/accounts/sync`.

Headers:

- `Content-Type: application/json`
- `X-Account-Sync-Signature: sha256=<hex>` where the hex is HMAC-SHA256 of the **raw body** with `ACCOUNT_SYNC_SECRET` (same secret on Fly and the website).

Body:

```json
{
  "phone": "+14155552671",
  "first_name": "Nathan",
  "user_id": "optional-existing-id",
  "stripe_customer_id": "cus_...",
  "plan": "alpha",
  "minutes_included": 30,
  "minutes_used": 0,
  "retain_call_artifacts": false,
  "prefs": {"full_name": "Nathan Tester"},
  "stripe_event_created": "2026-09-12T12:00:00+00:00"
}
```

`phone` is required. `first_name` may be omitted (row exists, kickoff will ask them to set it in account settings). Omitted `first_name` keeps the existing value so a later Stripe webhook without a name cannot wipe it. `from_did` is optional. The website is not expected to send `from_did` this climb; omitted `from_did` keeps the ops-seeded value so Stripe sync cannot wipe it. Omitted `prefs` keeps the existing object. Omitted `minutes_used` keeps the existing count; explicit `0` writes 0. `stripe_event_created` is ISO-8601. Equal or older than `stripe_synced_at` returns `{"ok": true, "skipped": true, "user_id": "..."}`.

Status:

- `200` upsert or skip
- `400` invalid JSON / E.164 / first name
- `401` missing secret, missing header, or bad HMAC
- `409` this phone already belongs to another `stripe_customer_id`
- `500` ledger misconfig

## `GET /accounts/dashboard`

Fly origin: `https://hidden-beacon-080.fly.dev/accounts/dashboard?phone=+14155552671`.

Same HMAC family as POST `/accounts/sync`:

- `X-Account-Sync-Signature: sha256=<hex>` where the hex is HMAC-SHA256 of the **phone query value** (E.164, not URL-encoded) with `ACCOUNT_SYNC_SECRET`.

The website must not open `NEON_DATABASE_URL` or `R2_*`. This GET is the packaged read: `users` row plus linq artifacts on Fly.

`phone` is required. Missing secret, missing header, or bad HMAC is `401`. Missing or invalid phone is `400`. Unknown phone is `200` with `"user": null` and empty `threads` / `calls`.

```json
{
  "ok": true,
  "user": {
    "user_id": "acctnathan",
    "phone": "+14155552671",
    "first_name": "Nathan",
    "prefs": {"full_name": "Nathan Tester"},
    "name": {"first_name": "Nathan", "full_name": "Nathan Tester"}
  },
  "threads": [],
  "calls": []
}
```

`user.name` is computed from `first_name` + `prefs.full_name`. It is not a database column.

Fly scans `CALL_AGENT_ARTIFACTS_DIR/linq/*` (skips `pending/`) for this phone / `user_id`. Matching inbound/kickoff/call artifacts become `threads`. `calls` includes `call_report` and `transcript` only when those files still exist (retain-on). Default wipe deletes the run dir, so those lists stay empty even if `linq/<event_id>/call.json` remains.

Status:

- `200` packaged dashboard
- `400` missing / invalid phone
- `401` missing secret, missing header, or bad HMAC
- `500` ledger misconfig

## When the website should POST

Map TRE-20 (account = Stripe Customer with phone, optional name, plan/minutes/retain metadata) onto this body. Do not invent fields TRE-20 does not have.

- `checkout.session.completed`
- `customer.updated`
- `customer.subscription.updated` / `deleted`
- Account-settings save (name edit). Write Stripe as the billing copy, then POST Fly so the `users` row is what kickoff reads.

Idempotent upsert keyed on `stripe_customer_id`. First insert may omit `user_id` (Fly generates uuid4 hex). A CLI-seeded row with the same phone and no Stripe id is claimed by attaching `stripe_customer_id`.

Retry on 5xx (Fly down). Do not fall back to writing Neon from the website.

## L1 before the website PR

```bash
call-agent account upsert --phone +17272701288 --name Nathan --from-did +12135690899
```

That writes the same table, including the assigned Treena `from_did`. Personal phone stays the linked iMessage number, not the from-number. Do not pass the same E.164 as `--phone` and `--from-did`. Set Fly `ACCOUNT_SYNC_SECRET` when the website PR is ready. This climb does not `fly deploy`.

## Kickoff copy

No account for this inbound phone:

`I don't have an account for this number yet. Set one up in your account settings, then text me again.`

Row exists but `first_name` is empty:

`I need your name for the call. Add it in your account settings, then text me again.`

Row exists but `from_did` is empty:

`This account doesn't have a Treena calling number assigned yet. Ask support to assign one, then text me again.`

None of these is prefixed `Call failed:`. There is no `Call failed: missing caller name`. `LINQ_KICKOFF_CALLER` and `LINQ_KICKOFF_USER_ID` are gone.
