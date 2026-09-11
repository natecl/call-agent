# call-agent

A phone agent that calls a business on your behalf, asks one quick question, and writes down the answer. You paste an E.164 number, pick `hours`, `stock`, or `aisle`, and give your first name. The agent dials from a Telnyx number that belongs to you, opens with "I'm calling on behalf of Nathan", gets the answer, hangs up, and leaves a result, a transcript, and the call audio on disk.

The stack is Python 3.12, Pydantic v2, Typer, FastAPI (two routes), Telnyx Call Control with media streaming, an app-owned GPT-Live bridge that follows [Telnyx's outbound GPT-Live recipe](https://telnyx.com/resources/outbound-ai-calls-python-openai-live), and a SQLite ledger of numbers. The design and its rationale are in [docs/design.md](docs/design.md).

Day 1 ships quick-ask only. Booking, hold queues, packaging, and iMessage are out of scope.

## Run the stub verification

Stub mode fakes the three network boundaries (Telnyx HTTP, Telnyx media WebSocket, OpenAI GPT-Live WebSocket) with raw JSON fixtures that go through the same parsers live mode uses. It needs no keys and opens no sockets. CI runs it on every push.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
call-agent verify-quick-ask --to +15555550123 --ask hours --name Nathan --mode stub
```

You should see five `[PASS]` lines and `5/5 passed`, and the command exits 0. The five checks are:

1. `did_in_ledger`. The user has exactly one number in the ledger, and provisioning twice returns the same number.
2. `dial_payload`. The Call Control body has `from` set to that number, `to` set to the destination, a `wss://` `stream_url`, `stream_bidirectional_mode: rtp`, and `stream_bidirectional_target_legs: self`.
3. `disclosure_first`. The GPT-Live `session.start` instructions name the caller ("on behalf of Nathan") before the question, and the agent's first transcript turn does too.
4. `clean_hangup`. The verdict comes from the transcript, the hangup follows the verdict, and Telnyx confirms with `call.hangup`.
5. `result_artifact`. `result.json` is a valid `QuickAskResult`, and its `transcript_ref` and `audio_ref` point at files that exist.

To run the test suite:

```bash
pytest -q
```

## Use the CLI in stub mode

```bash
call-agent did provision --user default
call-agent did list
call-agent quick-ask --to +14155552671 --ask hours --name Nathan
call-agent quick-ask --to +14155552671 --ask stock --item "oat milk" --name Nathan
```

`quick-ask` prints one JSON object with `status` (`answered`, `declined`, `no_answer`, or `failed`), `answer_text`, `call_control_id`, `transcript_ref`, and `audio_ref`. The refs are relative to `CALL_AGENT_ARTIFACTS_DIR`. Each call writes one directory there with `dial.json`, `session_start.json`, `events.jsonl`, `transcript.json`, `audio/inbound.wav`, `audio/outbound.wav`, `hangup.json`, and `result.json`.

## Enable live mode later

Live mode places a real call. It is gated so that no test or CI run can reach the network by accident.

1. Copy `.env.example` to `.env` and fill in `TELNYX_API_KEY`, `TELNYX_CONNECTION_ID`, and `OPENAI_API_KEY`. Keep `.env` out of git.
2. Start a public tunnel to `CALL_AGENT_PORT` (default 8000), for example `ngrok http 8000`, and put its `https://` URL in `PUBLIC_BASE_URL`. Telnyx posts webhooks to `PUBLIC_BASE_URL/webhooks/telnyx` and opens the media socket at the same host on `/media-stream`. In the Telnyx portal, set the Call Control application's webhook URL to the same `/webhooks/telnyx` address.
3. Set `QUICK_ASK_LIVE=1`.
4. Provision a real number once with `call-agent did provision --user default --live`. The Numbers API orders a voice-capable US number and records it in the ledger. Without `--live` the verb records a stub number.
5. Run `call-agent quick-ask --to +1... --ask hours --name Nathan --live`. The command hosts the two FastAPI routes in the same process, dials, bridges audio between Telnyx and GPT-Live, and prints the result when the call ends.

`call-agent verify-quick-ask --mode live` runs the same five checks against a real call. Only call numbers you control until you have reviewed consent, calling-hour, and AI-disclosure rules for your destination.

The live path is written to the recipe but has not been exercised against Telnyx or OpenAI from this repository. The three things it needs that this repository cannot supply are the Telnyx keys, the OpenAI key with GPT-Live access, and a public tunnel URL.

## Model routing for agents

`.cursor/rules/pstack-models.mdc` maps pstack roles to the model slugs available in Cursor cloud agents. Re-run `/setup-pstack` to change a role.
