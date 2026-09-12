# Book a table

`book` places one call, books a restaurant table on the caller's behalf, and prints one JSON result. The agent reasons about party, time, and constraints. It does not read a fixed question list. In stub mode the three network boundaries are faked with raw JSON fixtures, the restaurant walks those three facts, and the call ends booked or declined. Every call writes the same artifacts directory as `quick-ask`.

## Sub-features

- `book-stub` books a table for the given party, time, and notes.
- `book-missing-party` refuses a run with no `--party`.
- `book-missing-time` refuses a run with no `--time`.
- `book-artifacts` writes the per-call directory that `transcript_ref` and `audio_ref` point into.
- `book-live-gate` refuses `--live` unless `QUICK_ASK_LIVE=1` and keys are present.

## How to get to it (user POV)

- Run `call-agent book --to <E.164> --name <first name> --party <n> --time <text>` in a terminal.
- Add `--notes <text>` for constraints.
- Add `--live` to place a real call (gated). Do not use `--live` in this skill.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`.
- `CALL_AGENT_ARTIFACTS_DIR` is empty.

- **Book.** Run `.venv/bin/call-agent book --to +14155552671 --name Nathan --party 2 --time "Friday 7pm" --notes "outdoor if possible"`. Exit code `0`. The last stdout line is a JSON object with `"status": "booked"`, a `confirmation_text` that contains `booked`, a `call_control_id`, a `transcript_ref` ending in `transcript.json`, and an `audio_ref` ending in `audio/inbound.wav`.
- **Missing party.** Run `.venv/bin/call-agent book --to +14155552671 --name Nathan --time "Friday 7pm"`. Exit code `2` and stderr names `--party`.
- **Missing time.** Run `.venv/bin/call-agent book --to +14155552671 --name Nathan --party 2`. Exit code `2` and stderr names `--time`.
- **Artifacts.** Resolve `transcript_ref` and `audio_ref` against `CALL_AGENT_ARTIFACTS_DIR`. Both files exist. `transcript.json` has a first turn with `"speaker": "agent"` whose text contains `on behalf of Nathan`, at least three agent turns, at least two business turns, and business text that mentions `Friday 7pm`.
- **Live gate.** Run `.venv/bin/call-agent book --to +14155552671 --name Nathan --party 2 --time "Friday 7pm" --live`. Exit code `2`. Stderr says live mode is gated and names `QUICK_ASK_LIVE=1`.
- **Proof.** Save the result JSON line as `book-result.json` and copy the call's artifacts directory into the evidence directory.

## Gotchas

- Only the last stdout line is the result JSON.
- `from` in `dial.json` is the user's ledger number. A dirty ledger shows a different number than `+15555550100`.
- A one-shot stub is a broken book path. `verify-book` fails `multi_turn_booking` on that stub.
- The later live target is `+17272701288`. This skill never dials it.
- CLI `book --live` hosts CallHandle in this process. `PUBLIC_BASE_URL` must be a tunnel to `CALL_AGENT_PORT` on the laptop. Fly `https://hidden-beacon-080.fly.dev` is a different process with no BOOK entry and is not valid for Mac CLI live. The verb probes `/health` for `serve_token` before it dials.
