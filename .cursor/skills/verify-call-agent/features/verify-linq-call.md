# Verify linq-to-call

`verify-linq-call` is the stub lever for iMessage natural two-step book → collect phone → `run_call` → Linq result reply with artifact cross-links. It needs no Telnyx, OpenAI, or Linq keys and opens no sockets. It exits `0` only when all eight checks pass.

## Sub-features

- `verify-pass` prints eight `[PASS]` lines and `8/8 passed` in stub mode.
- `verify-exit` exits `0` on pass and `1` on any failure.
- `parse-book` the natural reservation fixture collects party and time and asks for the restaurant phone number. No dial.
- `collect-phone` the +E164 follow-up becomes a `BookRequest`.
- `parse-ask` `ask +14155552671 hours name Nathan` becomes a hours `QuickAskRequest`.
- `parse-fail` a non-number follow-up while waiting for the phone texts `Call failed:`, writes no `kickoff.json`.
- `kickoff-reply` two `POST /chats/{chat_id}/messages` sends: ack then `Booked:`.
- `call-ran` stub call is `booked`, hangup path trail, `hangup.json` present. This lever retains artifacts so it can read them.
- `cross-link` `linq/<event_id>/call.json` and `<ccid>/linq.json` point at each other.
- `same-host` `dial.json` webhook host equals stream host (`stub.invalid`).

## How to get to it (user POV)

- Run `call-agent verify-linq-call` in a terminal.
- CI runs `call-agent verify-linq-call`.
- `pytest tests/test_kickoff.py` and `tests/test_cli.py` call the same functions.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. This verb opens no sockets. It writes under a temp artifacts dir, not the process `CALL_AGENT_ARTIFACTS_DIR`, unless you pass a workspace through pytest.

- **CI command.** Run `.venv/bin/call-agent verify-linq-call`. Exit code `0`. Stdout has exactly eight lines that start with `[PASS]`, in this order: `parse_book`, `collect_phone`, `parse_quick_ask`, `parse_fail_loud`, `kickoff_reply`, `call_ran`, `cross_link`, `same_host_dial`. The last line is `8/8 passed`.
- **Defaults.** Run `.venv/bin/call-agent verify-linq-call`. Same eight lines, exit code `0`.
- **Proof.** Save stdout as `verify-linq-call.txt` in the evidence directory.

## Gotchas

- This lever never sets `QUICK_ASK_LIVE=1` and never POSTs to Fly.
- Stub Telnyx always uses `call_control_id` `stub-call-0001`. Two stub kickoffs in one artifacts dir reuse that run dir.
- This lever sets `retain_call_artifacts=True` so `call_ran` and `same_host_dial` can read hangup and dial files. Default CLI `book`/`quick-ask` wipe those files.
- Echo-only inbound is `verify-message`, not this verb.
