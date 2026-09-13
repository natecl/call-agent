# Accounts (`users` row)

The `users` table is the runtime account record. Kickoff looks it up by inbound phone. Stripe stays checkout and billing. call-agent-web POSTs a signed body to Fly `POST /accounts/sync`; Fly is the sole writer. CLI `account upsert --from-did` seeds a row, including the assigned Treena number, until that website PR lands. Name is `first_name` + `prefs.full_name`. There is no `users.name` column. Signed `GET /accounts/dashboard` packages that row plus linq artifacts so the website does not open Neon or R2. This skill never sets `NEON_DATABASE_URL` or `ACCOUNT_SYNC_SECRET`.

## Sub-features

- `users-row` one `users` row per phone: `first_name`, phone, assigned Treena `from_did`, minutes, retain, prefs, Stripe ids. No `users.name` column.
- `from-did` nullable E.164 Telnyx caller ID. Rejected when equal to `phone`. Additive `ALTER TABLE` on existing SQLite and Neon `users` tables.
- `omit-preserve` omitted `first_name`, `prefs`, `minutes_used`, and `from_did` keep the existing values. Explicit `minutes_used` `0` writes 0.
- `account-upsert` CLI seed without HMAC. `--from-did` sets the Treena number. Omitted `--from-did` and `--name` keep existing values.
- `account-get` read-back of that phone, including `from_did`.
- `account-list` every row (`USER PHONE NAME FROM_DID MINUTES RETAIN`).
- `accounts-sync` HMAC `POST /accounts/sync` upserts; Fly is the only writer. Omitted name/prefs/`minutes_used`/`from_did` keep seeded values.
- `accounts-dashboard` HMAC `GET /accounts/dashboard?phone=` packages `users` + linq thread index + retained `call_report`/transcript. Empty lists when unknown or wiped.
- `kickoff-lookup` inbound phone → `users.first_name`, `users.user_id`, and `users.from_did` for the call.

## How to get to it (user POV)

- Run `call-agent account upsert --phone +14155552671 --name Nathan --from-did +15555550177`.
- Run `call-agent account get --phone +14155552671`.
- Run `call-agent account list`.
- Website checkout/settings POST to Fly `/accounts/sync` (call-agent-web follow-up). Until then, CLI seed is the L1 path. The website is not expected to send `from_did` this climb. Stripe events without a name must omit `first_name`.
- Website dashboard GET to Fly `/accounts/dashboard?phone=` with the same HMAC family. Web must not open Neon/R2.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. Stub mode needs no Stripe, Telnyx, or Linq keys.

- **Upsert.** Run `.venv/bin/call-agent account upsert --phone +14155552671 --name Nathan --user acctnathan --from-did +15555550177`. Exit code `0`. Stdout is JSON with `"user_id": "acctnathan"`, `"first_name": "Nathan"`, and `"from_did": "+15555550177"`.
- **Get.** Run `.venv/bin/call-agent account get --phone +14155552671`. Exit code `0`. Same `user_id` and `from_did`.
- **List.** Run `.venv/bin/call-agent account list`. Exit code `0`. A row for `acctnathan`, `+14155552671`, and `+15555550177`.
- **Missing.** Run `.venv/bin/call-agent account get --phone +15555550199`. Exit code `1`. Stderr contains `no account for`.
- **Proof.** Keep those four commands in `transcript.txt`. `get` matching `upsert` is the read-back proof.

## Gotchas

- SQLite `users` is the same file as `dids`. On Fly, set `NEON_DATABASE_URL` or the row dies on redeploy.
- `account upsert` does not need `ACCOUNT_SYNC_SECRET`. The HTTP routes do.
- Kickoff `BookRequest.user_id` is `users.user_id`. The outbound Telnyx caller ID is `users.from_did`. Confirm/dial never calls `ledger.provision`.
- `from_did` must not equal `phone`. Personal iMessage number is not the Treena from-number.
- Omitted `--from-did` / `--name` (and omitted `from_did` / `first_name` / `prefs` / `minutes_used` on `/accounts/sync`) keep existing values so Stripe sync cannot wipe an ops-seeded DID, name, or minutes count. Explicit `minutes_used` `0` is a write.
- Dashboard HMAC signs the E.164 `phone` query value, not the URL-encoded query string. Unsigned GET is 401. Unknown phone returns empty lists.
- Do not collect a name over iMessage. Do not set `LINQ_KICKOFF_CALLER` or `LINQ_KICKOFF_USER_ID`.
- This skill never POSTs `/accounts/sync` live, never GETs `/accounts/dashboard` live, and never deploys Fly.
