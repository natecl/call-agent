# Verify quick-ask

`verify-quick-ask` is the milestone-1 lever. It provisions a number twice for a throwaway user, runs one stub call into a fresh temporary workspace, then reads the ledger and the call's artifacts back and prints five `[PASS]` or `[FAIL]` lines. It exits `0` only when all five pass. CI runs it on every push with no keys.

## Sub-features

- `verify-pass` prints five `[PASS]` lines and `5/5 passed` in stub mode.
- `verify-exit` exits `0` on pass and `1` on any failure.
- `verify-live-gate` refuses `--mode live` unless `QUICK_ASK_LIVE=1` and keys are present.
- `verify-inputs` accepts `--to`, `--ask`, `--question`, `--name`, `--from-did`, `--user`, and `--mode`.

## How to get to it (user POV)

- Run `call-agent verify-quick-ask` in a terminal, with or without the input flags.
- CI runs `call-agent verify-quick-ask --to +15555550123 --ask hours --name Nathan --mode stub`.
- `pytest tests/test_verify_quick_ask.py` calls the same function.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. This verb creates its own temporary workspace, so the ledger and artifacts env values only need to be set, not empty.

- **CI command.** Run `.venv/bin/call-agent verify-quick-ask --to +15555550123 --ask hours --name Nathan --mode stub`. Exit code `0`. Stdout has exactly five lines that start with `[PASS]`, in this order: `did_in_ledger`, `dial_payload`, `disclosure_first`, `clean_hangup`, `result_artifact`. The last line is `5/5 passed`.
- **Defaults.** Run `.venv/bin/call-agent verify-quick-ask`. Same five lines, exit code `0`.
- **Live gate.** Run `.venv/bin/call-agent verify-quick-ask --mode live`. Exit code `2`. Stderr says live mode is gated.
- **Proof.** Save stdout as `verify-quick-ask.txt` in the evidence directory. The `result_artifact` line names the temporary workspace path the run used.

## Gotchas

- The verb makes its own temporary workspace. It never reads artifacts from an earlier run, so a stale pass is not possible. Do not point it at a shared directory to "reuse" a call.
- `disclosure_first` requires both the disclosure and the question text to be present before it compares their positions. A fake that skips the disclosure fails this check. If it passes when you expected a failure, the fake did not skip the line.
- The `pytest` path and the CLI path call the same function. A green pytest with a red CLI, or the reverse, means the CLI wiring changed, not the checks.
- CLI `--mode live` still does not call `serve_in_process`. Laptop live calls use `quick-ask --live` / `book --live`, which need a tunnel to the local BOOK process.
