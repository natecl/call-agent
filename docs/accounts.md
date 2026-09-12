# Accounts sync contract

This is the contract for the follow-up **call-agent-web** PR. This repository (`call-agent`) owns the `users` table and is the sole writer. Stripe stays checkout and billing. Do not treat a Stripe Customer as the runtime account Fly reads. Neon is out of scope on the website today (TRE-20); the website must not open `NEON_DATABASE_URL`.

This tree does not contain `call-agent-web`. Builder’s second climb implements the POST. This tree does not claim that writer live.

## Runtime account

Fly reads `users` (same DB as `dids`: SQLite on a laptop, Neon when `NEON_DATABASE_URL` is set). Kickoff looks up `phone = inbound iMessage sender`. `BookRequest.caller` is `users.first_name`. The outbound DID still comes from `dids` for `LINQ_KICKOFF_USER_ID=default`. `kickoff.json` records `account.user_id` so a later per-user DID climb can join.

Columns (see `USERS_SCHEMA` in `call_agent/accounts.py`):

| column | role |
| --- | --- |
| `user_id` | lowercase `UserId` (uuid4 hex on insert if omitted) |
| `phone` | E.164, unique, kickoff key |
| `first_name` | nullable. Missing → Treena texts a plain account-settings line. Never collected in iMessage. |
| `stripe_customer_id` | unique billing link |
| `plan` | Stripe/TRE-20 plan metadata |
| `minutes_included` / `minutes_used` | recorded; kickoff does not refuse at zero or decrement |
| `retain_call_artifacts` | request flag on kickoff |
| `prefs` | JSON object of string values (other personal info jobs need, e.g. full name) |
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

`phone` is required. `first_name` may be omitted (row exists, kickoff will ask them to set it in account settings). `stripe_event_created` is ISO-8601. Equal or older than `stripe_synced_at` returns `{"ok": true, "skipped": true, "user_id": "..."}`.

Status:

- `200` upsert or skip
- `400` invalid JSON / E.164 / first name
- `401` missing secret, missing header, or bad HMAC
- `409` this phone already belongs to another `stripe_customer_id`
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
call-agent account upsert --phone +1... --name Nathan
```

That writes the same table. Set Fly `ACCOUNT_SYNC_SECRET` when the website PR is ready. This climb does not `fly deploy`.

## Kickoff copy

No account for this inbound phone:

`I don't have an account for this number yet. Set one up in your account settings, then text me again.`

Row exists but `first_name` is empty:

`I need your name for the call. Add it in your account settings, then text me again.`

Neither is prefixed `Call failed:`. There is no `Call failed: missing caller name`. `LINQ_KICKOFF_CALLER` is gone.
