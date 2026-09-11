# Feature map

This file lists what `main` implements today. It is not a plan. How to run the CLI is in [README.md](../README.md). Why the call is a six-track reducer is in [design.md](design.md).

## Status

`shipped` means stub-proven on main. CI or pytest exercises it.

`gated` means live adapters and `--live` or `--mode live` exist in code. Live mode needs `QUICK_ASK_LIVE=1` plus keys listed in `.env.example`. `README.md` states the live path has not been exercised against Telnyx or OpenAI from this repository.

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

The Cursor skill drives the four CLI verbs in stub mode and never sets live keys.

| name | status | does | proof |
| --- | --- | --- | --- |
| `.cursor/skills/verify-call-agent/SKILL.md` | shipped | States stub drive rules and that the skill never drives live mode. | `.cursor/skills/verify-call-agent/SKILL.md` |
| `.cursor/skills/verify-call-agent/features/README.md` | shipped | Indexes `did-provision`, `quick-ask`, and `verify-quick-ask` recipes. | `.cursor/skills/verify-call-agent/features/README.md` |
| `.cursor/skills/verify-call-agent/features/did-provision.md` | shipped | Covers first provision, repeat provision, a second user, and `did list`. | `.cursor/skills/verify-call-agent/features/did-provision.md` |
| `.cursor/skills/verify-call-agent/features/quick-ask.md` | shipped | Covers hours, stock, aisle, missing `--item`, artifacts, and the live gate. | `.cursor/skills/verify-call-agent/features/quick-ask.md` |
| `.cursor/skills/verify-call-agent/features/verify-quick-ask.md` | shipped | Covers the five stub checks, defaults, the live gate, and exit codes. | `.cursor/skills/verify-call-agent/features/verify-quick-ask.md` |
| `.cursor/skills/verify-call-agent/scripts/verify_stub.sh` | shipped | Runs one isolated stub drive of those recipes. | `.cursor/skills/verify-call-agent/scripts/verify_stub.sh` |

## Live adapters

Live mode uses `LiveTelnyxHttp`, `LiveTelnyxMedia`, and `LiveGptLive`.

| name | status | does | proof |
| --- | --- | --- | --- |
| live Telnyx and OpenAI | gated | Defines `LiveTelnyxHttp`, `LiveTelnyxMedia`, and `LiveGptLive` for `--live` and `--mode live`. | `call_agent/telnyx.py` `LiveTelnyxHttp` `LiveTelnyxMedia`. `call_agent/gpt_live.py` `LiveGptLive`. `README.md` |

## Design

`docs/design.md` Usage shows Day 1 quick-ask: `did provision`, `did list`, `quick-ask`, and `verify-quick-ask`.

`README.md` states "Day 1 ships quick-ask only. Booking, hold queues, packaging, and iMessage are out of scope."

`docs/design.md` Shape has "The hangup waits for the goodbye." and "Artifacts are the proof."

## Not shipped

| name | status | does | proof |
| --- | --- | --- | --- |
| `book_table` | not shipped | Raises `NotImplementedError` with `restaurant booking is phase 2` and has no CLI verb. | `call_agent/booking.py` `book_table` |
| hold queues | not shipped | Named out of scope in `README.md`, with no hold-queue module, and `build_instructions` includes `Never book, reserve, or hold anything.`. | `README.md`. `call_agent/domain.py` `build_instructions` |
| packaging | not shipped | Named out of scope in `README.md` and has no code. | `README.md` |
| iMessage | not shipped | Named out of scope in `README.md` and has no code. | `README.md` |
| host and providers | not shipped | No host or providers module exists under `call_agent/`. | `call_agent/` |
