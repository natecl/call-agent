# Ask a quick question

`quick-ask` places one call, asks one question on the caller's behalf, and prints one JSON result. In stub mode the three network boundaries are faked with raw JSON fixtures, the business gives a canned answer, and the whole call completes in well under a second with no sockets. Every call writes one artifacts directory with the dial body, the GPT-Live session start, the event journal, the transcript, the audio, the hangup, and the result.

## Sub-features

- `ask-hours` asks for opening hours.
- `ask-stock` asks whether an item is in stock and requires `--item`.
- `ask-aisle` asks which aisle an item is in and requires `--item`.
- `ask-artifacts` writes the per-call directory during the call. After hangup those files are deleted unless `RETAIN_CALL_ARTIFACTS=1`.
- `ask-live-gate` refuses `--live` unless `QUICK_ASK_LIVE=1` and keys are present.

## How to get to it (user POV)

- Run `call-agent quick-ask --to <E.164> --ask hours --name <first name>` in a terminal.
- Add `--item <text>` for `--ask stock` or `--ask aisle`.
- Add `--live` to place a real call (gated).

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`.
- `CALL_AGENT_ARTIFACTS_DIR` is empty.

- **Hours.** Run `.venv/bin/call-agent quick-ask --to +14155552671 --ask hours --name Nathan`. Exit code `0`. The last stdout line is a JSON object with `"status": "answered"`, `"answer_text": "We're open Monday through Friday, 9 AM to 5 PM."`, a `call_control_id`, a `transcript_ref` ending in `transcript.json`, and an `audio_ref` ending in `audio/inbound.wav`.
- **Stock.** Run `.venv/bin/call-agent quick-ask --to +14155552671 --ask stock --item "oat milk" --name Nathan`. Exit code `0`. `answer_text` is `Yes, we have that in stock.`
- **Aisle.** Run `.venv/bin/call-agent quick-ask --to +14155552671 --ask aisle --item "oat milk" --name Nathan`. Exit code `0`. `answer_text` is `That's in aisle 7.`
- **Missing item.** Run `.venv/bin/call-agent quick-ask --to +14155552671 --ask stock --name Nathan`. Exit code `2` and stderr names `--item`.
- **Artifacts (default wipe).** The JSON still names `transcript_ref` and `audio_ref`. Those paths do not exist after hangup. See [privacy-wipe.md](./privacy-wipe.md).
- **Artifacts (retain).** Run the hours command with `RETAIN_CALL_ARTIFACTS=1`. Resolve `transcript_ref` and `audio_ref` against `CALL_AGENT_ARTIFACTS_DIR`. Both files exist. `transcript.json` has a first turn with `"speaker": "agent"` whose text contains `on behalf of Nathan`, and a `business` turn whose text is the `answer_text`. The same directory holds `dial.json` with `"from": "+15555550100"`, `"to": "+14155552671"`, `"stream_bidirectional_mode": "rtp"`, and `"stream_bidirectional_target_legs": "self"`.
- **Live gate.** Run `.venv/bin/call-agent quick-ask --to +14155552671 --ask hours --name Nathan --live`. Exit code `2`. Stderr says live mode is gated and names `QUICK_ASK_LIVE=1`.
- **Proof.** Save the result JSON line as `quick-ask-result.json` and copy the call's artifacts directory into the evidence directory.

## Gotchas

- Only the last stdout line is the result JSON. Earlier lines, if any, are progress.
- `from` in `dial.json` is the user's ledger number. A run with a dirty ledger shows a different number than `+15555550100`.
- `audio/inbound.wav` in stub mode is silence. Its existence and WAV header are the proof, not its content.
- The live gate exits before any network call. A gate test with keys set and `QUICK_ASK_LIVE=1` would place a real call. Never set them in this skill.
- CLI `quick-ask --live` hosts CallHandle in this process. Set both `PUBLIC_BASE_URL` and `PUBLIC_WEBHOOK_URL` to the same tunnel to `CALL_AGENT_PORT` on the laptop for `stream_url` and per-call `webhook_url`. Process env overrides `.env`, so a leftover `PUBLIC_WEBHOOK_URL` can send webhooks to a stale host while media follows `PUBLIC_BASE_URL`. Fly `PUBLIC_BASE_URL` alone is not valid for Mac CLI live. The verb fails before dial if webhook host != stream host after wss/https normalize, or if `{PUBLIC_BASE_URL}/health` is not HTTP 200 with this process's `serve_token`.
- A leftover `R2_ACCOUNT_ID` (or any incomplete `R2_*` set) makes `quick-ask` exit 2 before it dials. Unset the four required `R2_*` vars and `NEON_DATABASE_URL` in the baseline.
