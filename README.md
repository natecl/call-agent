# call-agent

A phone agent that calls a business on your behalf, asks one quick question, and writes down the answer. You paste an E.164 number, pick `hours`, `stock`, or `aisle`, and give your first name. The agent dials from a Telnyx number that belongs to you, opens with "I'm calling on behalf of Nathan", gets the answer, hangs up, and leaves a result, a transcript, and the call audio on disk.

The stack is Python 3.12, Pydantic v2, Typer, FastAPI (two routes), Telnyx Call Control with media streaming, an app-owned GPT-Live bridge that follows [Telnyx's outbound GPT-Live recipe](https://telnyx.com/resources/outbound-ai-calls-python-openai-live), and a DID ledger that defaults to SQLite. Set `NEON_DATABASE_URL` to use Neon. Set the four `R2_*` vars to put finished artifacts on R2. The design and its rationale are in [docs/design.md](docs/design.md). How V1 prompts GPT-Live, and the OpenAI page that is the source of truth for it, are in [docs/gpt-live-prompting.md](docs/gpt-live-prompting.md). What this tree implements today is listed in [docs/FEATURE_MAP.md](docs/FEATURE_MAP.md). The standing verification map is [`.cursor/skills/verify-call-agent/features/`](.cursor/skills/verify-call-agent/features/README.md). Both maps update when a lever ships.

Day 1 ships quick-ask and restaurant book. Closed alpha includes iMessage inbound through the Linq Worker hook and the `MessagingPort` process path. Live send is gated on `LINQ_API_TOKEN`. That is not a public landing claim. Hold queues, packaging, and SMS are out of scope. Telnyx vs Linq is in [docs/linq.md](docs/linq.md). This repository has not dialed live book.

## Public webhook URL (Linq, register now)

Closed alpha L1 testers text Treena through Linq. This Worker URL is on that inbound path. Builder can register it now. No Nathan click. SMS stays off.

`https://call-agent-webhooks.usetreena.workers.dev/linq/webhook`

`GET /linq/webhook` returns `{"ok":true,"provider":"linq"}`. `POST` forwards the raw body and Standard Webhooks headers to `APP_BASE_URL/linq/webhook`. Unset `APP_BASE_URL` is 501 `missing_app_base_url`. Alias `https://call-agent-webhooks.usetreena.workers.dev/webhooks/linq`. Telnyx stays on `https://call-agent-webhooks.usetreena.workers.dev/webhooks/telnyx`. FastAPI `POST /linq/webhook` verifies HMAC and replies on iMessage `message.received` via MessagingPort. Non-kickoff chat echoes `Received: {text}`. A natural book ask without the restaurant number gets a phone-number collect on the same chat. After the number, Treena asks to confirm. Confirm / approve / yes returns 202 and this process texts `Okay, I'm going to call them now.`, places the call, then texts the result. The first message never dials. The Worker does not send SMS. Prod FastAPI is the always-on Fly app `hidden-beacon-080`. Set Worker `APP_BASE_URL` to `https://hidden-beacon-080.fly.dev` (no path). Details are in [workers/webhooks/README.md](workers/webhooks/README.md).

## Deploy FastAPI on Fly Machines

Fly Machines run always-on uvicorn. `fly.toml` pins `app = "hidden-beacon-080"` and sets `auto_stop_machines = "off"` and `min_machines_running = 1`. The live origin is `https://hidden-beacon-080.fly.dev` (no path). The laptop is not the prod FastAPI host. Worker `APP_BASE_URL` is that origin. Do not `fly launch` a second app. This VM has no Fly token.

```bash
fly secrets set LINQ_WEBHOOK_SIGNING_SECRET=...
fly secrets set ACCOUNT_SYNC_SECRET=...
fly deploy
```

`LINQ_WEBHOOK_SIGNING_SECRET` is required for inbound HMAC. `ACCOUNT_SYNC_SECRET` is required for `POST /accounts/sync` (call-agent-web checkout/settings). Live inbound reply also needs `LINQ_API_TOKEN` and `LINQ_FROM_E164` on this process. HMAC verify without a token still ACKs ignored events; `message.received` send uses StubLinq when the token is unset. Seed L1 testers with `call-agent account upsert --phone +1... --name Nathan --from-did +1...` until the website PR POSTs sync. `from_did` is the assigned Treena number, not the tester's iMessage phone. The sync contract is [docs/accounts.md](docs/accounts.md). This tree does not `fly deploy`.

Then point the Worker at the Fly origin (no path) and deploy the Worker:

```bash
cd workers/webhooks
npx wrangler secret put APP_BASE_URL
```

The value is `https://hidden-beacon-080.fly.dev`. Then:

```bash
npx wrangler deploy
```

## Run the stub verification

Stub mode fakes the three network boundaries (Telnyx HTTP, Telnyx media WebSocket, OpenAI GPT-Live WebSocket) with raw JSON fixtures that go through the same parsers live mode uses. It needs no keys and opens no sockets. CI runs it on every push.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
call-agent verify-quick-ask --to +15555550123 --ask hours --name Nathan --mode stub
call-agent verify-book --to +15555550123 --name Nathan --party 2 --time "Friday 7pm" --mode stub
```

`verify-quick-ask` should print eight `[PASS]` lines and `8/8 passed`, and exit 0. The eight checks are:

1. `did_in_ledger`. The user has exactly one number in the ledger, and provisioning twice returns the same number.
2. `dial_payload`. The Call Control body has `from` set to that number, `to` set to the destination, a `wss://` `stream_url`, `stream_bidirectional_mode: rtp`, and `stream_bidirectional_target_legs: self`.
3. `disclosure_first`. The GPT-Live `session.start` instructions name the caller ("on behalf of Nathan") before the question, and the agent's first transcript turn does too.
4. `clean_hangup`. The verdict comes from `record_task_result`, the agent calls `end_call`, this process farewells then drains, sends exactly one Telnyx mark named `hangup` once GPT-Live confirms the close, Telnyx echoes it, `HANGUP_TRAILING_SILENCE_S` (default 0.4 s) elapses, the hangup follows, and Telnyx confirms with `call.hangup`. `hangup.path` is `tool`. A drain that runs into `HANGUP_DRAIN_TIMEOUT_S` (default 10 s), or a call that sends more than one mark, fails this check.
5. `result_artifact`. `result.json` is a valid `QuickAskResult`, its `transcript_ref` and `audio_ref` point at files that exist, and `call_report.json` is a valid `CallReport` whose refs exist.
6. `session_delegation`. `session.start` has `delegation.type=responses`, tools `record_task_result` and `end_call`, audio 16 kHz, and no resample.
7. `tool_loop`. A Responses delegation, tool calls, `tool_result` then `continue_response`, and `task.json` whose outcome maps to the result status.
8. `app_owned_hangup`. Hangup path `tool`, one mark, trail before `call.hangup`, no silence-failsafe steer.

`verify-book` should print ten `[PASS]` lines and `10/10 passed`. The first five checks match `verify-quick-ask`, except `clean_hangup` expects `booked` or `declined`. The sixth check is `multi_turn_booking`. The transcript must have at least three agent turns and two business turns, and it must mention party, time, and constraints. A one-shot stub that skips that conversation fails this check and exits 1. The seventh check is `keeps_talking`: the stub emits Responses tools after the time turn, this process returns the tool result and continues, and the agent speaks again before hanging up `via=tool`. Checks 8–10 are the same `session_delegation`, `tool_loop`, and `app_owned_hangup` rows.

To run the test suite:

```bash
pytest -q
```

Stub messaging (no Linq token, no public URL):

```bash
call-agent verify-message
```

You should see six `[PASS]` lines and `6/6 passed`. The checks are stub send shape, inbound `message.received` mapping, inbound reply + artifact text, Standard Webhooks signature accept/reject, live constructor without `LINQ_API_TOKEN`, and SMS ignored. See [docs/linq.md](docs/linq.md).

`call-agent verify-linq-call` proves iMessage natural collect + confirm-before-dial → stub `run_call` → result reply on the same chat, plus artifact cross-links. Fourteen `[PASS]` lines and `14/14 passed`, including `collect_time`, `from_did_users_row` and `no_number_order`. It fails if dial happens before confirm. It does not dial Telnyx or hit Linq live. qa owns the Fly prove.

`call-agent verify-persistence` checks the Neon and R2 ports. With those vars unset it uses SQLite and a local directory, prints three `[PASS]` lines, and exits 0. If some `R2_*` vars are set and others are not, or if `NEON_DATABASE_URL` is not a `postgresql://` URL, it exits 2 and names the problem. CI runs it with the vars unset. It never opens Neon or R2.

## Use the CLI in stub mode

```bash
call-agent did provision --user default
call-agent did list
call-agent account upsert --phone +14155552671 --name Nathan --from-did +15555550177
call-agent quick-ask --to +14155552671 --ask hours --name Nathan
call-agent quick-ask --to +14155552671 --ask stock --item "oat milk" --name Nathan
call-agent book --to +14155552671 --name Nathan --party 2 --time "Friday 7pm" --notes "outdoor if possible"
```

`quick-ask` prints one JSON object with `status` (`answered`, `declined`, `no_answer`, or `failed`), `answer_text`, `call_control_id`, `transcript_ref`, and `audio_ref`. `book` prints `status` (`booked`, `declined`, `no_answer`, or `failed`) and `confirmation_text` on a booked result. The refs are relative to `CALL_AGENT_ARTIFACTS_DIR`. During the call, each run writes one directory there with `dial.json`, `session_start.json`, `events.jsonl`, `transcript.json`, `audio/inbound.wav`, `audio/outbound.wav`, `hangup.json`, `result.json`, `call_report.json`, and `call_report.redacted.json`. After hangup, those files are deleted unless `RETAIN_CALL_ARTIFACTS` is set or the request sets `retain_call_artifacts=True`. Crash leftovers are accepted. `verify-quick-ask` and `verify-book` retain so they can prove hangup and transcript shape.

Laptop and stub mode keep the ledger in `CALL_AGENT_DB_PATH` (SQLite). When `NEON_DATABASE_URL` is set, `did provision` and `did list` use Neon with the same `dids` table. When retention is on and `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET` are all set, `run_call` also puts `transcript.json`, `result.json`, `call_report.json`, and both audio files to that bucket. Default wipe skips that publish. `events.jsonl`, `dial.json`, and `call_report.redacted.json` stay on the local disk when retained. A partial R2 set fails before the call starts. Install `psycopg[binary]` before setting the Neon URL.

## Read a call

Start with `call_report.json`. It names the call (`call_control_id`, `run_id`, `to`, `from_did`, `mode`), the outcome (`status`, `answer_text`, `reason`), and how the call ended under `hangup`: `path` is `trail` when the final mark's echo and the trailing silence triggered the hangup, `drain_timeout` when the echo never came, `failure` when an error ended the call, and `far_party` when the callee hung up first. `timing_ms` gives milliseconds from the run start to each step (`answered`, `live_active`, `verdict`, `live_closed`, `final_mark_sent`, `final_mark_echoed`, `hangup_sent`, `call_ended`). `counts` includes `marks_sent`, `mark_echoes`, `stale_mark_echoes`, and `output_audio_chunks_after_verdict`. `refs` points at every other artifact.

`events.jsonl` is the full sequence. Every row has `seq` and `t_ms`. Rows with `kind` are events, and a `transitions` object on an event row lists the tracks whose state it changed. Rows with `effect` are what the runtime did in response (`play_audio`, `mark`, `schedule`, `hangup`, `close_live`, `finish`). Audio payloads appear as byte counts.

`call_report.redacted.json` is the shareable view. `call_report.json`, `transcript.json`, and the audio files stay local. Never send `transcript.json`, audio, or `call_report.json` to product analytics. Landing analytics stay separate. This repo has no landing code.

## Enable live mode later

Live mode places a real call. It is gated so that no test or CI run can reach the network by accident.

1. Copy `.env.example` to `.env` and fill in `TELNYX_API_KEY`, `TELNYX_CONNECTION_ID`, and `OPENAI_API_KEY`. Keep `.env` out of git.
2. Start a public tunnel to `CALL_AGENT_PORT` (default 8000), for example `ngrok http 8000` or `cloudflared tunnel --url http://localhost:8000`, and put that tunnel's `https://` URL in **both** `PUBLIC_BASE_URL` and `PUBLIC_WEBHOOK_URL`. CLI `--live` hosts CallHandle in this process. Telnyx must open `/media-stream` and post the per-call `webhook_url` on that same host. Process env overrides `.env`, so a leftover `PUBLIC_WEBHOOK_URL` can send webhooks to a stale host while media follows `PUBLIC_BASE_URL` (Fly media + old tunnel). The verb fails before dial if webhook host != stream host after wss/https normalize, or if `{PUBLIC_BASE_URL}/health` is not HTTP 200 with this process's `serve_token`. Fly `https://hidden-beacon-080.fly.dev` is a different process with no BOOK entry; it is not valid as `PUBLIC_BASE_URL` for Mac CLI live. The verb waits until local `/health` accepts, then probes `{PUBLIC_BASE_URL}/health` until HTTP 200 and this process's `serve_token` match (and `/media-stream` accepts) before it dials. A printed trycloudflare URL is not enough: DNS can lag after the URL is advertised. The Call Control application `webhook_event_url` is independently `https://call-agent-webhooks.usetreena.workers.dev/webhooks/telnyx` (always-on, laptop-off). That Worker does not terminate media: `/media-stream` returns 501. Optional delivery proof through a working tunnel: `POST /webhooks/telnyx` with an unknown `call_control_id` returns 404 `unknown call` from this process (the Worker unsigned POST is 401). Linq registers `https://call-agent-webhooks.usetreena.workers.dev/linq/webhook` (alias `/webhooks/linq`). See [workers/webhooks/README.md](workers/webhooks/README.md).
3. Set `QUICK_ASK_LIVE=1`.
4. Assign the outbound caller ID. Live CLI takes `--from-did` or reads `users.from_did` for `--user`. Seed with `call-agent account upsert --phone +1... --name Nathan --from-did +1...`. Confirm/dial never calls `ledger.provision` and never `POST /v2/number_orders`. `did provision --live` is an ops verb that can order a number into the `dids` table; it is not a live-call prerequisite.
5. Run `call-agent quick-ask --to +1... --ask hours --name Nathan --live --from-did +1...`. The command hosts the two FastAPI routes in the same process, dials, bridges audio between Telnyx and GPT-Live, and prints the result when the call ends. `call-agent book --to +1... --name Nathan --party 2 --time "Friday 7pm" --live --from-did +1...` is the same path with a `BookRequest`. The later live target for book is `+17272701288`. Do not dial it until `{PUBLIC_BASE_URL}/health` returns HTTP 200 with this process's `serve_token`. Cap GO on this fail-loud climb. HOLD REDIAL for the live book. This tree does not place that call.

`call-agent verify-quick-ask --mode live` runs the same eight checks against a real call. `call-agent verify-book --mode live` is gated the same way and still runs the stub adapters unless you change the code. Only call numbers you control until you have reviewed consent, calling-hour, and AI-disclosure rules for your destination.

Prove the Worker inbox without deploying:

```bash
call-agent verify-webhooks
```

You should see five `[PASS]` lines and `5/5 passed`. Against a deployed Worker:

```bash
call-agent verify-webhooks --url https://call-agent-webhooks.usetreena.workers.dev
```

Unsigned Telnyx POSTs must be 401. If they are 200, signature verification is unwired and the lever fails.

The live path is written to the recipe. CLI `--live` now fails loud if `PUBLIC_BASE_URL` does not reach this process. A successful live book against Telnyx still needs keys, GPT-Live access, and a public media `wss://` tunnel on the laptop. The always-on webhook Worker is already at `https://call-agent-webhooks.usetreena.workers.dev`.

## Model routing for agents

`.cursor/rules/pstack-models.mdc` maps pstack roles to the model slugs available in Cursor cloud agents. Re-run `/setup-pstack` to change a role.
