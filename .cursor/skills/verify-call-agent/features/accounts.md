# Accounts (`users` row)

The `users` table is the runtime account record. Kickoff looks it up by inbound phone. Stripe stays checkout and billing. call-agent-web POSTs a signed body to Fly `POST /accounts/sync`; Fly is the sole writer. CLI `account upsert` seeds a row until that website PR lands. This skill never sets `NEON_DATABASE_URL` or `ACCOUNT_SYNC_SECRET`.

## Sub-features

- `users-row` one `users` row per phone: name, phone, minutes, retain, prefs, Stripe ids.
- `account-upsert` CLI seed without HMAC.
- `account-get` read-back of that phone.
- `account-list` every row.
- `accounts-sync` HMAC `POST /accounts/sync` upserts; Fly is the only writer.
- `kickoff-lookup` inbound phone → `users.first_name` for the call. DID line stays `LINQ_KICKOFF_USER_ID=default`.

## How to get to it (user POV)

- Run `call-agent account upsert --phone +14155552671 --name Nathan`.
- Run `call-agent account get --phone +14155552671`.
- Run `call-agent account list`.
- Website checkout/settings POST to Fly `/accounts/sync` (call-agent-web follow-up). Until then, CLI seed is the L1 path.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. Stub mode needs no Stripe, Telnyx, or Linq keys.

- **Upsert.** Run `.venv/bin/call-agent account upsert --phone +14155552671 --name Nathan --user acctnathan`. Exit code `0`. Stdout is JSON with `"user_id": "acctnathan"` and `"first_name": "Nathan"`.
- **Get.** Run `.venv/bin/call-agent account get --phone +14155552671`. Exit code `0`. Same `user_id`.
- **List.** Run `.venv/bin/call-agent account list`. Exit code `0`. A row for `acctnathan` and `+14155552671`.
- **Missing.** Run `.venv/bin/call-agent account get --phone +15555550199`. Exit code `1`. Stderr contains `no account for`.
- **Proof.** Keep those four commands in `transcript.txt`. `get` matching `upsert` is the read-back proof.

## Gotchas

- SQLite `users` is the same file as `dids`. On Fly, set `NEON_DATABASE_URL` or the row dies on redeploy.
- `account upsert` does not need `ACCOUNT_SYNC_SECRET`. The HTTP route does.
- Kickoff `BookRequest.user_id` is the DID line (`default`), not `users.user_id`. `kickoff.json` carries `account.user_id`.
- Do not collect a name over iMessage. Do not set `LINQ_KICKOFF_CALLER`.
- This skill never POSTs `/accounts/sync` live and never deploys Fly.
