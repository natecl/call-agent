---
name: verify-call-agent
description: Drive the call-agent CLI in stub mode the way a user does and keep the proof. Use after any change under call_agent/ or tests/, before opening or updating a PR, or when asked to prove that did provision, quick-ask, verify-quick-ask, or verify-message still work.
---

# Verify call-agent

call-agent is a CLI. Its user surface is `did provision`, `did list`, `quick-ask`, `verify-quick-ask`, `verify-message`, and `linq register-webhook`. There is no server to keep alive in stub mode. Each drive is one process that reads env, may touch a SQLite ledger, writes artifacts when a call runs, and exits. The FastAPI routes exist for live Telnyx and the Linq webhook skeleton. Stub verify-message does not host them.

Read `features/README.md` before driving. It is the maintained map of user paths and proof.

## Launch

Install once per checkout:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

Isolate every run. Point the ledger and the artifacts at a scratch directory that no other run uses, and pin stub mode:

```bash
export RUN_ID="$(date +%Y%m%d-%H%M%S)-$$"
export CALL_AGENT_DB_PATH="/tmp/verify-call-agent-$RUN_ID/ledger.sqlite3"
export CALL_AGENT_ARTIFACTS_DIR="/tmp/verify-call-agent-$RUN_ID/artifacts"
export QUICK_ASK_LIVE=0
unset TELNYX_API_KEY TELNYX_CONNECTION_ID OPENAI_API_KEY PUBLIC_BASE_URL
unset LINQ_API_TOKEN LINQ_WEBHOOK_SECRET LINQ_WEBHOOK_URL PUBLIC_WEBHOOK_URL
mkdir -p "/tmp/verify-call-agent-$RUN_ID"
```

Ready means `.venv/bin/call-agent --help` exits 0 and lists `verify-quick-ask` and `verify-message`. Two runs with different `RUN_ID` values can run at the same time.

## Doctor

Run before driving whenever anything looks off. All three must hold.

```bash
.venv/bin/python -c "import call_agent; print('import ok')"
.venv/bin/call-agent --help | grep verify-quick-ask
.venv/bin/call-agent --help | grep verify-message
[ "${QUICK_ASK_LIVE:-0}" = "0" ] && echo "stub mode"
```

If `call-agent` is missing, the package is not installed in this venv. Re-run the install line. If `QUICK_ASK_LIVE` is `1`, stop. This skill never drives live mode.

## Drive

Run the whole path in one command. It launches, doctors, drives every mapped feature, keeps the evidence, and cleans the scratch state:

```bash
.cursor/skills/verify-call-agent/scripts/verify_stub.sh
```

To drive one feature by hand, use its file under `features/`. The commands there are literal. Always pass `--user`, `--to`, `--ask`, and `--name` as written so the expected output matches.

## Evidence

The script writes to `artifacts/verify-call-agent/<RUN_ID>/` in the repo (the `artifacts/` directory is gitignored):

- `transcript.txt`. Every command, its stdout and stderr, in order.
- `quick-ask-result.json`. The JSON line `quick-ask` printed.
- `quick-ask-artifacts/<call_control_id>/`. The call's `dial.json`, `session_start.json`, `events.jsonl`, `transcript.json`, `audio/`, `hangup.json`, and `result.json`, copied from the scratch artifacts dir.
- `verify-quick-ask.txt`. The five-line checklist and `5/5 passed`.
- `verify-message.txt`. The five-line MessagingPort checklist and `5/5 passed`.

Proof standards. Exercise the real CLI verb, not a Python function. Capture the command and the state it produced, so the result JSON and the files it references, not only the exit code. For a mutation such as `did provision`, prove it with a second read (`did list`), and prove idempotency by running it twice. Stub mode is the only mock, and it sits where production already isolates Telnyx and OpenAI. Confirm it opened no sockets by running with the keys unset, which the script does.

## Cleanup

The script removes `/tmp/verify-call-agent-<RUN_ID>` on exit and never touches `artifacts/verify-call-agent/`. If you drove by hand:

```bash
rm -rf "/tmp/verify-call-agent-$RUN_ID"
```

Never delete the evidence directory. Never `pkill` by name. Stub runs leave no processes behind.

## Helpers

`scripts/verify_stub.sh [run-id]` is the full drive. It exits non-zero at the first failed step and prints the evidence path last. Read `transcript.txt` in the evidence directory to see which step failed.
