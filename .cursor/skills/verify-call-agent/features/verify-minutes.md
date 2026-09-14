# Verify minutes

`verify-minutes` is the minute-cap lever. It seeds a throwaway SQLite `users` row, proves kickoff refuses at included+prepaid 0, allows included remaining, unlocks with a 5-minute prepaid pack, enforces the 12-pack / 60-minute cap, locks pack price at $1.00, and refuses Stripe live keys. It exits `0` only when all six pass. It never POSTs Fly, never charges Stripe, and never dials Telnyx. CI runs it on every push.

## Sub-features

- `verify-minutes-pass` prints six `[PASS]` lines and `6/6 passed` in stub mode.
- `verify-minutes-exit` exits `0` on pass and `1` on any failure.
- `cap_blocks` remaining 0 texts the buy-a-pack line and does not dial.
- `included_allows` remaining included minutes still collect.
- `prepaid_unlocks` exhausted included + 5 prepaid collects.
- `pack_cap` 12 packs credit 60 minutes; a further pack is `pack_cap_exceeded`; 13 packs is invalid.
- `price_locked` pack is 5 minutes for $1.00 (`price_cents=100`), Stripe TEST, purchase open.
- `stripe_test` `sk_live_` is refused; signed `POST /billing/metered-pack` credits 5 minutes in TEST.

## How to get to it (user POV)

- Run `call-agent verify-minutes` in a terminal.
- CI runs `call-agent verify-minutes`.
- `pytest tests/test_verify_minutes.py` calls the same function.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. This verb creates its own temporary workspace and a throwaway `ACCOUNT_SYNC_SECRET`. Leave `NEON_DATABASE_URL` unset. Stripe live keys must be unset.

- **CI command.** Run `.venv/bin/call-agent verify-minutes`. Exit code `0`. Stdout has exactly six lines that start with `[PASS]`, in this order: `cap_blocks`, `included_allows`, `prepaid_unlocks`, `pack_cap`, `price_locked`, `stripe_test`. The last line is `6/6 passed`.
- **Proof.** Save stdout as `verify-minutes.txt` in the evidence directory.

## Gotchas

- This lever never hits Fly or Stripe live. `POST /billing/metered-pack` is an intended Fly route; this verb proves the in-process handler.
- Cap locked $1.00 / 5 minutes, max 12 packs. `METERED_PACK_PRICE_CENTS=0` would fail `price_locked` / purchase.
- This skill never sets `NEON_DATABASE_URL`, never `fly deploy`, and never dials.
