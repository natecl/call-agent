# Accounts (`users` row)

The `users` table is the runtime account record. Cap model: one shared Treena iMessage bot (`LINQ_FROM_E164`); one shared Telnyx FROM (`TELNYX_FROM_NUMBER` / `TELNYX_PHONE_NUMBER`); each account stores the user's own phone. Kickoff looks it up by inbound phone. Stripe stays checkout and billing. call-agent-web POSTs a signed body to Fly `POST /accounts/sync`; Fly is the sole writer. Those Fly routes (`POST /accounts/sync` and `GET /accounts/dashboard`) are intended, not accidental. Dashboard UI stays on Pages (web). CLI `account upsert` seeds a row until that website PR lands. `--from-did` still writes the legacy column; kickoff does not use it. Name is collected on website `/start` and that copy is in settings after signup. Kickoff reads `users.first_name`. Never collected over iMessage. Display name is `first_name` + `prefs.full_name`. There is no `users.name` column. Signed `GET /accounts/dashboard` packages that row, the sitewide `bot_number`, plus linq artifacts so the website does not open Neon or R2. Prove with `call-agent verify-accounts`. This skill never sets `NEON_DATABASE_URL` or `ACCOUNT_SYNC_SECRET`.

## Sub-features

- `users-row` one `users` row per phone: `first_name`, the user's own phone, legacy Telnyx `from_did` (not unique, not required at kickoff), minutes, retain, prefs, Stripe ids. No `users.name` column.
- `from-did` nullable legacy E.164. Not unique. Rejected when equal to `phone`. Additive `ALTER TABLE` on existing SQLite and Neon `users` tables. Not the shared iMessage bot and not the live Telnyx FROM.
- `shared-bot` `LINQ_FROM_E164` is the one Text Treena number (sandbox `+12138653024` until ~Sep 18). Dashboard `bot_number` is that key.
- `shared-telnyx-from` `TELNYX_FROM_NUMBER` / `TELNYX_PHONE_NUMBER` is the one Telnyx FROM (Cap `+12135690899`). Kickoff dials from that env, not `users.from_did`.
- `omit-preserve` omitted `first_name`, `prefs`, `minutes_used`, `minutes_prepaid`, and `from_did` keep the existing values. Explicit `minutes_used` / `minutes_prepaid` `0` writes 0.
- `account-upsert` CLI seed without HMAC. `--phone` is the user's number. `--from-did` still writes the legacy column. Omitted `--from-did` and `--name` keep existing values.
- `account-get` read-back of that phone, including `from_did`.
- `account-list` every row (`USER PHONE NAME FROM_DID MINUTES RETAIN`).
- `accounts-sync` HMAC `POST /accounts/sync` upserts; Fly is the only writer. Omitted name/prefs/`minutes_used`/`from_did` keep seeded values.
- `accounts-dashboard` HMAC `GET /accounts/dashboard?phone=` packages `users` + `minutes` snapshot + `metered_pack` ($1.00 / 5 min, max 12 packs, Stripe TEST) + `bot_number` + linq thread index + retained `call_report`/transcript. Empty lists when unknown or wiped.
- `kickoff-lookup` inbound phone → `users.first_name` and `users.user_id`. Telnyx FROM is `TELNYX_FROM_NUMBER`.

## How to get to it (user POV)

- Run `call-agent account upsert --phone +14155552671 --name Nathan --from-did +15555550177 --minutes 30`.
- Run `call-agent account get --phone +14155552671`.
- Run `call-agent account list`.
- Website checkout/settings POST to Fly `/accounts/sync` (intended Fly route). Until the writer is live, CLI seed is the L1 path. Name is collected on website `/start` and copied into settings after signup. The website is not expected to send `from_did` this climb. Stripe events without a name must omit `first_name`.
- Website dashboard UI stays on Pages. Fly `GET /accounts/dashboard?phone=` with the same HMAC family is the packaged read, including `bot_number`. Web must not open Neon/R2.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. Stub mode needs no Stripe, Telnyx, or Linq keys.

- **Upsert.** Run `.venv/bin/call-agent account upsert --phone +14155552671 --name Nathan --user acctnathan --from-did +15555550177 --minutes 30`. Exit code `0`. Stdout is JSON with `"user_id": "acctnathan"`, `"first_name": "Nathan"`, and `"from_did": "+15555550177"`.
- **Get.** Run `.venv/bin/call-agent account get --phone +14155552671`. Exit code `0`. Same `user_id` and `from_did`.
- **List.** Run `.venv/bin/call-agent account list`. Exit code `0`. A row for `acctnathan`, `+14155552671`, and `+15555550177`.
- **Missing.** Run `.venv/bin/call-agent account get --phone +15555550199`. Exit code `1`. Stderr contains `no account for`.
- **Stub lever.** Run `.venv/bin/call-agent verify-accounts`. Exit code `0`. Five `[PASS]` lines and `5/5 passed`.
- **Proof.** Keep those four commands in `transcript.txt`. `get` matching `upsert` is the read-back proof. Save `verify-accounts` stdout as `verify-accounts.txt`.

## Gotchas

- SQLite `users` is the same file as `dids`. On Fly, set `NEON_DATABASE_URL` or the row dies on redeploy. Live Fly sha `bb91810` (PR #39) has `psycopg[binary]` baked (health 200). A hot-install does not survive the next image deploy.
- `account upsert` does not need `ACCOUNT_SYNC_SECRET`. The HTTP routes do.
- Kickoff `BookRequest.user_id` is `users.user_id`. The outbound Telnyx caller ID is `TELNYX_FROM_NUMBER` / `TELNYX_PHONE_NUMBER`. Confirm/dial never calls `ledger.provision`. The iMessage bot is `LINQ_FROM_E164`. Kickoff ignores `users.from_did`.
- `from_did` must not equal `phone`. Personal number is not the Telnyx from-number. Two accounts may share one legacy `from_did`.
- Omitted `--from-did` / `--name` (and omitted `from_did` / `first_name` / `prefs` / `minutes_used` on `/accounts/sync`) keep existing values so Stripe sync cannot wipe an ops-seeded DID, name, or minutes count. Explicit `minutes_used` `0` is a write.
- Dashboard HMAC signs the E.164 `phone` query value, not the URL-encoded query string. Unsigned GET is 401. Unknown phone returns empty lists. `bot_number` still comes from `LINQ_FROM_E164`.
- Do not collect a name over iMessage. Name is collected on website `/start` and is in settings after signup. Do not set `LINQ_KICKOFF_CALLER` or `LINQ_KICKOFF_USER_ID`.
- This skill never POSTs `/accounts/sync` live, never GETs `/accounts/dashboard` live, and never deploys Fly.
