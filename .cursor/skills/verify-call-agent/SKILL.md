---
name: verify-call-agent
description: Drive the call-agent CLI in stub mode the way a user does and keep the proof. Use after any change under call_agent/, workers/webhooks/, or tests/, before opening or updating a PR, or when asked to prove that did provision, quick-ask, verify-quick-ask, book, verify-book, verify-message, verify-webhooks, or verify-persistence still work. Update features/ in the same change that ships or changes a verb.
---

# Verify call-agent

call-agent is a CLI. Its user surface is `did provision`, `did list`, `quick-ask`, `verify-quick-ask`, `book`, `verify-book`, `verify-message`, `verify-webhooks`, `linq register-webhook`, and `verify-persistence`. Stub drives of the call and messaging verbs need no server. Each of those reads env, may touch a SQLite ledger, writes artifacts when a call runs, and exits. `verify-webhooks` is the Worker inbox lever. It needs Node and does not place a Telnyx call. The FastAPI routes exist for live Telnyx and Linq inbound processing. Stub verify-message does not host them. The Worker inbox forwards Linq POST to `APP_BASE_URL/linq/webhook` and returns 501 `missing_app_base_url` when that origin is unset. This skill never sets `NEON_DATABASE_URL` or `R2_*`.

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
unset LINQ_API_TOKEN LINQ_WEBHOOK_SIGNING_SECRET LINQ_WEBHOOK_URL PUBLIC_WEBHOOK_URL
unset NEON_DATABASE_URL R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET R2_ENDPOINT
mkdir -p "/tmp/verify-call-agent-$RUN_ID"
```

Ready means `.venv/bin/call-agent --help` exits 0 and lists `verify-quick-ask`, `verify-book`, `verify-message`, `verify-webhooks`, and `verify-persistence`. Two runs with different `RUN_ID` values can run at the same time.

## Doctor

Run before driving whenever anything looks off. All three must hold.

```bash
.venv/bin/python -c "import call_agent; print('import ok')"
.venv/bin/call-agent --help | grep -E 'verify-quick-ask|verify-book|verify-message|verify-webhooks|verify-persistence'
[ "${QUICK_ASK_LIVE:-0}" = "0" ] && echo "stub mode"
```

If `call-agent` is missing, the package is not installed in this venv. Re-run the install line. If `QUICK_ASK_LIVE` is `1`, stop. This skill never drives live mode.

## Drive

Run the whole path in one command. It launches, doctors, drives every mapped feature, keeps the evidence, and cleans the scratch state:

```bash
.cursor/skills/verify-call-agent/scripts/verify_stub.sh
```

To drive one feature by hand, use its file under `features/`. The commands there are literal. For `quick-ask` pass `--user`, `--to`, `--ask`, and `--name` as written. For `book` pass `--user`, `--to`, `--name`, `--party`, and `--time` as written.

## Evidence

The script writes to `artifacts/verify-call-agent/<RUN_ID>/` in the repo (the `artifacts/` directory is gitignored):

- `transcript.txt`. Every command, its stdout and stderr, in order.
- `quick-ask-result.json`. The JSON line `quick-ask` printed.
- `quick-ask-artifacts/<call_control_id>/`. The call's `dial.json`, `session_start.json`, `events.jsonl`, `transcript.json`, `audio/`, `hangup.json`, and `result.json`, copied from the scratch artifacts dir.
- `verify-quick-ask.txt`. The five-line checklist and `5/5 passed`.
- `book-result.json`. The JSON line `book` printed.
- `book-artifacts/<call_control_id>/`. The booking call's artifacts, copied from the scratch artifacts dir.
- `verify-book.txt`. The six-line checklist and `6/6 passed`.
- `verify-message.txt`. The five-line MessagingPort checklist and `5/5 passed`.
- `verify-webhooks.txt`. The Worker inbox five-line checklist and `5/5 passed`.
- `verify-persistence.txt`. The three-line checklist, `sqlite ledger + local artifacts`, and `3/3 passed`. The partial-R2 refusal is in `transcript.txt`.

Proof standards. Exercise the real CLI verb, not a Python function. Capture the command and the state it produced, so the result JSON and the files it references, not only the exit code. For a mutation such as `did provision`, prove it with a second read (`did list`), and prove idempotency by running it twice. Stub mode is the only mock, and it sits where production already isolates Telnyx and OpenAI. Confirm it opened no sockets by running with the keys unset, which the script does.

## Cleanup

The script removes `/tmp/verify-call-agent-<RUN_ID>` on exit and never touches `artifacts/verify-call-agent/`. If you drove by hand:

```bash
rm -rf "/tmp/verify-call-agent-$RUN_ID"
```

Never delete the evidence directory. Never `pkill` by name. Stub runs leave no processes behind.

## Helpers

`scripts/verify_stub.sh [run-id]` is the full drive. It exits non-zero at the first failed step and prints the evidence path last. Read `transcript.txt` in the evidence directory to see which step failed.
