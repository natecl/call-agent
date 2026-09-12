# Linq to call (closed alpha)

Closed alpha testers text Treena on iMessage. A `book` or `ask` line on that chat kicks off the same general `run_call` path FastAPI already uses for CLI book and quick-ask. The Worker forwards Linq POST to Fly. This process returns 202, places the call, hangs up, and texts the result on the same Linq chat. It does not pause the live call for mid-call chat. qa owns live prove.

## Sub-features

- `parse-book` `book <+E164> party <n> at <time> [name <First>] [notes <text>]` becomes a `BookRequest`.
- `parse-ask` `ask <+E164> hours|stock <item>|aisle <item> [name <First>]` becomes a `QuickAskRequest`.
- `parse-fail` Ambiguous or incomplete kickoff text gets a usage reply. No dial.
- `echo-else` Any other inbound text stays on `Received: {text}`.
- `async-b` HMAC verify, parse, event-id dedupe, then 202 `{ok, kicked_off, event_id}`. `BackgroundTasks` runs `run_kickoff` in this process.
- `same-chat` Ack and result go to `POST /chats/{chat_id}/messages`.
- `cross-link` `linq/<event_id>/` points at `call_control_id`; the call run dir has `linq.json`.
- `no-mid-call` `KickoffSession.ask` is reserved. Clarification needed texts `Call failed:`.

## How to get to it (user POV)

- Text Treena a book line such as `book +14155552671 party 2 at Friday 7pm name Nathan notes outdoor if possible`.
- Text an ask line such as `ask +14155552671 hours name Nathan`.
- Wait for the calling ack, then the result on the same chat.
- Prove the stub path with `call-agent verify-linq-call`. Do not dial live from this skill.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. Stub mode needs no Telnyx, OpenAI, or Linq keys.

- **Stub lever.** Run `.venv/bin/call-agent verify-linq-call`. Exit code `0`. Seven `[PASS]` lines and `7/7 passed`.
- **Echo still works.** Run `.venv/bin/call-agent verify-message`. The `inbound_reply` line is `[PASS]`.
- **Proof.** Save stdout as `verify-linq-call.txt` in the evidence directory.

## Gotchas

- This lever never hits Telnyx or Linq live. qa owns the Fly prove.
- `PUBLIC_BASE_URL` and `PUBLIC_WEBHOOK_URL` on Fly must both be `https://hidden-beacon-080.fly.dev`. The Worker does not terminate media.
- One Fly machine: `BOOK` is process-local. Do not scale this app until that is no longer true.
- A first live kickoff for user `default` will provision a DID if the Fly ledger has no row.
- Mid-call iMessage is not shipped. Do not treat a missing pause/resume as a failed L1 kickoff.
