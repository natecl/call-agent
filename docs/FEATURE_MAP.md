# Feature map

This file lists what `main` implements today. It is not a plan. How to run the CLI is in [README.md](../README.md). Why the call is a six-track reducer is in [design.md](design.md).

## Status

`shipped` means stub-proven on main. CI or pytest exercises it.

`gated` means a live adapter or live CLI verb exists in code and refuses to run without its keys. Telnyx live needs `QUICK_ASK_LIVE=1` plus the Telnyx and OpenAI keys in `.env.example`. Linq live send and `linq register-webhook` need `LINQ_API_TOKEN`. Register also needs `LINQ_WEBHOOK_URL` or `PUBLIC_WEBHOOK_URL`. `README.md` states the Telnyx live path has not been exercised from this repository.

`not shipped` means placeholder, phase 2, or out of scope. The function may exist and raise.

## did provision and did list

The ledger is the SQLite store.

| name | status | does | proof |
| --- | --- | --- | --- |
| `did provision` | shipped | Assigns one number per user through `Ledger.provision`. A later run for that user prints the same E.164. `StubTelnyxHttp` starts at `+15555550100` on each new instance, so a second user in a later CLI process can hit `UNIQUE(did)`. Distinct DIDs for two users are proven only when one `StubTelnyxHttp` is reused. | `call_agent/cli.py` `did_provision`. `call_agent/ledger.py` `Ledger.provision`. `call_agent/telnyx.py` `StubTelnyxHttp.search_and_order_number`. `tests/test_ledger.py` `test_provision_is_idempotent_and_users_get_distinct_dids`. `tests/test_cli.py` `test_did_provision_twice_prints_same_did` |
| `did provision --live` | gated | Orders a US voice number through `LiveTelnyxHttp.search_and_order_number` when `--live` is set and `_require_live_gate` plus `validate_live` pass. | `call_agent/cli.py` `did_provision`. `call_agent/telnyx.py` `LiveTelnyxHttp.search_and_order_number`. `call_agent/runtime.py` `validate_live`. `tests/test_cli.py` `test_did_provision_live_is_gated` |
| `did list` | shipped | Prints every ledger row as USER, DID, and STATUS. | `call_agent/cli.py` `did_list`. `call_agent/ledger.py` `Ledger.list`. `.cursor/skills/verify-call-agent/features/did-provision.md` |
| `Ledger` | shipped | Stores DID rows in SQLite table `dids` with `user_id TEXT PRIMARY KEY` and `did TEXT UNIQUE`. | `call_agent/ledger.py` `CREATE TABLE IF NOT EXISTS dids`. `docs/design.md` Shape, "Idempotent by shape." `tests/test_ledger.py` |

## quick-ask

CLI verb `quick-ask` drives `run_quick_ask`.

| name | status | does | proof |
| --- | --- | --- | --- |
| `quick-ask` | shipped | Runs one outbound call through `run_quick_ask` with `--to`, `--ask`, `--name`, and `--item` for stock and aisle. | `call_agent/cli.py` `quick_ask`. `call_agent/runtime.py` `run_quick_ask`. `tests/test_cli.py` `test_quick_ask_stub_prints_answered_result`. `.cursor/skills/verify-call-agent/features/quick-ask.md` |
| `quick-ask --live` | gated | Hosts FastAPI routes `/webhooks/telnyx` and `/media-stream` with `serve_in_process` and runs `run_quick_ask` when `--live` is set and `_require_live_gate` passes. | `call_agent/cli.py` `_run_request` `_require_live_gate`. `call_agent/server.py` `serve_in_process`. `call_agent/runtime.py` `run_quick_ask`. `.cursor/skills/verify-call-agent/features/quick-ask.md` |
| `Call` | shipped | Holds six tracks named dial, telnyx, media, live, conversation, and hangup, and `reduce` applies every transition. | `call_agent/domain.py` `Call` `reduce`. `docs/design.md` Shape, "The call is a product of tracks, reduced by one pure function." `tests/test_domain.py` |
| `HANGUP_MARK` | shipped | Sends one Telnyx mark named `hangup` after live closes, and `clean_hangup` requires `marks_sent == 1`. | `call_agent/domain.py` `HANGUP_MARK = "hangup"`. `call_agent/verify.py` `clean_hangup`. `tests/test_domain.py` `test_goodbye_audio_storm_sends_one_final_mark_after_live_closes_and_hangs_up_once`. `tests/test_runtime.py` `test_goodbye_audio_storm_hangs_up_through_one_final_mark_and_the_trail`. `tests/test_verify_quick_ask.py` `test_verify_passes_with_one_final_mark_when_goodbye_audio_storms` |
| `CLOSING_LINE` | shipped | `assess_transcript` joins agent text, sets `collapsed = " ".join(agent_text.split())`, and requires casefold containment of `Thanks, that's all I needed. Goodbye.`. | `call_agent/domain.py` `CLOSING_LINE` `assess_transcript`. `tests/test_domain.py` `test_split_closing_turns_still_verdict_and_enter_hangup_drain` |
| `HangupTrailElapsedEvent` | shipped | Schedules `HangupTrailElapsedEvent` after `hangup_trailing_silence_s` of 0.4 s on the final mark echo and hangs up with path trail, and a 10 s `HangupDrainTimeoutEvent` fails `clean_hangup`. | `call_agent/domain.py` `HangupTrailElapsedEvent` `HangupDrainTimeoutEvent`. `call_agent/config.py` `hangup_trailing_silence_s` `hangup_drain_timeout_s`. `call_agent/verify.py` `marks_sent == 1` `hangup_path == "trail"` last audio then trail then `call.hangup`. `docs/design.md` Shape, "The hangup waits for the goodbye." |

## Run directory

Each `quick-ask` run writes one run directory.

| name | status | does | proof |
| --- | --- | --- | --- |
| run directory | shipped | Writes `dial.json`, `session_start.json`, `events.jsonl`, `transcript.json`, `audio/inbound.wav`, `audio/outbound.wav`, `hangup.json`, `result.json`, `call_report.json`, and `call_report.redacted.json` under `CALL_AGENT_ARTIFACTS_DIR` in a directory named for `call_control_id`. | `call_agent/runtime.py` `RunDir`. `README.md`. `docs/design.md` Shape, "Artifacts are the proof." |
| `CallReport` | shipped | Records `timing_ms`, `hangup.path` as trail, drain_timeout, failure, or far_party, `counts.marks_sent`, and refs, and `result_artifact` checks that `CallReport` and those refs exist. | `call_agent/report.py` `CallReport` `HangupSummary.path` `Counts.marks_sent`. `call_agent/verify.py` `result_artifact`. `tests/test_verify_quick_ask.py` |
| `call_report.redacted.json` | shipped | Writes a `RedactedCallReport` sibling from `redact_call_report` after `call_report.json`. Phone fields are `[phone]`. The caller field is `[name]` or `None`. | `call_agent/report.py` `redact_call_report`. `call_agent/runtime.py` `RunDir.finish`. `tests/test_report.py` |

## verify-quick-ask

CLI verb `verify-quick-ask` calls `verify_quick_ask`.

| name | status | does | proof |
| --- | --- | --- | --- |
| `verify-quick-ask` | shipped | Runs five checks: `did_in_ledger`, `dial_payload`, `disclosure_first`, `clean_hangup`, and `result_artifact`. | `call_agent/cli.py` `verify_quick_ask_command`. `call_agent/verify.py` `verify_quick_ask`. `.github/workflows/ci.yml`. `tests/test_verify_quick_ask.py`. `tests/test_cli.py` `test_verify_quick_ask_stub_exits_zero`. `.cursor/skills/verify-call-agent/features/verify-quick-ask.md` |
| `verify-quick-ask --mode live` | gated | The CLI exits 2 unless `QUICK_ASK_LIVE=1`. `verify_quick_ask` still provisions with `StubTelnyxHttp` and does not call `serve_in_process`. | `call_agent/cli.py` `verify_quick_ask_command`. `call_agent/verify.py` `verify_quick_ask`. `tests/test_cli.py` `test_verify_quick_ask_live_requires_gate` `test_verify_quick_ask_live_names_missing_keys` |

## verify-call-agent

The Cursor skill drives the stub CLI verbs and never sets live keys.

| name | status | does | proof |
| --- | --- | --- | --- |
| `.cursor/skills/verify-call-agent/SKILL.md` | shipped | States stub drive rules and that the skill never drives live mode. | `.cursor/skills/verify-call-agent/SKILL.md` |
| `.cursor/skills/verify-call-agent/features/README.md` | shipped | Indexes `did-provision`, `quick-ask`, `verify-quick-ask`, and `verify-message` recipes. | `.cursor/skills/verify-call-agent/features/README.md` |
| `.cursor/skills/verify-call-agent/features/did-provision.md` | shipped | Covers first provision, repeat provision, a second user, and `did list`. | `.cursor/skills/verify-call-agent/features/did-provision.md` |
| `.cursor/skills/verify-call-agent/features/quick-ask.md` | shipped | Covers hours, stock, aisle, missing `--item`, artifacts, and the live gate. | `.cursor/skills/verify-call-agent/features/quick-ask.md` |
| `.cursor/skills/verify-call-agent/features/verify-quick-ask.md` | shipped | Covers the five stub checks, defaults, the live gate, and exit codes. | `.cursor/skills/verify-call-agent/features/verify-quick-ask.md` |
| `.cursor/skills/verify-call-agent/features/verify-message.md` | shipped | Covers the five MessagingPort checks and the register-webhook gate. | `.cursor/skills/verify-call-agent/features/verify-message.md` |
| `.cursor/skills/verify-call-agent/scripts/verify_stub.sh` | shipped | Runs one isolated stub drive of those recipes, including `linq register-webhook` exit 2 without a token. | `.cursor/skills/verify-call-agent/scripts/verify_stub.sh` |

## Live adapters

Live mode uses `LiveTelnyxHttp`, `LiveTelnyxMedia`, and `LiveGptLive`.

| name | status | does | proof |
| --- | --- | --- | --- |
| live Telnyx and OpenAI | gated | Defines `LiveTelnyxHttp`, `LiveTelnyxMedia`, and `LiveGptLive` for `--live` and `--mode live`. | `call_agent/telnyx.py` `LiveTelnyxHttp` `LiveTelnyxMedia`. `call_agent/gpt_live.py` `LiveGptLive`. `README.md` |

## MessagingPort and Linq

This section is the inventory for the current MessagingPort implementation. Update it when the port, adapter, verifier, or webhook path changes.

Telnyx owns PSTN calls. Linq Partner API V3 owns the peer iMessage kickoff thread. SMS stays off.

Sandbox outbound `message.parts` text from `+12138653024` to `+17272701288` landed on Builder's box. That prove is outside this repository. It is sandbox only, not product-live and not always-on inbound.

| name | status | does | proof |
| --- | --- | --- | --- |
| `MessagingPort` | shipped | Protocol `send` / `receive` over chat `parts`, not an SMS body. | `call_agent/messaging.py` `MessagingPort` `OutboundChat` `InboundMessage`. `tests/test_messaging.py` `test_stub_send_and_receive_round_trip` |
| Linq V3 outbound stub | shipped | `StubLinq.send` records `POST /chats` with `preferred_service: iMessage` and `message.parts` text. | `call_agent/linq.py` `serialize_outbound` `StubLinq`. `tests/test_messaging.py` `test_text_chat_is_parts_not_sms_body`. `call_agent/verify_message.py` `stub_send` |
| Linq V3 live send | gated | `LiveLinqHttp` posts the same JSON and fails loud without `LINQ_API_TOKEN`. `kickoff_from_settings` fails loud without `LINQ_FROM_E164`. This repository has not live-sent. Sandbox prove was on Builder's box. | `call_agent/linq.py` `LiveLinqHttp` `linq_from` `kickoff_from_settings`. `tests/test_messaging.py` `test_live_constructor_fails_without_token` `test_kickoff_from_settings_requires_from` `test_live_send_posts_chats_parts_shape`. `docs/linq.md` |
| Linq V3 inbound skeleton | shipped | Parses `message.received` (2026-02-03 and 2025-01-01), verifies Standard Webhooks signatures, ignores SMS. Stub-testable with no public URL. | `call_agent/linq.py` `parse_inbound` `verify_linq_signature`. `call_agent/server.py` `/webhooks/linq`. `tests/test_messaging.py` `test_signature_accepts_good_and_rejects_bad` `test_linq_webhook_route_verifies_signature` |
| `verify-message` | shipped | Five stub checks: send shape, inbound map, signature, live token gate, iMessage-only. CI runs the CLI verb. | `call_agent/verify_message.py` `verify_message`. `call_agent/cli.py` `verify_message_command`. `.github/workflows/ci.yml`. `tests/test_cli.py` `test_verify_message_stub_exits_zero`. `.cursor/skills/verify-call-agent/features/verify-message.md` |
| `linq register-webhook` | gated | `POST /webhook-subscriptions` after `LINQ_API_TOKEN` and `LINQ_WEBHOOK_URL` or `PUBLIC_WEBHOOK_URL` are set. Fails loud if either is missing. | `call_agent/cli.py` `linq_register_webhook`. `tests/test_cli.py` `test_linq_register_webhook_fails_without_token` `test_linq_register_webhook_fails_without_url` |
| always-on Linq webhook URL | not shipped | No public HTTPS URL is wired in this tree. That URL comes from the Cloudflare Workers track. Until it lands, poll `GET /api/partner/v3/chats/{chatId}/messages`. A temp tunnel is interim only. | `docs/linq.md` |
| SMS | not shipped | No SMS adapter. Outbound pins iMessage. Inbound SMS events are ignored. | `call_agent/linq.py` `serialize_outbound` `parse_inbound`. `tests/test_messaging.py` `test_receive_ignores_sms_and_non_message_events` |

## Design

`docs/design.md` Usage shows Day 1 quick-ask: `did provision`, `did list`, `quick-ask`, and `verify-quick-ask`. Messaging is a sibling port. See [linq.md](linq.md).

`README.md` states booking, hold queues, and packaging stay out of scope. SMS stays off.

`docs/design.md` Shape has "The hangup waits for the goodbye." and "Artifacts are the proof."

## Not shipped

| name | status | does | proof |
| --- | --- | --- | --- |
| `book_table` | not shipped | Raises `NotImplementedError` with `restaurant booking is phase 2` and has no CLI verb. | `call_agent/booking.py` `book_table` |
| hold queues | not shipped | Named out of scope in `README.md`, with no hold-queue module, and `build_instructions` includes `Never book, reserve, or hold anything.`. | `README.md`. `call_agent/domain.py` `build_instructions` |
| packaging | not shipped | Named out of scope in `README.md` and has no code. | `README.md` |
| host and providers | not shipped | No host or providers module exists under `call_agent/`. | `call_agent/` |
