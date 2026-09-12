# Feature map

This file lists what this tree implements today. It is not a plan. It is a standing map. Each implementation updates its own rows here and its file under `.cursor/skills/verify-call-agent/features/`. How to run the CLI is in [README.md](../README.md). Why the call is a six-track reducer is in [design.md](design.md). There is no `call-agent-web` map in this repository.

## Status

`shipped` means stub-proven on this tree. CI or pytest exercises it.

`gated` means a live adapter or live CLI verb exists in code and refuses to run without its keys. Telnyx live needs `QUICK_ASK_LIVE=1` plus the Telnyx and OpenAI keys in `.env.example`. Linq live send and `linq register-webhook` need `LINQ_API_TOKEN`. Register also needs `LINQ_WEBHOOK_URL` or `PUBLIC_WEBHOOK_URL`. `README.md` states the Telnyx live path has not been exercised from this repository.

`not shipped` means placeholder, phase 2, or out of scope. The function may exist and raise.

## did provision and did list

The ledger is SQLite unless `NEON_DATABASE_URL` is set. Then it is Neon.

| name | status | does | proof |
| --- | --- | --- | --- |
| `did provision` | shipped | Assigns one number per user through `Ledger.provision`. A later run for that user prints the same E.164. `StubTelnyxHttp` starts at `+15555550100` on each new instance, so a second user in a later CLI process can hit `UNIQUE(did)`. Distinct DIDs for two users are proven only when one `StubTelnyxHttp` is reused. | `call_agent/cli.py` `did_provision`. `call_agent/ledger.py` `Ledger.provision`. `call_agent/telnyx.py` `StubTelnyxHttp.search_and_order_number`. `tests/test_ledger.py` `test_provision_is_idempotent_and_users_get_distinct_dids`. `tests/test_cli.py` `test_did_provision_twice_prints_same_did` |
| `did provision --live` | gated | Orders a US voice number through `LiveTelnyxHttp.search_and_order_number` when `--live` is set and `_require_live_gate` plus `validate_live` pass. | `call_agent/cli.py` `did_provision`. `call_agent/telnyx.py` `LiveTelnyxHttp.search_and_order_number`. `call_agent/runtime.py` `validate_live`. `tests/test_cli.py` `test_did_provision_live_is_gated` |
| `did list` | shipped | Prints every ledger row as USER, DID, and STATUS. | `call_agent/cli.py` `did_list`. `call_agent/ledger.py` `Ledger.list`. `.cursor/skills/verify-call-agent/features/did-provision.md` |
| `Ledger` | shipped | Stores DID rows in SQLite table `dids` with `user_id TEXT PRIMARY KEY` and `did TEXT UNIQUE` when `NEON_DATABASE_URL` is unset. | `call_agent/ledger.py` `Ledger` `DIDS_SCHEMA`. `call_agent/persistence.py` `parse_ledger_config`. `docs/design.md` Shape, "Idempotent by shape." `tests/test_ledger.py` `test_provision_is_idempotent_and_users_get_distinct_dids` |

## quick-ask

CLI verb `quick-ask` drives `run_call` with a `QuickAskRequest`. `run_quick_ask` is the same function.

| name | status | does | proof |
| --- | --- | --- | --- |
| `quick-ask` | shipped | Runs one outbound call through `run_call` with `--to`, `--ask`, `--name`, and `--item` for stock and aisle. | `call_agent/cli.py` `quick_ask`. `call_agent/runtime.py` `run_call`. `tests/test_cli.py` `test_quick_ask_stub_prints_answered_result`. `.cursor/skills/verify-call-agent/features/quick-ask.md` |
| `quick-ask --live` | gated | Hosts FastAPI routes `/webhooks/telnyx` and `/media-stream` with `serve_in_process` and runs `run_call` when `--live` is set and `_require_live_gate` passes. | `call_agent/cli.py` `_run_request` `_require_live_gate`. `call_agent/server.py` `serve_in_process`. `call_agent/runtime.py` `run_call`. `.cursor/skills/verify-call-agent/features/quick-ask.md` |
| `Call` | shipped | Holds six tracks named dial, telnyx, media, live, conversation, and hangup, and `reduce` applies every transition. `Call.request` is `QuickAskRequest \| BookRequest`. | `call_agent/domain.py` `Call` `reduce`. `docs/design.md` Shape, "The call is a product of tracks, reduced by one pure function." `tests/test_domain.py` |
| `HANGUP_MARK` | shipped | Sends one Telnyx mark named `hangup` after live closes, and `clean_hangup` requires `marks_sent == 1`. | `call_agent/domain.py` `HANGUP_MARK = "hangup"`. `call_agent/verify.py` `clean_hangup`. `tests/test_domain.py` `test_goodbye_audio_storm_sends_one_final_mark_after_live_closes_and_hangs_up_once`. `tests/test_runtime.py` `test_goodbye_audio_storm_hangs_up_through_one_final_mark_and_the_trail`. `tests/test_verify_quick_ask.py` `test_verify_passes_with_one_final_mark_when_goodbye_audio_storms` |
| `CLOSING_LINE` | shipped | `assess_transcript` and `assess_book` join agent text, set `collapsed = " ".join(agent_text.split())`, and require casefold containment of `Thanks, that's all I needed. Goodbye.`. | `call_agent/domain.py` `CLOSING_LINE` `assess_transcript` `assess_book`. `tests/test_domain.py` `test_split_closing_turns_still_verdict_and_enter_hangup_drain` `test_assess_book_waits_for_closing_then_books_or_declines` |
| `HangupTrailElapsedEvent` | shipped | Schedules `HangupTrailElapsedEvent` after `hangup_trailing_silence_s` of 0.4 s on the final mark echo and hangs up with path trail, and a 10 s `HangupDrainTimeoutEvent` fails `clean_hangup`. | `call_agent/domain.py` `HangupTrailElapsedEvent` `HangupDrainTimeoutEvent`. `call_agent/config.py` `hangup_trailing_silence_s` `hangup_drain_timeout_s`. `call_agent/verify.py` `marks_sent == 1` `hangup_path == "trail"` last audio then trail then `call.hangup`. `docs/design.md` Shape, "The hangup waits for the goodbye." |

## book

CLI verb `book` drives `run_call` with a `BookRequest`. This section stays next to `quick-ask`. It is not an appendix.

| name | status | does | proof |
| --- | --- | --- | --- |
| `book` | shipped | Runs one outbound booking call through `run_call` with `--to`, `--name`, `--party`, `--time`, and optional `--notes`. Stub `StubGptLive` walks party, time, and constraints, then a booked or declined close. | `call_agent/cli.py` `book`. `call_agent/runtime.py` `run_call`. `call_agent/domain.py` `BookRequest` `assess_book`. `tests/test_cli.py` `test_book_stub_prints_booked_result`. `.cursor/skills/verify-call-agent/features/book.md` |
| `book --live` | gated | Same live gate as `quick-ask --live`. Hosts FastAPI routes and runs `run_call` when `--live` is set and `_require_live_gate` passes. This repository has not dialed live book. Later live target is `+17272701288`. | `call_agent/cli.py` `_run_request` `_require_live_gate`. `call_agent/server.py` `serve_in_process`. `.cursor/skills/verify-call-agent/features/book.md` |
| `BookRequest` | shipped | Holds `party_size` (>= 1), `time_text`, and optional `constraints`. | `call_agent/domain.py` `BookRequest`. `tests/test_domain.py` `test_book_result_is_discriminated_and_confirmation_is_nonempty` |
| `BookResult` | shipped | Discriminated union on `status`: `booked` requires `confirmation_text`, `declined` / `no_answer` / `failed` pin it absent. | `call_agent/domain.py` `Booked` `BookResult`. `tests/test_domain.py` `test_assess_book_waits_for_closing_then_books_or_declines` |
| `build_book_instructions` | shipped | Personality on line 0, disclosure line `I'd like to book a table` on line 1, then party, time, and constraints on line 2. Tells the model to reason, not read a script, never join a hold or waitlist, and never delegate to a tool backend. | `call_agent/domain.py` `first_book_line` `build_book_instructions`. `tests/test_domain.py` `test_build_book_instructions_puts_disclosure_before_facts` |
| `assess_book` | shipped | Pure. Needs `CLOSING_LINE`, then booked needles or decline needles. Decline wins when both match (`fully booked` contains `booked`). A close without either needle is not a verdict. | `call_agent/domain.py` `assess_book` `BOOK_NEEDLES` `DECLINE_BOOK_NEEDLES`. `tests/test_domain.py` `test_assess_book_waits_for_closing_then_books_or_declines` |
| `StubScenario` booking faults | shipped | `one_shot_booking` emits one ask and one answer so `multi_turn_booking` fails. `decline_booking` walks the same turns and ends `declined`. | `call_agent/gpt_live.py` `StubScenario` `StubGptLive._play_booking`. `tests/test_verify_book.py` `test_verify_book_fails_one_shot_theater` `test_verify_book_passes_on_clear_decline` |

## Run directory

Each `quick-ask` or `book` run writes one run directory.

| name | status | does | proof |
| --- | --- | --- | --- |
| run directory | shipped | Writes `dial.json`, `session_start.json`, `events.jsonl`, `transcript.json`, `audio/inbound.wav`, `audio/outbound.wav`, `hangup.json`, `result.json`, `call_report.json`, and `call_report.redacted.json` under `CALL_AGENT_ARTIFACTS_DIR` in a directory named for `call_control_id`. | `call_agent/runtime.py` `RunDir`. `README.md`. `docs/design.md` Shape, "Artifacts are the proof." |
| `CallReport` | shipped | Records `timing_ms`, `hangup.path` as trail, drain_timeout, failure, or far_party, `counts.marks_sent`, and refs. `status` includes `booked`. For a booked result, `answer_text` is the confirmation. `result_artifact` checks that `CallReport` and those refs exist. | `call_agent/report.py` `CallReport` `HangupSummary.path` `Counts.marks_sent`. `call_agent/verify.py` `result_artifact`. `tests/test_verify_quick_ask.py`. `tests/test_verify_book.py` |
| `call_report.redacted.json` | shipped | Writes a `RedactedCallReport` sibling from `redact_call_report` after `call_report.json`. Phone fields are `[phone]`. The caller field is `[name]` or `None`. | `call_agent/report.py` `redact_call_report`. `call_agent/runtime.py` `RunDir.finish`. `tests/test_report.py` |

## verify-quick-ask

CLI verb `verify-quick-ask` calls `verify_quick_ask`.

| name | status | does | proof |
| --- | --- | --- | --- |
| `verify-quick-ask` | shipped | Runs five checks: `did_in_ledger`, `dial_payload`, `disclosure_first`, `clean_hangup`, and `result_artifact`. | `call_agent/cli.py` `verify_quick_ask_command`. `call_agent/verify.py` `verify_quick_ask`. `.github/workflows/ci.yml`. `tests/test_verify_quick_ask.py`. `tests/test_cli.py` `test_verify_quick_ask_stub_exits_zero`. `.cursor/skills/verify-call-agent/features/verify-quick-ask.md` |
| `verify-quick-ask --mode live` | gated | The CLI exits 2 unless `QUICK_ASK_LIVE=1`. `verify_quick_ask` still provisions with `StubTelnyxHttp` and does not call `serve_in_process`. | `call_agent/cli.py` `verify_quick_ask_command`. `call_agent/verify.py` `verify_quick_ask`. `tests/test_cli.py` `test_verify_quick_ask_live_requires_gate` `test_verify_quick_ask_live_names_missing_keys` |

## verify-book

CLI verb `verify-book` calls `verify_book`. This section stays next to `verify-quick-ask`.

| name | status | does | proof |
| --- | --- | --- | --- |
| `verify-book` | shipped | Runs six checks: `did_in_ledger`, `dial_payload`, `disclosure_first`, `clean_hangup` (`booked` or `declined`), `result_artifact`, and `multi_turn_booking`. | `call_agent/cli.py` `verify_book_command`. `call_agent/verify.py` `verify_book`. `tests/test_verify_book.py`. `tests/test_cli.py` `test_verify_book_stub_exits_zero`. `.github/workflows/ci.yml`. `.cursor/skills/verify-call-agent/features/verify-book.md` |
| `multi_turn_booking` | shipped | Requires at least three agent turns and two business turns, party and time and constraints spoken, and status `booked` or `declined`. A one-shot stub fails this check. | `call_agent/verify.py` `verify_book`. `tests/test_verify_book.py` `test_verify_book_fails_one_shot_theater`. `.cursor/skills/verify-call-agent/features/verify-book.md` |
| `verify-book --mode live` | gated | The CLI exits 2 unless `QUICK_ASK_LIVE=1`. `verify_book` still provisions with `StubTelnyxHttp` and does not call `serve_in_process`. | `call_agent/cli.py` `verify_book_command`. `tests/test_cli.py` `test_verify_book_live_requires_gate` |

## verify-call-agent

The Cursor skill drives the CLI verbs in stub mode and never sets live keys or Neon and R2 vars. Its `features/` directory is a standing map. Update it in the same change that ships or changes a verb.

| name | status | does | proof |
| --- | --- | --- | --- |
| `.cursor/skills/verify-call-agent/SKILL.md` | shipped | States stub drive rules and that the skill never drives live mode or sets Neon and R2 vars. Lists `book`, `verify-book`, `verify-message`, `verify-webhooks`, and `verify-persistence`. | `.cursor/skills/verify-call-agent/SKILL.md` |
| `.cursor/skills/verify-call-agent/features/README.md` | shipped | Indexes `did-provision`, `quick-ask`, `verify-quick-ask`, `book`, `verify-book`, `verify-message`, `webhooks`, `linq-inbound`, `verify-webhooks`, and `verify-persistence` recipes. | `.cursor/skills/verify-call-agent/features/README.md` `tests/test_feature_map.py` |
| `.cursor/skills/verify-call-agent/features/webhooks.md` | shipped | Maps always-on Worker versus local tunnel, Fly `APP_BASE_URL`, `PUBLIC_WEBHOOK_URL`, and the Linq hook. | `.cursor/skills/verify-call-agent/features/webhooks.md` |
| `.cursor/skills/verify-call-agent/features/linq-inbound.md` | shipped | Maps closed-alpha iMessage Worker forward at `https://call-agent-webhooks.usetreena.workers.dev/linq/webhook`, MessagingPort ack on `message.received`, and inbound artifacts. SMS stays off. | `.cursor/skills/verify-call-agent/features/linq-inbound.md` |
| `.cursor/skills/verify-call-agent/features/verify-webhooks.md` | shipped | Covers the Worker inbox stub checks, `--url` wiring, and exit codes. | `.cursor/skills/verify-call-agent/features/verify-webhooks.md` |
| `.cursor/skills/verify-call-agent/features/did-provision.md` | shipped | Covers first provision, repeat provision, a second user, and `did list`. | `.cursor/skills/verify-call-agent/features/did-provision.md` |
| `.cursor/skills/verify-call-agent/features/quick-ask.md` | shipped | Covers hours, stock, aisle, missing `--item`, artifacts, and the live gate. | `.cursor/skills/verify-call-agent/features/quick-ask.md` |
| `.cursor/skills/verify-call-agent/features/verify-quick-ask.md` | shipped | Covers the five stub checks, defaults, the live gate, and exit codes. | `.cursor/skills/verify-call-agent/features/verify-quick-ask.md` |
| `.cursor/skills/verify-call-agent/features/book.md` | shipped | Covers stub book, missing flags, artifacts, and the live gate. | `.cursor/skills/verify-call-agent/features/book.md` |
| `.cursor/skills/verify-call-agent/features/verify-book.md` | shipped | Covers the six stub checks, theater fail, decline, the live gate, and exit codes. | `.cursor/skills/verify-call-agent/features/verify-book.md` |
| `.cursor/skills/verify-call-agent/features/verify-message.md` | shipped | Covers the six MessagingPort checks, inbound reply artifacts, and the register-webhook gate. | `.cursor/skills/verify-call-agent/features/verify-message.md` |
| `.cursor/skills/verify-call-agent/features/verify-persistence.md` | shipped | Covers the three stub checks, the SQLite and local fallback, and the misconfig exit. | `.cursor/skills/verify-call-agent/features/verify-persistence.md` |
| `.cursor/skills/verify-call-agent/scripts/verify_stub.sh` | shipped | Runs one isolated stub drive of those recipes, including `verify-book`, `verify-message`, `verify-webhooks`, `verify-persistence`, and `linq register-webhook` exit 2 without a token. | `.cursor/skills/verify-call-agent/scripts/verify_stub.sh` |

## Always-on webhooks

The Worker is the production HTTPS inbox. FastAPI inbound processing runs on always-on Fly Machines. The laptop tunnel is the local-dev path and the media host until a later always-on media process exists.

| name | status | does | proof |
| --- | --- | --- | --- |
| `POST /webhooks/telnyx` Worker | shipped | Cloudflare Worker `call-agent-webhooks` verifies Telnyx Ed25519, claims `data.id`, returns 2xx on first seen and on duplicate, and 401 when the signature is missing or wrong. Laptop-off still ACKs. | `workers/webhooks/src/handler.ts` `receiveTelnyx`. `workers/webhooks/test/webhook.test.ts`. `call-agent verify-webhooks` `signature` `idempotency` |
| `GET /health` Worker | shipped | Returns `{"ok":true}`. | `workers/webhooks/src/handler.ts`. `call-agent verify-webhooks` `health` |
| `GET /health` FastAPI | shipped | Returns `{"ok":true}` for Fly HTTP checks. | `call_agent/server.py` `health`. `tests/test_server.py` `test_health_returns_ok` |
| Linq inbound (closed alpha) | shipped | L1 testers text Treena through Linq. Ingress is `POST https://call-agent-webhooks.usetreena.workers.dev/linq/webhook` (alias `/webhooks/linq`). GET ping is `{"ok":true,"provider":"linq"}`. POST forwards raw body and Standard Webhooks headers to `APP_BASE_URL/linq/webhook`. Unset `APP_BASE_URL` is 501 `missing_app_base_url`. Prod FastAPI is the always-on Fly host `https://hidden-beacon-080.fly.dev` with `LINQ_WEBHOOK_SIGNING_SECRET`. HMAC verify, `message.received` map, MessagingPort ack, and `CALL_AGENT_ARTIFACTS_DIR/linq/<event_id>/` inbound text. No SMS. | `workers/webhooks/src/handler.ts` `receiveLinq`. `call_agent/server.py` `process_linq_webhook`. `call-agent verify-webhooks` `linq_hook`. `call-agent verify-message` `inbound_reply`. `.cursor/skills/verify-call-agent/features/linq-inbound.md` |
| Worker→app Linq forward | shipped | Worker POST `/linq/webhook` awaits `APP_BASE_URL/linq/webhook` with raw body and Standard Webhooks headers. Unset origin is 501 `missing_app_base_url`. Unreachable app is 502 `app_unreachable`. FastAPI `process_linq_webhook` verifies HMAC, maps iMessage `message.received`, and replies via MessagingPort. Prod `APP_BASE_URL` is the always-on Fly host `https://hidden-beacon-080.fly.dev` (no path). | `workers/webhooks/src/linq.ts` `parseAppBaseUrl`. `workers/webhooks/src/handler.ts` `receiveLinq`. `call_agent/server.py` `process_linq_webhook`. `workers/webhooks/test/webhook.test.ts` |
| Fly Machines FastAPI | shipped | Dockerfile + fly.toml. App name is `hidden-beacon-080`. uvicorn serves `call_agent.server:app` on port 8000. Always-on: `auto_stop_machines = "off"` and `min_machines_running = 1`. Worker `APP_BASE_URL` is `https://hidden-beacon-080.fly.dev` (no path). SMS stays off. | `Dockerfile`. `fly.toml`. `README.md` Deploy FastAPI on Fly Machines. `tests/test_feature_map.py` |
| Worker `/media-stream` | shipped | Returns 501 `media_not_terminated_here`. The Worker does not terminate Telnyx media. | `workers/webhooks/src/handler.ts`. `call-agent verify-webhooks` `media_gap` |
| `verify-webhooks` | shipped | Headless five-check lever for parse, signature, idempotency, the Linq hook, and the media gap. Stub drives `createHandler` in-process. `--url` hits a deployed Worker and fails loud if unsigned Telnyx POST is 200. | `call_agent/verify_webhooks.py` `verify_webhooks`. `call_agent/cli.py` `verify_webhooks_command`. `workers/webhooks/scripts/verify.mjs`. `tests/test_verify_webhooks.py`. `.cursor/skills/verify-call-agent/features/verify-webhooks.md` |
| `PUBLIC_WEBHOOK_URL` | shipped | Dial `webhook_url` uses this origin when set. `stream_url` still comes from `PUBLIC_BASE_URL`. Empty keeps today's single-origin tunnel. | `call_agent/runtime.py` `webhook_and_stream_urls`. `tests/test_verify_webhooks.py` `test_webhook_url_splits_from_media_origin` |
| local FastAPI tunnel | gated | `quick-ask --live` still hosts `/webhooks/telnyx` and `/media-stream` in-process for laptop dev. Prod Call Control webhook URL is the Worker, not the tunnel. | `call_agent/server.py`. `README.md`. `.cursor/skills/verify-call-agent/features/webhooks.md` |
| wrangler deploy | shipped | Live origin is `https://call-agent-webhooks.usetreena.workers.dev`. Linq registers `https://call-agent-webhooks.usetreena.workers.dev/linq/webhook`. Telnyx uses `/webhooks/telnyx`. Linq POST needs Worker secret `APP_BASE_URL` (FastAPI origin). `TELNYX_PUBLIC_KEY` is still unset on the preview Worker (unsigned Telnyx POST is 401 `missing_public_key`). | `workers/webhooks/wrangler.jsonc`. `workers/webhooks/README.md` |

## Live adapters

Live mode uses `LiveTelnyxHttp`, `LiveTelnyxMedia`, and `LiveGptLive`.

| name | status | does | proof |
| --- | --- | --- | --- |
| live Telnyx and OpenAI | gated | Defines `LiveTelnyxHttp`, `LiveTelnyxMedia`, and `LiveGptLive` for `--live` and `--mode live`. | `call_agent/telnyx.py` `LiveTelnyxHttp` `LiveTelnyxMedia`. `call_agent/gpt_live.py` `LiveGptLive`. `README.md` |
| live prompt | shipped | `build_instructions` and `build_book_instructions` join a short live prompt. Line 0 is role plus personality. Line 1 is the opener. Line 2 is the ask or party, time, and constraints. Then `say exactly: {CLOSING_LINE}`, product guardrails, and a `Delegation policy` block. No Backchannel or Interruption policy. | `call_agent/domain.py` `build_instructions` `build_book_instructions`. `tests/test_domain.py` `test_build_instructions_puts_disclosure_before_ask` `test_build_book_instructions_puts_disclosure_before_facts`. `docs/gpt-live-prompting.md`. `.cursor/skills/verify-call-agent/features/gpt-live-prompt.md` |
| speak_first | shipped | After `session.started`, `LiveGptLive.speak_first` sends `session.instructions.append` then `session.commentary.append`, both with `delegation_id: None`. Instructions say speak first, then listen, and name the exact opener. They do not say `verbatim`. | `call_agent/gpt_live.py` `speak_first_frames` `LiveGptLive.speak_first`. `tests/test_wire.py` `test_speak_first_frames_are_instructions_then_commentary` `test_live_gpt_live_speak_first_sends_append_frames` |
| Realtime reject | shipped | `parse_event` raises on Realtime types including `session.update`, `session.created`, `conversation.item.*`, `response.*`, and `input_audio_buffer.*`. | `call_agent/gpt_live.py` `parse_event`. `tests/test_wire.py` `test_parse_gpt_live_rejects_realtime_and_ignores_usage` |
| no tool-delegation | shipped | `session.start` has no `delegation` field. The live prompt lists no backend tools. The verdict stays `assess_transcript` or `assess_book`. | `call_agent/gpt_live.py` `serialize_session_start`. `call_agent/domain.py` `DELEGATION_POLICY_LINES`. `tests/test_wire.py` `test_serialize_session_start_matches_gpt_live_payload`. `docs/gpt-live-prompting.md` |

## MessagingPort and Linq

This section is the inventory for the current MessagingPort implementation. Update it when the port, adapter, verifier, or webhook path changes.

Telnyx owns PSTN calls. Linq Partner API V3 owns the closed-alpha iMessage kickoff thread. SMS stays off.

Closed-alpha testers text Treena on this Linq path. The sending line is the sandbox DID (`LINQ_FROM_E164`; sandbox has used `+12138653024`). That is not a public landing claim and not a blue-bubble marketing claim. Outbound `message.parts` text from `+12138653024` to `+17272701288` landed on Builder's box; that prove is outside this repository. The Workers inbound URL is live and the Linq subscription is registered. The Worker forwards Linq POST to `APP_BASE_URL`. FastAPI in this process owns HMAC verify, `message.received` mapping, and the MessagingPort reply.

| name | status | does | proof |
| --- | --- | --- | --- |
| `MessagingPort` | shipped | Protocol `send` / `receive` over chat `parts`, not an SMS body. | `call_agent/messaging.py` `MessagingPort` `OutboundChat` `InboundMessage`. `tests/test_messaging.py` `test_stub_send_and_receive_round_trip` |
| Linq V3 outbound stub | shipped | `StubLinq.send` records `POST /chats` with `preferred_service: iMessage` and `message.parts` text. | `call_agent/linq.py` `serialize_outbound` `StubLinq`. `tests/test_messaging.py` `test_text_chat_is_parts_not_sms_body`. `call_agent/verify_message.py` `stub_send` |
| Linq V3 live send | gated | Closed-alpha outbound on the sandbox line. `LiveLinqHttp` posts the same JSON and fails loud without `LINQ_API_TOKEN`. `kickoff_from_settings` fails loud without `LINQ_FROM_E164`. This repository has not live-sent. Builder-box prove used `+12138653024` → `+17272701288`. | `call_agent/linq.py` `LiveLinqHttp` `linq_from` `kickoff_from_settings`. `tests/test_messaging.py` `test_live_constructor_fails_without_token` `test_kickoff_from_settings_requires_from` `test_live_send_posts_chats_parts_shape`. `docs/linq.md` |
| Linq V3 inbound | shipped | L1 path: Worker `POST /linq/webhook` forwards to `APP_BASE_URL/linq/webhook`. FastAPI `POST /webhooks/linq` and `POST /linq/webhook` verify Standard Webhooks on raw bytes using `LINQ_WEBHOOK_SIGNING_SECRET`, map iMessage `message.received` into `InboundMessage`, send a `Received: {text}` ack on `POST /chats/{chat_id}/messages` via MessagingPort, and write `inbound.json` / `reply.json` under `CALL_AGENT_ARTIFACTS_DIR/linq/<event_id>/`. Missing secret or bad signature is 401. Missing artifact dir is 500. `message.sent` / `delivered` / `read` / `failed` and SMS are ignored. Unset Worker `APP_BASE_URL` is 501. Live reply needs `LINQ_API_TOKEN` and `LINQ_FROM_E164`. | `workers/webhooks/src/linq.ts` `parseAppBaseUrl`. `call_agent/linq.py` `parse_inbound` `verify_linq_signature` `handle_inbound`. `call_agent/server.py` `process_linq_webhook`. `tests/test_messaging.py` `test_linq_webhook_replies_and_writes_inbound_text` `test_linq_webhook_fails_when_artifacts_dir_missing` `test_receive_ignores_sms_and_non_message_events` |
| `verify-message` | shipped | Six stub checks: send shape, inbound map, inbound reply + artifact text, signature, live token gate, iMessage-only. CI runs the CLI verb. | `call_agent/verify_message.py` `verify_message`. `call_agent/cli.py` `verify_message_command`. `.github/workflows/ci.yml`. `tests/test_cli.py` `test_verify_message_stub_exits_zero`. `.cursor/skills/verify-call-agent/features/verify-message.md` |
| `linq register-webhook` | gated | Closed-alpha subscription `4c1ad6c6-d674-4fa2-b63a-ca947f2d9b7c` is already registered. The verb still `POST /webhook-subscriptions` when `LINQ_API_TOKEN` and `LINQ_WEBHOOK_URL` or `PUBLIC_WEBHOOK_URL` are set, and prints `signing_secret` to store as `LINQ_WEBHOOK_SIGNING_SECRET`. Fails loud if token or URL is missing. | `call_agent/cli.py` `linq_register_webhook`. `tests/test_cli.py` `test_linq_register_webhook_fails_without_token` `test_linq_register_webhook_fails_without_url` |
| always-on Linq webhook URL | shipped | Registered Workers target `https://call-agent-webhooks.usetreena.workers.dev/linq/webhook?version=2026-02-03`. Subscription `4c1ad6c6-d674-4fa2-b63a-ca947f2d9b7c` listens for `message.received`, `message.sent`, `message.delivered`, `message.read`, `message.failed`. Worker forwards POST to `APP_BASE_URL`. Signing secret is `LINQ_WEBHOOK_SIGNING_SECRET` on Builder's box, not in this VM. | `docs/linq.md` |
| SMS | not shipped | No SMS adapter. Outbound pins iMessage. Inbound SMS events are ignored. | `call_agent/linq.py` `serialize_outbound` `parse_inbound`. `tests/test_messaging.py` `test_receive_ignores_sms_and_non_message_events` |

## Design

`docs/design.md` Usage shows `did provision`, `did list`, `quick-ask`, `verify-quick-ask`, `book`, and `verify-book`. Messaging is a sibling port. See [linq.md](linq.md). Prod webhook ingress is the Worker inbox. `verify-webhooks` is the Worker lever. `verify-message` is the MessagingPort lever. `verify-persistence` is the later port lever. It is listed under Durable persistence.

`README.md` states hold queues, packaging, and SMS are out of scope. Closed alpha includes iMessage through MessagingPort in this process and Worker forward on the Linq hook. Restaurant book is shipped in stub. This repository has not dialed live book. Neon and R2 ports are shipped with sqlite and local fallback. Live Neon and R2 stay gated on env.

`docs/design.md` Shape has "The hangup waits for the goodbye." and "Artifacts are the proof."

## Not shipped

| name | status | does | proof |
| --- | --- | --- | --- |
| hold queues | not shipped | Named out of scope in `README.md`. `build_book_instructions` says never join a hold or waitlist. `build_instructions` for quick-ask still says never book. | `README.md`. `call_agent/domain.py` `build_book_instructions` `build_instructions` |
| packaging | not shipped | Named out of scope in `README.md` and has no code. | `README.md` |
| Linq SMS | not shipped | Worker exposes `POST /linq/webhook`. No SMS send or receive. | `workers/webhooks/src/handler.ts`. `README.md` |
| host and providers | not shipped | No host or providers module exists under `call_agent/`. | `call_agent/` |

## Durable persistence

Neon holds the DID ledger when `NEON_DATABASE_URL` is set. R2 holds the finished artifact subset when the four required `R2_*` vars are set. Unset env keeps SQLite and `CALL_AGENT_ARTIFACTS_DIR`. Partial R2 env fails before any I/O.

| name | status | does | proof |
| --- | --- | --- | --- |
| SQLite ledger fallback | shipped | `parse_ledger_config` returns `SqliteLedgerConfig` when `NEON_DATABASE_URL` is unset or blank. `open_ledger` constructs `Ledger`. | `call_agent/persistence.py` `parse_ledger_config`. `call_agent/ledger.py` `open_ledger`. `tests/test_persistence.py` `test_unset_env_selects_sqlite_and_local`. `tests/test_ledger.py` `test_open_ledger_unset_returns_sqlite` |
| Neon ledger | shipped | `NEON_DATABASE_URL` selects `NeonLedger` and the same `dids` schema. Provision stays idempotent. A blank or schemeless URL fails loud. Missing `psycopg` fails loud. | `call_agent/ledger.py` `NeonLedger` `DIDS_SCHEMA`. `call_agent/persistence.py` `NeonLedgerConfig`. `tests/test_ledger.py` `test_neon_ledger_roundtrip_through_sqlite_bridge` `test_psycopg_connect_fails_loud_when_driver_missing`. `tests/test_persistence.py` `test_neon_url_without_scheme_fails_loud` |
| Local artifact fallback | shipped | `parse_artifact_config` returns `LocalArtifactConfig` when no `R2_*` var is set. `LocalArtifactStore` put/get writes under `CALL_AGENT_ARTIFACTS_DIR`. | `call_agent/persistence.py` `parse_artifact_config`. `call_agent/artifacts.py` `LocalArtifactStore`. `tests/test_artifacts.py` `test_local_artifact_put_get_roundtrip` `test_open_artifact_store_defaults_local` |
| R2 artifacts | shipped | Complete `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET` selects `R2ArtifactStore`. Put/get use the S3 API. Partial env raises `PersistenceConfigError`. `RunDir.finish` publishes transcript, result, `call_report.json`, and both audio files. `events.jsonl` stays local. | `call_agent/artifacts.py` `R2ArtifactStore` `publish_durable_artifacts` `DURABLE_RELATIVE`. `call_agent/runtime.py` `FinishEffect`. `tests/test_artifacts.py` `test_r2_put_get_with_httpx_mock` `test_publish_durable_artifacts_puts_documented_subset`. `tests/test_persistence.py` `test_partial_r2_fails_loud_and_names_missing` |
| `verify-persistence` | shipped | Headless lever. Unset env prints three `[PASS]` lines for `config`, `ledger_roundtrip`, and `artifact_put_get` against SQLite and local stubs. Partial `R2_*` or a bad `NEON_DATABASE_URL` exits 2. Complete Neon/R2 env still exercises stubs so CI needs no cloud. | `call_agent/cli.py` `verify_persistence_command`. `call_agent/verify_persistence.py` `verify_persistence`. `tests/test_verify_persistence.py`. `.cursor/skills/verify-call-agent/features/verify-persistence.md` |
