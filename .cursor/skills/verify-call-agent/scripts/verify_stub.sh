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

mkdir -p "$SCRATCH" "$EVIDENCE"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT

step() { printf '\n== %s ==\n' "$1" | tee -a "$EVIDENCE/transcript.txt"; }
run() { printf '$ %s\n' "$*" | tee -a "$EVIDENCE/transcript.txt"; "$@" 2>&1 | tee -a "$EVIDENCE/transcript.txt"; return "${PIPESTATUS[0]}"; }

step "doctor"
[ -x "$CLI" ] || { echo "call-agent is not installed in .venv. Run: $REPO/.venv/bin/pip install -e '.[dev]'"; exit 1; }
run "$PY" -c "import call_agent; print('import ok')"
run "$CLI" --help | grep -q "verify-quick-ask" || { echo "verify-quick-ask verb missing from --help"; exit 1; }
run "$CLI" --help | grep -q "verify-message" || { echo "verify-message verb missing from --help"; exit 1; }

step "did provision twice returns the same DID"
FIRST="$(run "$CLI" did provision --user default | tail -n1)"
SECOND="$(run "$CLI" did provision --user default | tail -n1)"
[ "$FIRST" = "$SECOND" ] || { echo "provision is not idempotent: $FIRST vs $SECOND"; exit 1; }
run "$CLI" did list

step "quick-ask stub writes a result and artifacts"
RESULT_JSON="$(run "$CLI" quick-ask --to +14155552671 --ask hours --name Nathan | tail -n1)"
printf '%s\n' "$RESULT_JSON" > "$EVIDENCE/quick-ask-result.json"
"$PY" - "$RESULT_JSON" "$CALL_AGENT_ARTIFACTS_DIR" <<'EOF'
import json, sys, pathlib
result = json.loads(sys.argv[1]); root = pathlib.Path(sys.argv[2])
assert result["status"] == "answered", result
assert result["answer_text"], result
for key in ("transcript_ref", "audio_ref"):
    assert (root / result[key]).is_file(), f"{key} missing: {root / result[key]}"
print("result ok:", result["status"], "|", result["answer_text"])
EOF
cp -r "$CALL_AGENT_ARTIFACTS_DIR" "$EVIDENCE/quick-ask-artifacts"

step "verify-quick-ask stub checklist"
run "$CLI" verify-quick-ask --to +15555550123 --ask hours --name Nathan --mode stub | tee "$EVIDENCE/verify-quick-ask.txt" >/dev/null
grep -q "5/5 passed" "$EVIDENCE/verify-quick-ask.txt"

step "verify-message stub checklist"
run "$CLI" verify-message | tee "$EVIDENCE/verify-message.txt" >/dev/null
grep -q "5/5 passed" "$EVIDENCE/verify-message.txt"

step "linq register-webhook fails loud without token"
set +e
run "$CLI" linq register-webhook
REGISTER_EXIT=$?
set -e
[ "$REGISTER_EXIT" -eq 2 ] || { echo "expected exit 2 for register-webhook without token, got $REGISTER_EXIT"; exit 1; }

step "live mode is gated"
set +e
run "$CLI" quick-ask --to +14155552671 --ask hours --name Nathan --live
GATE_EXIT=$?
set -e
[ "$GATE_EXIT" -eq 2 ] || { echo "expected exit 2 for gated live mode, got $GATE_EXIT"; exit 1; }

step "done"
echo "evidence: $EVIDENCE" | tee -a "$EVIDENCE/transcript.txt"
