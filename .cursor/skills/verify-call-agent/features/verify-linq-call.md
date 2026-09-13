# Verify linq-to-call

`verify-linq-call` is the stub lever for iMessage natural book → collect phone → confirm/approve → `run_call` → Linq result reply with artifact cross-links. It seeds a `users` row for the inbound fixture phone, including `from_did=+15555550177`. It needs no Telnyx, OpenAI, or Linq keys and opens no sockets. It exits `0` only when all thirteen checks pass. It fails if dial happens before confirm.

## Sub-features

- `verify-pass` prints thirteen `[PASS]` lines and `13/13 passed` in stub mode.
- `verify-exit` exits `0` on pass and `1` on any failure.
- `parse-book` the natural reservation fixture collects party and time and asks for the restaurant phone number. No dial.
- `collect-phone` the +E164 follow-up asks to confirm and does not become a `BookRequest`. No dial.
- `confirm-before-dial` `approve` / `yes` / `I confirm` become a `BookRequest`. A complete first-message one-shot never dials. A non-affirmative while waiting for confirm texts `Call failed:`.
- `parse-ask` `ask +14155552671 hours name Nathan` asks to confirm, then `approve` becomes a hours `QuickAskRequest`. The `name` token is ignored; caller comes from the `users` row.
- `parse-fail` a non-number follow-up while waiting for the phone texts `Call failed:`, writes no `kickoff.json`.
- `account-lookup` approve builds `BookRequest.caller` and `user_id` from the seeded `users` row. `kickoff.json` records `account.user_id` and `from_did`.
- `account-gate` no row, a row with no first name, and a row with no `from_did` reply plain lines. Never `Call failed: missing caller name`. No dial.
- `kickoff-reply` two `POST /chats/{chat_id}/messages` sends: `Okay, I'm going to call them now.` then `Booked:`.
- `call-ran` stub call is `booked`, hangup path tool, `hangup.json` present. This lever retains artifacts so it can read them.
- `cross-link` `linq/<event_id>/call.json` and `<ccid>/linq.json` point at each other.
- `same-host` `dial.json` webhook host equals stream host (`stub.invalid`).
- `from-did-users-row` `dial.json` `from` is the seeded `users.from_did` (`+15555550177`), not the inbound personal phone and not the Linq line.
- `no-number-order` confirm/dial source has no `provision(`, `search_and_order_number`, `number_orders`, or `open_ledger`. Stub `dids` stays empty.

## How to get to it (user POV)

- Run `call-agent verify-linq-call` in a terminal.
- CI runs `call-agent verify-linq-call`.
- `pytest tests/test_kickoff.py` and `tests/test_cli.py` call the same functions.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. This verb opens no sockets. It writes under a temp artifacts dir, not the process `CALL_AGENT_ARTIFACTS_DIR`, unless you pass a workspace through pytest.

- **CI command.** Run `.venv/bin/call-agent verify-linq-call`. Exit code `0`. Stdout has exactly thirteen lines that start with `[PASS]`, in this order: `parse_book`, `collect_phone`, `confirm_before_dial`, `parse_quick_ask`, `parse_fail_loud`, `account_lookup`, `account_gate`, `kickoff_reply`, `call_ran`, `cross_link`, `same_host_dial`, `from_did_users_row`, `no_number_order`. The last line is `13/13 passed`.
- **Defaults.** Run `.venv/bin/call-agent verify-linq-call`. Same thirteen lines, exit code `0`.
- **Proof.** Save stdout as `verify-linq-call.txt` in the evidence directory.

## Gotchas

- This lever never sets `QUICK_ASK_LIVE=1` and never POSTs to Fly.
- Stub Telnyx always uses `call_control_id` `stub-call-0001`. Two stub kickoffs in one artifacts dir reuse that run dir.
- This lever sets `retain_call_artifacts=True` so `call_ran` and `same_host_dial` can read hangup and dial files. Default CLI `book`/`quick-ask` wipe those files. The seeded account's retain flag is off, so the `BookRequest` flag is false; the verify Settings still retain.
- Echo-only inbound is `verify-message`, not this verb.
- Seeded stub `from_did` is `+15555550177`. The Linq line fixture is `+15555550100`. The inbound sender is `+14155552671`. Those three must stay distinct.
