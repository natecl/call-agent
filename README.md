# call-agent

A phone agent that calls a business on your behalf, asks one quick question, and writes down the answer. You paste an E.164 number, pick `hours`, `stock`, or `aisle`, and give your first name. The agent dials from a Telnyx number that belongs to you, opens with "I'm calling on behalf of Nathan", gets the answer, hangs up, and leaves a result, a transcript, and the call audio on disk.

The stack is Python 3.12, Pydantic v2, Typer, FastAPI (two routes), Telnyx Call Control with media streaming, an app-owned GPT-Live bridge that follows [Telnyx's outbound GPT-Live recipe](https://telnyx.com/resources/outbound-ai-calls-python-openai-live), and a DID ledger that defaults to SQLite. Set `NEON_DATABASE_URL` to use Neon. Set the four `R2_*` vars to put finished artifacts on R2. The design and its rationale are in [docs/design.md](docs/design.md). What `main` implements today is listed in [docs/FEATURE_MAP.md](docs/FEATURE_MAP.md).

Day 1 ships quick-ask. Closed alpha includes iMessage inbound through the Linq Worker hook and the `MessagingPort` process path. Live send is gated on `LINQ_API_TOKEN`. That is not a public landing claim. Booking, hold queues, packaging, and SMS are out of scope. Telnyx vs Linq is in [docs/linq.md](docs/linq.md).

## Public webhook URL (Linq, register now)

Closed alpha L1 testers text Treena through Linq. This Worker URL is on that inbound path. Builder can register it now. No Nathan click. SMS stays off.

`https://call-agent-webhooks.blossom-linen.workers.dev/linq/webhook`

`POST` (and `GET`) return `{"ok":true,"provider":"linq","implemented":false}` and do not send SMS. Alias `https://call-agent-webhooks.blossom-linen.workers.dev/webhooks/linq`. Telnyx stays on `https://call-agent-webhooks.blossom-linen.workers.dev/webhooks/telnyx`. The Worker ACKs only. FastAPI `POST /webhooks/linq` is the process path. Forward from Worker to app is a follow-up. Details are in [workers/webhooks/README.md](workers/webhooks/README.md).

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
4. `clean_hangup`. The verdict comes from the transcript, the agent closes GPT-Live and plays out what it still sends, sends exactly one Telnyx mark named `hangup` once GPT-Live confirms the close, Telnyx echoes it, `HANGUP_TRAILING_SILENCE_S` (default 0.4 s) elapses, the hangup follows, and Telnyx confirms with `call.hangup`. A drain that runs into `HANGUP_DRAIN_TIMEOUT_S` (default 10 s), or a call that sends more than one mark, fails this check.
5. `result_artifact`. `result.json` is a valid `QuickAskResult`, its `transcript_ref` and `audio_ref` point at files that exist, and `call_report.json` is a valid `CallReport` whose refs exist.

To run the test suite:

```bash
pytest -q
```

Stub messaging (no Linq token, no public URL):

```bash
call-agent verify-message
```

You should see five `[PASS]` lines and `5/5 passed`. The checks are stub send shape, inbound `message.received` mapping, Standard Webhooks signature accept/reject, live constructor without `LINQ_API_TOKEN`, and SMS ignored. See [docs/linq.md](docs/linq.md).

`call-agent verify-persistence` checks the Neon and R2 ports. With those vars unset it uses SQLite and a local directory, prints three `[PASS]` lines, and exits 0. If some `R2_*` vars are set and others are not, or if `NEON_DATABASE_URL` is not a `postgresql://` URL, it exits 2 and names the problem. CI runs it with the vars unset. It never opens Neon or R2.

## Use the CLI in stub mode

```bash
call-agent did provision --user default
call-agent did list
call-agent quick-ask --to +14155552671 --ask hours --name Nathan
call-agent quick-ask --to +14155552671 --ask stock --item "oat milk" --name Nathan
```

`quick-ask` prints one JSON object with `status` (`answered`, `declined`, `no_answer`, or `failed`), `answer_text`, `call_control_id`, `transcript_ref`, and `audio_ref`. The refs are relative to `CALL_AGENT_ARTIFACTS_DIR`. Each call writes one directory there with `dial.json`, `session_start.json`, `events.jsonl`, `transcript.json`, `audio/inbound.wav`, `audio/outbound.wav`, `hangup.json`, `result.json`, `call_report.json`, and `call_report.redacted.json`.

Laptop and stub mode keep the ledger in `CALL_AGENT_DB_PATH` (SQLite) and the full run directory on disk. When `NEON_DATABASE_URL` is set, `did provision` and `did list` use Neon with the same `dids` table. When `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET` are all set, `finish` also puts `transcript.json`, `result.json`, `call_report.json`, and both audio files to that bucket. `events.jsonl`, `dial.json`, and `call_report.redacted.json` stay on the local disk. A partial R2 set fails before the call starts. Install `psycopg[binary]` before setting the Neon URL.

## Read a call

Start with `call_report.json`. It names the call (`call_control_id`, `run_id`, `to`, `from_did`, `mode`), the outcome (`status`, `answer_text`, `reason`), and how the call ended under `hangup`: `path` is `trail` when the final mark's echo and the trailing silence triggered the hangup, `drain_timeout` when the echo never came, `failure` when an error ended the call, and `far_party` when the callee hung up first. `timing_ms` gives milliseconds from the run start to each step (`answered`, `live_active`, `verdict`, `live_closed`, `final_mark_sent`, `final_mark_echoed`, `hangup_sent`, `call_ended`). `counts` includes `marks_sent`, `mark_echoes`, `stale_mark_echoes`, and `output_audio_chunks_after_verdict`. `refs` points at every other artifact.

`events.jsonl` is the full sequence. Every row has `seq` and `t_ms`. Rows with `kind` are events, and a `transitions` object on an event row lists the tracks whose state it changed. Rows with `effect` are what the runtime did in response (`play_audio`, `mark`, `schedule`, `hangup`, `close_live`, `finish`). Audio payloads appear as byte counts.

`call_report.redacted.json` is the shareable view. `call_report.json`, `transcript.json`, and the audio files stay local. Never send `transcript.json`, audio, or `call_report.json` to product analytics. Landing analytics stay separate. This repo has no landing code.

## Enable live mode later

Live mode places a real call. It is gated so that no test or CI run can reach the network by accident.

1. Copy `.env.example` to `.env` and fill in `TELNYX_API_KEY`, `TELNYX_CONNECTION_ID`, and `OPENAI_API_KEY`. Keep `.env` out of git.
2. Start a public tunnel to `CALL_AGENT_PORT` (default 8000), for example `ngrok http 8000`, and put its `https://` URL in `PUBLIC_BASE_URL`. Telnyx opens the media socket at that host on `/media-stream`. For local-only webhook tests you can leave `PUBLIC_WEBHOOK_URL` empty so Telnyx also posts to `PUBLIC_BASE_URL/webhooks/telnyx`. Production webhooks use the always-on Worker at `https://call-agent-webhooks.blossom-linen.workers.dev`. Set `PUBLIC_WEBHOOK_URL` and the Telnyx Call Control application webhook to `https://call-agent-webhooks.blossom-linen.workers.dev/webhooks/telnyx`. Linq registers `https://call-agent-webhooks.blossom-linen.workers.dev/linq/webhook` (alias `/webhooks/linq`). The Worker does not terminate media. A request to `/media-stream` on the Worker returns 501. See [workers/webhooks/README.md](workers/webhooks/README.md).
3. Set `QUICK_ASK_LIVE=1`.
4. Provision a real number once with `call-agent did provision --user default --live`. The Numbers API orders a voice-capable US number and records it in the ledger. Without `--live` the verb records a stub number.
5. Run `call-agent quick-ask --to +1... --ask hours --name Nathan --live`. The command hosts the two FastAPI routes in the same process, dials, bridges audio between Telnyx and GPT-Live, and prints the result when the call ends.

`call-agent verify-quick-ask --mode live` runs the same five checks against a real call. Only call numbers you control until you have reviewed consent, calling-hour, and AI-disclosure rules for your destination.

Prove the Worker inbox without deploying:

```bash
call-agent verify-webhooks
```

You should see five `[PASS]` lines and `5/5 passed`. Against a deployed Worker:

```bash
call-agent verify-webhooks --url https://call-agent-webhooks.blossom-linen.workers.dev
```

Unsigned Telnyx POSTs must be 401. If they are 200, signature verification is unwired and the lever fails.

The live path is written to the recipe but has not been exercised against Telnyx or OpenAI from this repository. The things it needs that this repository cannot supply are the Telnyx keys, the OpenAI key with GPT-Live access, and a public media `wss://` host. The always-on webhook Worker is already at `https://call-agent-webhooks.blossom-linen.workers.dev`.

## Model routing for agents

`.cursor/rules/pstack-models.mdc` maps pstack roles to the model slugs available in Cursor cloud agents. Re-run `/setup-pstack` to change a role.
