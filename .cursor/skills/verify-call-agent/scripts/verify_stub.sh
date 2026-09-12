#!/usr/bin/env bash
# Drive the call-agent CLI in stub mode end to end with isolated state and keep the evidence.
# Usage: .cursor/skills/verify-call-agent/scripts/verify_stub.sh [run-id]
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../../../.." && pwd)"
RUN_ID="${1:-$(date +%Y%m%d-%H%M%S)-$$}"
SCRATCH="/tmp/verify-call-agent-$RUN_ID"
EVIDENCE="$REPO/artifacts/verify-call-agent/$RUN_ID"
PY="$REPO/.venv/bin/python"
CLI="$REPO/.venv/bin/call-agent"

export CALL_AGENT_DB_PATH="$SCRATCH/ledger.sqlite3"
export CALL_AGENT_ARTIFACTS_DIR="$SCRATCH/artifacts"
export QUICK_ASK_LIVE=0
unset TELNYX_API_KEY TELNYX_CONNECTION_ID OPENAI_API_KEY PUBLIC_BASE_URL
unset LINQ_API_TOKEN LINQ_WEBHOOK_SIGNING_SECRET LINQ_WEBHOOK_URL PUBLIC_WEBHOOK_URL
unset NEON_DATABASE_URL R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET R2_ENDPOINT
unset RETAIN_CALL_ARTIFACTS

mkdir -p "$SCRATCH" "$EVIDENCE"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT

step() { printf '\n== %s ==\n' "$1" | tee -a "$EVIDENCE/transcript.txt"; }
run() { printf '$ %s\n' "$*" | tee -a "$EVIDENCE/transcript.txt"; "$@" 2>&1 | tee -a "$EVIDENCE/transcript.txt"; return "${PIPESTATUS[0]}"; }

step "doctor"
[ -x "$CLI" ] || { echo "call-agent is not installed in .venv. Run: $REPO/.venv/bin/pip install -e '.[dev]'"; exit 1; }
run "$PY" -c "import call_agent; print('import ok')"
run "$CLI" --help | grep -q "verify-quick-ask" || { echo "verify-quick-ask verb missing from --help"; exit 1; }
run "$CLI" --help | grep -q "verify-book" || { echo "verify-book verb missing from --help"; exit 1; }
run "$CLI" --help | grep -q "verify-message" || { echo "verify-message verb missing from --help"; exit 1; }
run "$CLI" --help | grep -q "verify-webhooks" || { echo "verify-webhooks verb missing from --help"; exit 1; }
run "$CLI" --help | grep -q "verify-persistence" || { echo "verify-persistence verb missing from --help"; exit 1; }
run "$CLI" --help | grep -q "verify-linq-call" || { echo "verify-linq-call verb missing from --help"; exit 1; }

step "did provision twice returns the same DID"
FIRST="$(run "$CLI" did provision --user default | tail -n1)"
SECOND="$(run "$CLI" did provision --user default | tail -n1)"
[ "$FIRST" = "$SECOND" ] || { echo "provision is not idempotent: $FIRST vs $SECOND"; exit 1; }
run "$CLI" did list

step "quick-ask stub writes a result then wipes artifacts"
RESULT_JSON="$(run "$CLI" quick-ask --to +14155552671 --ask hours --name Nathan | tail -n1)"
printf '%s\n' "$RESULT_JSON" > "$EVIDENCE/quick-ask-result.json"
"$PY" - "$RESULT_JSON" "$CALL_AGENT_ARTIFACTS_DIR" <<'EOF'
import json, sys, pathlib
result = json.loads(sys.argv[1]); root = pathlib.Path(sys.argv[2])
assert result["status"] == "answered", result
assert result["answer_text"], result
for key in ("transcript_ref", "audio_ref"):
    assert not (root / result[key]).is_file(), f"{key} should be wiped: {root / result[key]}"
print("result ok (wiped):", result["status"], "|", result["answer_text"])
EOF

step "quick-ask retain keeps artifacts"
export RETAIN_CALL_ARTIFACTS=1
RETAIN_JSON="$(run "$CLI" quick-ask --to +14155552671 --ask hours --name Nathan | tail -n1)"
"$PY" - "$RETAIN_JSON" "$CALL_AGENT_ARTIFACTS_DIR" <<'EOF'
import json, sys, pathlib
result = json.loads(sys.argv[1]); root = pathlib.Path(sys.argv[2])
assert result["status"] == "answered", result
for key in ("transcript_ref", "audio_ref"):
    assert (root / result[key]).is_file(), f"{key} missing: {root / result[key]}"
print("result ok (retained):", result["status"])
EOF
cp -r "$CALL_AGENT_ARTIFACTS_DIR" "$EVIDENCE/quick-ask-artifacts"
unset RETAIN_CALL_ARTIFACTS

step "verify-quick-ask stub checklist"
run "$CLI" verify-quick-ask --to +15555550123 --ask hours --name Nathan --mode stub | tee "$EVIDENCE/verify-quick-ask.txt" >/dev/null
grep -q "5/5 passed" "$EVIDENCE/verify-quick-ask.txt"

step "book stub writes a result then wipes artifacts"
BOOK_JSON="$(run "$CLI" book --to +14155552671 --name Nathan --party 2 --time "Friday 7pm" --notes "outdoor if possible" | tail -n1)"
printf '%s\n' "$BOOK_JSON" > "$EVIDENCE/book-result.json"
"$PY" - "$BOOK_JSON" "$CALL_AGENT_ARTIFACTS_DIR" <<'EOF'
import json, sys, pathlib
result = json.loads(sys.argv[1]); root = pathlib.Path(sys.argv[2])
assert result["status"] == "booked", result
assert result["confirmation_text"], result
for key in ("transcript_ref", "audio_ref"):
    assert not (root / result[key]).is_file(), f"{key} should be wiped: {root / result[key]}"
print("book result ok (wiped):", result["status"])
EOF

step "book retain keeps artifacts"
export RETAIN_CALL_ARTIFACTS=1
RETAIN_BOOK="$(run "$CLI" book --to +14155552671 --name Nathan --party 2 --time "Friday 7pm" --notes "outdoor if possible" | tail -n1)"
"$PY" - "$RETAIN_BOOK" "$CALL_AGENT_ARTIFACTS_DIR" <<'EOF'
import json, sys, pathlib
result = json.loads(sys.argv[1]); root = pathlib.Path(sys.argv[2])
assert result["status"] == "booked", result
for key in ("transcript_ref", "audio_ref"):
    assert (root / result[key]).is_file(), f"{key} missing: {root / result[key]}"
print("book result ok (retained):", result["status"])
EOF
cp -r "$CALL_AGENT_ARTIFACTS_DIR" "$EVIDENCE/book-artifacts"
unset RETAIN_CALL_ARTIFACTS

step "verify-book stub checklist"
run "$CLI" verify-book --to +15555550123 --name Nathan --party 2 --time "Friday 7pm" --mode stub | tee "$EVIDENCE/verify-book.txt" >/dev/null
grep -q "6/6 passed" "$EVIDENCE/verify-book.txt"

step "verify-message stub checklist"
run "$CLI" verify-message | tee "$EVIDENCE/verify-message.txt" >/dev/null
grep -q "6/6 passed" "$EVIDENCE/verify-message.txt"

step "verify-linq-call stub checklist"
run "$CLI" verify-linq-call | tee "$EVIDENCE/verify-linq-call.txt" >/dev/null
grep -q "8/8 passed" "$EVIDENCE/verify-linq-call.txt"

step "linq register-webhook fails loud without token"
set +e
run "$CLI" linq register-webhook
REGISTER_EXIT=$?
set -e
[ "$REGISTER_EXIT" -eq 2 ] || { echo "expected exit 2 for register-webhook without token, got $REGISTER_EXIT"; exit 1; }

step "verify-webhooks stub checklist"
run "$CLI" verify-webhooks | tee "$EVIDENCE/verify-webhooks.txt" >/dev/null
grep -q "5/5 passed" "$EVIDENCE/verify-webhooks.txt"

step "verify-persistence stub checklist"
run "$CLI" verify-persistence | tee "$EVIDENCE/verify-persistence.txt" >/dev/null
grep -q "3/3 passed" "$EVIDENCE/verify-persistence.txt"
grep -q "sqlite ledger + local artifacts" "$EVIDENCE/verify-persistence.txt"

step "verify-persistence rejects partial R2"
set +e
R2_ACCOUNT_ID=acct run "$CLI" verify-persistence
PERSIST_EXIT=$?
set -e
[ "$PERSIST_EXIT" -eq 2 ] || { echo "expected exit 2 for partial R2, got $PERSIST_EXIT"; exit 1; }
grep -q "R2 is misconfigured" "$EVIDENCE/transcript.txt"
unset R2_ACCOUNT_ID

step "live mode is gated"
set +e
run "$CLI" quick-ask --to +14155552671 --ask hours --name Nathan --live
GATE_EXIT=$?
set -e
[ "$GATE_EXIT" -eq 2 ] || { echo "expected exit 2 for gated live mode, got $GATE_EXIT"; exit 1; }

step "done"
echo "evidence: $EVIDENCE" | tee -a "$EVIDENCE/transcript.txt"
