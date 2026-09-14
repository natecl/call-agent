# Verify accounts

`verify-accounts` is the `users` row lever. It seeds a throwaway SQLite `users` row, proves omit-preserve, drives in-process HMAC `POST /accounts/sync` and `GET /accounts/dashboard`, and checks there is no `users.name` column. It exits `0` only when all five pass. It never POSTs Fly and never opens Neon or R2. CI runs it on every push.

## Sub-features

- `verify-accounts-pass` prints five `[PASS]` lines and `5/5 passed` in stub mode.
- `verify-accounts-exit` exits `0` on pass and `1` on any failure.
- `cli-roundtrip` upserts `first_name`, `from_did`, prefs, and minutes, then get/list match.
- `omit-preserve` omitted `first_name`, `prefs`, `minutes_used`, and `from_did` keep the existing values.
- `sync-hmac` unsigned POST is 401; signed POST upserts Stripe ids without wiping name or from-DID.
- `dashboard-get` unsigned GET is 401; signed GET packages `users` plus computed `user.name` and sitewide `bot_number` from `LINQ_FROM_E164`. Unknown phone is empty lists. Dashboard UI stays on Pages (web).
- `no-name-column` schema has `first_name` and `prefs`, not `users.name`.

## How to get to it (user POV)

- Run `call-agent verify-accounts` in a terminal.
- CI runs `call-agent verify-accounts`.
- `pytest tests/test_verify_accounts.py` calls the same function.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. This verb creates its own temporary workspace and a throwaway `ACCOUNT_SYNC_SECRET`. Leave `NEON_DATABASE_URL` unset.

- **CI command.** Run `.venv/bin/call-agent verify-accounts`. Exit code `0`. Stdout has exactly five lines that start with `[PASS]`, in this order: `cli_roundtrip`, `omit_preserve`, `sync_hmac`, `dashboard_get`, `no_name_column`. The last line is `5/5 passed`.
- **Proof.** Save stdout as `verify-accounts.txt` in the evidence directory.

## Gotchas

- This lever never hits Fly. Fly `POST /accounts/sync` and `GET /accounts/dashboard` are intended production routes; this verb proves the in-process handlers.
- Dashboard UI stays on Pages (call-agent-web). This verb only packages the Fly read.
- Name is collected on website `/start` and copied into settings after signup. Kickoff never collects a name over iMessage.
- `minutes_included` defaults to 0 on `AccountWrite` and is not an omit-preserve field. `minutes_used` is.
- This skill never sets `NEON_DATABASE_URL` and never `fly deploy`.
