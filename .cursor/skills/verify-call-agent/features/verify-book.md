# Verify book

`verify-book` is the restaurant-book lever. It provisions a number twice for a throwaway user, runs one stub booking call into a fresh temporary workspace, then reads the ledger and the call's artifacts back and prints seven `[PASS]` or `[FAIL]` lines. It exits `0` only when all seven pass. CI runs it on every push with no keys.

## Sub-features

- `verify-book-pass` prints seven `[PASS]` lines and `7/7 passed` in stub mode.
- `verify-book-exit` exits `0` on pass and `1` on any failure.
- `verify-book-theater` fails `multi_turn_booking` when the stub is one ask and one answer. Hangup still follows the closing line (`hangup.path` is `trail`).
- `verify-book-goodbye-hangup` hangs up after `CLOSING_LINE` when the last business turn is `Yes, we can do that` (no booked needle). `clean_hangup` fails because status is `failed`, not because the far party hung up.
- `verify-book-decline` passes when the restaurant clearly declines.
- `verify-book-keeps-talking` requires a client delegation, an in-process reply, more agent speech, trail hangup, and booked or declined. `never_resumes` fails this check with `hangup_path=stall`.
- `verify-book-media-drop` fails `clean_hangup` with `hangup_path=far_party` when the media socket dies before hangup, and still writes `result_artifact`.
- `verify-book-live-gate` refuses `--mode live` unless `QUICK_ASK_LIVE=1` and keys are present.
- `verify-book-inputs` accepts `--to`, `--name`, `--party`, `--time`, `--notes`, `--from-did`, `--user`, and `--mode`.

## How to get to it (user POV)

- Run `call-agent verify-book` in a terminal, with or without the input flags.
- CI runs `call-agent verify-book --to +15555550123 --name Nathan --party 2 --time "Friday 7pm" --mode stub`.
- `pytest tests/test_verify_book.py` calls the same function, including the theater (goodbye hangup), decline, never-resumes, and media-drop faults.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. This verb creates its own temporary workspace, so the ledger and artifacts env values only need to be set, not empty.

- **CI command.** Run `.venv/bin/call-agent verify-book --to +15555550123 --name Nathan --party 2 --time "Friday 7pm" --mode stub`. Exit code `0`. Stdout has exactly seven lines that start with `[PASS]`, in this order: `did_in_ledger`, `dial_payload`, `disclosure_first`, `clean_hangup`, `result_artifact`, `multi_turn_booking`, `keeps_talking`. The last line is `7/7 passed`.
- **Defaults.** Run `.venv/bin/call-agent verify-book`. Same seven lines, exit code `0`.
- **Live gate.** Run `.venv/bin/call-agent verify-book --mode live`. Exit code `2`. Stderr says live mode is gated.
- **Proof.** Save stdout as `verify-book.txt` in the evidence directory. The `result_artifact` line names the temporary workspace path the run used.

## Gotchas

- The verb makes its own temporary workspace. It never reads artifacts from an earlier run.
- This lever retains call artifacts so `result_artifact` can read them. Default `book` wipes those files after hangup.
- `multi_turn_booking` fails when the stub emits one ask and one answer. That is the loud fail for a theater path. The call still hangs up after goodbye (`hangup.path=trail`, `marks_sent=1`).
- `clean_hangup` expects `booked` or `declined`, not `answered`. A goodbye without booked or declined needles fails this check with `status=failed` and `hangup_path=trail`. That is the TRE-14 live miss, not `far_party`.
- `keeps_talking` fails when the model goes quiet after the time turn (`never_resumes`, `hangup_path=stall`). A media socket that dies before hangup fails `clean_hangup` with `far_party` and still leaves `result.json`.
- `pytest` and the CLI call the same function. A green pytest with a red CLI means the CLI wiring changed.
- Live book against `+17272701288` needs `QUICK_ASK_LIVE=1`, Telnyx and OpenAI keys, and both `PUBLIC_BASE_URL` and `PUBLIC_WEBHOOK_URL` set to the same tunnel to the local BOOK process. Fly `PUBLIC_BASE_URL` is not valid for Mac CLI live. This skill never sets them.
