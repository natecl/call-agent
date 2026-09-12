# Default wipe at hangup

Each call writes audio, transcript, and call-history files under the run directory while it is in progress. After hangup on the trail/success path, `run_call` deletes those files unless retention is opted in. Crash leftovers are accepted. Login and minute totals are out of scope here. The privacy page is not claimed live on this tree.

## Sub-features

- `wipe-default` deletes the run-dir audio, transcript, and history files when `RETAIN_CALL_ARTIFACTS` is unset or false and the request flag is absent.
- `retain-settings` `RETAIN_CALL_ARTIFACTS=1` keeps the files and publishes the durable subset.
- `retain-request` `QuickAskRequest`/`BookRequest.retain_call_artifacts=True` keeps them even when settings are off. The request flag wins when set.
- `no-privacy-page` FEATURE_MAP keeps the privacy page `not shipped`. qa owns live prove.

## How to get to it (user POV)

- Place a stub `quick-ask` or `book` with baseline env. After the JSON result prints, the run directory no longer holds audio, transcript, or history files.
- Set `RETAIN_CALL_ARTIFACTS=1` (or pass `retain_call_artifacts=True` on the request) to keep those files.
- Account opt-in UI is out of scope on this tree. This climb does not look up web accounts.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`.
- `CALL_AGENT_ARTIFACTS_DIR` is empty.
- `RETAIN_CALL_ARTIFACTS` is unset.

- **Default wipe.** Run `.venv/bin/call-agent quick-ask --to +14155552671 --ask hours --name Nathan`. Exit code `0`. The JSON line still has `transcript_ref` and `audio_ref`. Those paths do not exist under `CALL_AGENT_ARTIFACTS_DIR`.
- **Retain.** Run the same command with `RETAIN_CALL_ARTIFACTS=1`. Exit code `0`. `transcript_ref` and `audio_ref` exist. The run directory also has `hangup.json` and `call_report.json`.
- **Verify levers keep proof files.** Run `.venv/bin/call-agent verify-quick-ask --mode stub`. Exit code `0`. That verb sets `retain_call_artifacts=True` so `result_artifact` can read the files.
- **Proof.** Save the default-wipe JSON line and a listing of the empty-ish run directory. For the retain path, copy the run directory into the evidence directory.

## Gotchas

- Files exist during the call. The delete is after hangup, not a skip-write.
- `verify-quick-ask`, `verify-book`, and `verify-linq-call` retain on purpose so they can prove hangup, dial, and transcript shape.
- Kickoff may still write `linq.json` into the emptied run directory. That is a cross-link, not a recording.
- This skill never sets `--live`. qa owns live prove.
- Do not mark the privacy page shipped because this climb matches default wipe.
