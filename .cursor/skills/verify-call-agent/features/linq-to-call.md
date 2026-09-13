# Linq to call (closed alpha)

Closed alpha testers text Treena on iMessage. A natural book ask on that chat collects party and time, then the restaurant phone number, then confirm/approve, then kicks off the same general `run_call` path FastAPI already uses for CLI book and quick-ask. If time is missing, Treena asks for time (or party+time) before the phone number. Caller name, kickoff `user_id`, and Telnyx from-DID come from the `users` row for the inbound phone, not from the chat. Name is collected on website `/start` and is in settings after signup. The Worker forwards Linq POST to Fly. Collect and confirm reply 200. Approve returns 202, texts `Okay, I'm going to call them now.`, places the call, hangs up, and texts the result on the same Linq chat. The first message never dials. It does not pause the live call for mid-call chat. Cap recorded `live-prove: PASS (#34)`. qa owns live-prove sign-off (TRE-19). This skill does not redial.

## Sub-features

- `parse-book` Natural book intent with party and time, and no restaurant number, becomes a pending collect. A complete first message `book <+E164> party <n> at <time>` asks to confirm and does not become a `BookRequest`.
- `collect-time` A reservation without time (including `at <restaurant>`) asks for time, or party+time together. "2 people" does not unlock the phone ask. Time is a clock / am/pm / noon span, not a place name.
- `parse-ask` `ask <+E164> hours|stock <item>|aisle <item>` still collects confirm before a `QuickAskRequest`. A leftover `name` token is ignored.
- `collect-phone` Treena texts `okay, sounds good. What's the restaurant's phone number?` and does not dial. The next text on that chat supplies the E164, then Treena asks to confirm.
- `confirm-before-dial` After the number, Treena texts `Okay, I'm going to call this number. Do you confirm? Reply "approve"`. User must say confirm / approve / yes. Then Treena texts `Okay, I'm going to call them now.` and dials. A first message that already has +E164 never dials.
- `parse-fail` A phone follow-up that is not a number, or a confirm follow-up that is not an affirmative, texts a plain intake line. No `Call failed:` / `Call not placed:` prefix. No dial.
- `account-lookup` Inbound phone must match a `users` row. The call uses that row's first name, `user_id`, and `from_did`.
- `account-gate` No row, a row with no first name, or a row with no `from_did` texts a plain line. Never `Call failed: missing caller name`. Never `ledger.provision`.
- `from-did` Confirm/dial uses `users.from_did` as `DialSpec.from_did`. `run_call` takes a required `from_did` kwarg. `KickoffSession.from_did` stays the Linq iMessage line.
- `no-number-order` Confirm/dial never calls `ledger.provision`, `search_and_order_number`, or `POST /v2/number_orders`.
- `echo-else` Any other inbound text stays on `Received: {text}`.
- `async-b` HMAC verify, parse, event-id dedupe, then 202 `{ok, kicked_off, event_id}` on the approve follow-up. `BackgroundTasks` runs `run_kickoff` in this process.
- `same-chat` Collect, confirm, ack, and result go to `POST /chats/{chat_id}/messages`.
- `cross-link` `linq/<event_id>/` points at `call_control_id`; the call run dir has `linq.json`.
- `no-mid-call` `KickoffSession.ask` is reserved. Clarification needed texts a plain intake line.

## How to get to it (user POV)

- Have an account (website checkout, or `call-agent account upsert --phone <your iMessage> --name Nathan --from-did <Treena DID>`) with a first name and assigned Treena number.
- Text Treena `can you make a reservation with this restaurant at 7:30 for a party of two?`.
- If she asks for time (or party and time), reply with those before the restaurant number.
- Reply with the restaurant phone number when she asks.
- Reply `approve` (or confirm / yes) when she asks to confirm.
- Wait for `Okay, I'm going to call them now.`, then the result on the same chat.
- Prove the stub path with `call-agent verify-linq-call`. Do not dial live from this skill.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. Stub mode needs no Telnyx, OpenAI, or Linq keys.

- **Stub lever.** Run `.venv/bin/call-agent verify-linq-call`. Exit code `0`. Fourteen `[PASS]` lines and `14/14 passed`.
- **Echo still works.** Run `.venv/bin/call-agent verify-message`. The `inbound_reply` line is `[PASS]`.
- **Proof.** Save stdout as `verify-linq-call.txt` in the evidence directory.

## Gotchas

- This lever never hits Telnyx or Linq live. Cap recorded `live-prove: PASS (#34)`. qa owns live-prove sign-off (TRE-19). This skill does not redial.
- `PUBLIC_BASE_URL` and `PUBLIC_WEBHOOK_URL` on Fly must both be `https://hidden-beacon-080.fly.dev`. The Worker does not terminate media.
- One Fly machine: `BOOK` is process-local. Do not scale this app until that is no longer true.
- Confirm/dial never provisions a DID. A missing `users.from_did` texts `NO_FROM_DID_TEXT` and does not buy a number.
- Live kickoff needs a `users` row on Fly's Neon for the tester's iMessage phone, with `from_did` set. Seed with `account upsert --from-did` until call-agent-web POSTs `/accounts/sync`.
- `KickoffSession.from_did` is the Linq iMessage line. Telnyx caller ID is `KickoffParsed.from_did` / `run_call(from_did=...)`.
- Mid-call iMessage is not shipped. Do not treat a missing pause/resume as a failed L1 kickoff.
- Pending collect and confirm live at `linq/pending/<chat_id>.json` under artifacts. It is not the live-call pause/resume seam.
- Do not collect a name in iMessage. Name is collected on website `/start` and is in settings after signup. Do not set `LINQ_KICKOFF_CALLER` or `LINQ_KICKOFF_USER_ID`.
