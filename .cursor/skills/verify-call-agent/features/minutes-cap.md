# Minutes cap (prepaid packs)

When included minutes hit 0, Treena does not auto-continue on metered overage. The user must buy prepaid packs in 5-minute increments ($1.00 per pack, Cap locked) before more calls. Wallet cap is 12 packs / 60 prepaid minutes. Stripe stays TEST. Kickoff and live `run_call` refuse to start without remaining included minutes or prepaid pack balance.

## Sub-features

- `cap-blocks` included remaining 0 and prepaid 0 texts a plain buy-a-pack line. No dial.
- `included-allows` remaining included minutes still collect and confirm as before.
- `prepaid-unlocks` exhausted included + prepaid 5 minutes collects. No auto-meter.
- `pack-price` one pack is 5 connected minutes for $1.00 (`price_cents=100`). Stripe TEST only. `sk_live_` is refused.
- `pack-cap` at most 12 packs / 60 prepaid minutes. A 13th pack is refused.
- `consume` hangup consumes included remaining first, then prepaid, and never bills leftover as overage.
- `live-gate` `run_call` looks up the `users` row and refuses before dial when remaining is 0.

## How to get to it (user POV)

- Have a `users` row (`call-agent account upsert --phone <iMessage> --name Nathan --minutes 30`).
- Use all included minutes. Treena texts `Your included minutes are used up. Buy a 5-minute pack in your account settings, then text me again.`
- Buy a pack from account settings (website Stripe TEST) or `call-agent account credit-pack --phone <iMessage> --packs 1`.
- Text Treena again. The call can start.
- Prove the stub path with `call-agent verify-minutes`. Do not dial live from this skill.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. Stub mode needs no Telnyx, OpenAI, Linq, or live Stripe keys.

- **Stub lever.** Run `.venv/bin/call-agent verify-minutes`. Exit code `0`. Six `[PASS]` lines and `6/6 passed`.
- **Zero minutes blocks.** Run `.venv/bin/call-agent account upsert --phone +14155552671 --name Nathan --minutes 0 --user acctnathan`. Then prove intake with `verify-minutes` `cap_blocks`, or a kickoff with that row. No `stub-call-0001`.
- **Credit one pack.** Run `.venv/bin/call-agent account credit-pack --phone +14155552671 --packs 1`. Exit `0`. JSON `minutes_added` is `5`, `price_cents` is `100`, `stripe_mode` is `test`.
- **Proof.** Save `verify-minutes` stdout as `verify-minutes.txt`.

## Gotchas

- Cap locked 2026-09-14: **$1.00 per 5 connected minutes**, max **12 packs / 60 prepaid minutes**. `METERED_PACK_PRICE_CENTS=0` is an ops kill switch that blocks purchase. Default is `100`.
- Stripe stays TEST. `sk_live_` is refused. This tree does not charge Stripe; website checkout POSTs `/billing/metered-pack` after TEST payment (or CLI `credit-pack` in stub).
- CLI stub `quick-ask` / `book` without a `users` row still run (ops). Kickoff and `run_call` with a `users` row at remaining 0 do not dial.
- `minutes_prepaid` is omit-preserve on `/accounts/sync`. Explicit `0` writes 0.
- Do not `fly deploy`. Do not dial. Do not flip Stripe live.
