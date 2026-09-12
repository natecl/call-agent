# Linq to call (closed alpha)

Closed alpha testers text Treena on iMessage. A natural book ask on that chat collects party and time, then the restaurant phone number, then confirm/approve, then kicks off the same general `run_call` path FastAPI already uses for CLI book and quick-ask. Caller name comes from the `users` row for the inbound phone, not from the chat. The Worker forwards Linq POST to Fly. Collect and confirm reply 200. Approve returns 202, texts `Okay, I'm going to call them now.`, places the call, hangs up, and texts the result on the same Linq chat. The first message never dials. It does not pause the live call for mid-call chat. qa owns live prove.

## Sub-features

- `parse-book` Natural book intent with party and time, and no restaurant number, becomes a pending collect. A complete first message `book <+E164> party <n> at <time>` asks to confirm and does not become a `BookRequest`.
- `parse-ask` `ask <+E164> hours|stock <item>|aisle <item>` still collects confirm before a `QuickAskRequest`. A leftover `name` token is ignored.
- `collect-phone` Treena texts `okay, sounds good. What's the restaurant's phone number?` and does not dial. The next text on that chat supplies the E164, then Treena asks to confirm.
- `confirm-before-dial` After the number, Treena texts `Okay, I'm going to call this number. Do you confirm? Reply "approve"`. User must say confirm / approve / yes. Then Treena texts `Okay, I'm going to call them now.` and dials. A first message that already has +E164 never dials.
- `parse-fail` A phone follow-up that is not a number, or a confirm follow-up that is not an affirmative, texts `Call failed:`. No dial.
- `account-lookup` Inbound phone must match a `users` row. The call uses that row's first name. DID provision still uses `LINQ_KICKOFF_USER_ID`.
- `account-gate` No row, or a row with no first name, texts a plain account-settings line. Never `Call failed: missing caller name`.
- `echo-else` Any other inbound text stays on `Received: {text}`.
- `async-b` HMAC verify, parse, event-id dedupe, then 202 `{ok, kicked_off, event_id}` on the approve follow-up. `BackgroundTasks` runs `run_kickoff` in this process.
- `same-chat` Collect, confirm, ack, and result go to `POST /chats/{chat_id}/messages`.
- `cross-link` `linq/<event_id>/` points at `call_control_id`; the call run dir has `linq.json`.
- `no-mid-call` `KickoffSession.ask` is reserved. Clarification needed texts `Call failed:`.

## How to get to it (user POV)

- Have an account (website checkout, or `call-agent account upsert --phone <your iMessage> --name Nathan`) with a first name in account settings.
- Text Treena `can you make a reservation with this restaurant at 7:30 for a party of two?`.
- Reply with the restaurant phone number when she asks.
- Reply `approve` (or confirm / yes) when she asks to confirm.
- Wait for `Okay, I'm going to call them now.`, then the result on the same chat.
- Prove the stub path with `call-agent verify-linq-call`. Do not dial live from this skill.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. Stub mode needs no Telnyx, OpenAI, or Linq keys.

- **Stub lever.** Run `.venv/bin/call-agent verify-linq-call`. Exit code `0`. Eleven `[PASS]` lines and `11/11 passed`.
- **Echo still works.** Run `.venv/bin/call-agent verify-message`. The `inbound_reply` line is `[PASS]`.
- **Proof.** Save stdout as `verify-linq-call.txt` in the evidence directory.

## Gotchas

- This lever never hits Telnyx or Linq live. qa owns the Fly prove.
- `PUBLIC_BASE_URL` and `PUBLIC_WEBHOOK_URL` on Fly must both be `https://hidden-beacon-080.fly.dev`. The Worker does not terminate media.
- One Fly machine: `BOOK` is process-local. Do not scale this app until that is no longer true.
- A first live kickoff for user `default` will provision a DID if the Fly ledger has no row.
- Live kickoff needs a `users` row on Fly's Neon for the tester's iMessage phone. Seed with `account upsert` until call-agent-web POSTs `/accounts/sync`.
- Mid-call iMessage is not shipped. Do not treat a missing pause/resume as a failed L1 kickoff.
- Pending collect and confirm live at `linq/pending/<chat_id>.json` under artifacts. It is not the live-call pause/resume seam.
- Do not collect a name in iMessage. Do not set `LINQ_KICKOFF_CALLER`.

