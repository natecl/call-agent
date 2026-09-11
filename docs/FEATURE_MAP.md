# Feature map

This file lists what `main` implements today. It is not a plan.

How to run the CLI is in [README.md](../README.md). Why the call is a six-track reducer is in [design.md](design.md). The stub recipes an agent drives are in [`.cursor/skills/verify-call-agent/features/README.md`](../.cursor/skills/verify-call-agent/features/README.md).

## Status words

Three words appear in the tables below.

- shipped. Stub mode and the tests on `main` exercise the behavior. CI runs `verify-quick-ask` in stub mode on every push.
- gated. The live adapters and flags exist. They refuse to run unless `QUICK_ASK_LIVE=1` and the keys in `.env.example` are set. [README.md](../README.md) states that no live call against Telnyx or OpenAI has been run from this repository.
- not shipped. The repo names the item as phase 2, a placeholder, or out of scope.

## CLI verbs

`call-agent` is the Typer app `call_agent.cli:app` in `pyproject.toml`.

| name | status | does | proof |
| --- | --- | --- | --- |
| `did provision` | shipped | Records one Telnyx number per user in the ledger and prints the E.164. A second run for the same user returns the same number. `--live` is gated. | `call_agent/cli.py`, `call_agent/ledger.py`, `tests/test_cli.py`, `tests/test_ledger.py` |
| `did list` | shipped | Prints every ledger row as `USER`, `DID`, `STATUS`. | `call_agent/cli.py`, `.cursor/skills/verify-call-agent/features/did-provision.md` |
| `quick-ask` | shipped | Dials `--to`, asks `--ask` on behalf of `--name`, prints one `QuickAskResult` JSON object, and writes one run directory. `--ask stock` and `--ask aisle` require `--item`. `--live` is gated. | `call_agent/cli.py`, `call_agent/runtime.py`, `tests/test_cli.py`, `.cursor/skills/verify-call-agent/features/quick-ask.md` |
| `verify-quick-ask` | shipped | Provisions twice, runs one call, reads the ledger and the run directory, and prints five `[PASS]` or `[FAIL]` lines. Exits 0 only when all five pass. `--mode live` is gated. | `call_agent/verify.py`, `tests/test_verify_quick_ask.py`, `tests/test_cli.py`, `.github/workflows/ci.yml` |

There is no other CLI verb. `book_table` is not wired.

## Unique DID and ledger

`Ledger` owns the SQLite file at `CALL_AGENT_DB_PATH`. The `dids` table has `UNIQUE(user_id)` and `UNIQUE(did)`.

| name | status | does | proof |
| --- | --- | --- | --- |
| First provision | shipped | Inserts one row. Stub mode assigns `+15555550100`, then `+15555550101`, in order of first provision. | `call_agent/ledger.py`, `call_agent/telnyx.py`, `tests/test_ledger.py` |
| Repeat provision | shipped | Returns the active row when one exists. | `Ledger.provision`, `test_provision_is_idempotent_and_users_get_distinct_dids`, `test_did_provision_twice_prints_same_did` |
| Resume an in-flight order | shipped | If the row is `ordering`, `provision` calls `telnyx.get_order` and activates the number when the order has a DID. | `Ledger.provision` in `call_agent/ledger.py`, [design.md](design.md) section "Idempotent by shape" |
| Live order | gated | `--live` builds `LiveTelnyxHttp` after `QUICK_ASK_LIVE=1` and `validate_live`. Without `--live` the verb uses `StubTelnyxHttp`. | `did_provision` in `call_agent/cli.py`, `test_did_provision_live_is_gated` |
| On-demand provision | shipped | `run_quick_ask` calls `Ledger.provision` before it dials. | `call_agent/runtime.py`, `.cursor/skills/verify-call-agent/features/did-provision.md` |

## quick-ask dial, talk, and hangup

`run_quick_ask` owns one call. `reduce` in `call_agent/domain.py` is the only transition function. The call is six tracks: dial, Telnyx, media, GPT-Live, conversation, hangup. That shape is specified in [design.md](design.md) under "The call is a product of tracks".

| name | status | does | proof |
| --- | --- | --- | --- |
| Hours, stock, aisle | shipped | `QuestionType` is `hours`, `stock`, or `aisle`. `assess_transcript` reads the question table needles and never takes a verdict from the fake. | `Question.from_cli`, `QUESTION_TABLE`, `tests/test_cli.py`, `test_assess_transcript_waits_for_closing_then_answers_or_declines` |
| Disclosure first | shipped | `session.start` instructions name `on behalf of {caller}` before the question. The first agent transcript turn does too. | `build_instructions`, `DISCLOSURE_PHRASE`, `disclosure_first` in `call_agent/verify.py`, `test_build_instructions_puts_disclosure_before_ask` |
| `CLOSING_LINE` match | shipped | The constant is `Thanks, that's all I needed. Goodbye.` `assess_transcript` joins agent turns, collapses whitespace with `" ".join(agent_text.split())`, and checks that the collapsed text contains that line, ignoring case, before it returns a verdict. Split streamed turns still match. | `CLOSING_LINE`, `assess_transcript` in `call_agent/domain.py`, `test_split_closing_turns_still_verdict_and_enter_hangup_drain` |
| One final mark | shipped | After `session.close` and `LiveClosedEvent`, the reducer sends exactly one Telnyx mark named `hangup`. That name is the `HANGUP_MARK` constant. Chunks during the drain play with no mark. A second mark fails `clean_hangup`. | `HANGUP_MARK` in `call_agent/domain.py`, `test_goodbye_audio_storm_sends_one_final_mark_after_live_closes_and_hangs_up_once`, `test_goodbye_audio_storm_hangs_up_through_one_final_mark_and_the_trail`, `test_verify_passes_with_one_final_mark_when_goodbye_audio_storms` |
| Hangup trail | shipped | Telnyx echoes the `hangup` mark. The runtime waits `HANGUP_TRAILING_SILENCE_S`, default 0.4 s, then hangs up. `call_report.json` records `hangup.path` `trail`. | [design.md](design.md) section "The hangup waits for the goodbye", `clean_hangup` in `call_agent/verify.py`, `tests/test_runtime.py` |
| Drain timeout | shipped | If the echo never arrives, `HANGUP_DRAIN_TIMEOUT_S`, default 10 s, hangs up with `hangup.path` `drain_timeout`. `clean_hangup` fails. | `test_drain_timeout_hangs_up_anyway_and_journals_the_timeout`, `test_verify_fails_clean_hangup_when_playback_never_drains` |
| Stub ports | shipped | Stub Telnyx HTTP, Telnyx media, and GPT-Live replay raw JSON fixtures through the live parsers. No sockets. | `call_agent/telnyx.py`, `call_agent/gpt_live.py`, [README.md](../README.md) |
| Live bridge | gated | `--live` hosts FastAPI `/webhooks/telnyx` and `/media-stream` in process, then dials. | `serve_in_process` in `call_agent/server.py`, `_run_request` in `call_agent/cli.py` |

## Per-call artifacts

Each call writes only under `CALL_AGENT_ARTIFACTS_DIR/<call_control_id>/`. [design.md](design.md) calls this "Artifacts are the proof."

| file | status | does | proof |
| --- | --- | --- | --- |
| `dial.json` | shipped | The Call Control body as serialized, including `from`, `to`, `stream_url`, `stream_bidirectional_mode` `rtp`, and `stream_bidirectional_target_legs` `self`. | `dial_payload` in `call_agent/verify.py` |
| `session_start.json` | shipped | The GPT-Live `session.start` body, including instructions. | `disclosure_first` in `call_agent/verify.py` |
| `events.jsonl` | shipped | Reduction order. Every row has `seq` and `t_ms`. Event rows have `kind` and optional `transitions`. Effect rows have `effect`. | `call_agent/runtime.py`, [README.md](../README.md) section "Read a call" |
| `transcript.json` | shipped | Agent and business turns. | `QuickAskResult.transcript_ref` |
| `audio/inbound.wav`, `audio/outbound.wav` | shipped | L16 WAV, or raw PCMU. Stub inbound audio is silence. | `call_agent/audio.py`, `.cursor/skills/verify-call-agent/features/quick-ask.md` |
| `hangup.json` | shipped | The hangup request the runtime sent. | `clean_hangup` in `call_agent/verify.py` |
| `result.json` | shipped | A `QuickAskResult`. `answered` has `answer_text`. `declined`, `no_answer`, and `failed` pin it to null. | `QUICK_ASK_RESULT_ADAPTER`, `test_quick_ask_result_is_discriminated_and_answer_is_nonempty` |
| `call_report.json` | shipped | A `CallReport` with ids, `status`, `answer_text`, `reason`, `timing_ms`, hangup path and `final_mark`, silence and drain seconds, counts of marks, echoes, stale echoes, and audio after the verdict, and `refs`. Hangup `path` is `trail`, `drain_timeout`, `failure`, or `far_party`. | `call_agent/report.py`, `result_artifact` in `call_agent/verify.py` |

`verify-quick-ask` reads these files. It does not spy on mocks.

## verify-quick-ask checks

CI on every push is `call-agent verify-quick-ask --to +15555550123 --ask hours --name Nathan --mode stub` in `.github/workflows/ci.yml`.

| check | status | does | proof |
| --- | --- | --- | --- |
| `did_in_ledger` | shipped | The verify user has exactly one ledger row. Two provisions return the same DID, equal to `--from-did`. | `call_agent/verify.py` |
| `dial_payload` | shipped | `from`, `to`, a `wss://` `stream_url`, `stream_bidirectional_mode` `rtp`, `stream_bidirectional_target_legs` `self`, and matching stream codecs. | `call_agent/verify.py` |
| `disclosure_first` | shipped | Instructions place the disclosure before the ask, and the first agent turn speaks it. | `call_agent/verify.py` |
| `clean_hangup` | shipped | Verdict is `answered` or `declined`. Exactly one mark. Last audio, then the trail, then `call.hangup`. `hangup.path` is `trail`. A drain timeout or a second mark fails. | `call_agent/verify.py`, `tests/test_verify_quick_ask.py` |
| `result_artifact` | shipped | `result.json` matches the returned `QuickAskResult`. `call_report.json` is a valid `CallReport`. Every ref exists on disk. | `call_agent/verify.py` |

`--mode live` is gated the same way as `quick-ask --live`.

## verify-call-agent skill

[`.cursor/skills/verify-call-agent/SKILL.md`](../.cursor/skills/verify-call-agent/SKILL.md) drives the four CLI verbs in stub mode and keeps evidence under `artifacts/verify-call-agent/<RUN_ID>/`. It never sets live keys.

| file | status | does |
| --- | --- | --- |
| `.cursor/skills/verify-call-agent/features/README.md` | shipped | Index of user paths and proof rules. |
| `.cursor/skills/verify-call-agent/features/did-provision.md` | shipped | First provision, repeat, second user, `did list`. |
| `.cursor/skills/verify-call-agent/features/quick-ask.md` | shipped | Hours, stock, aisle, missing `--item`, artifacts, live gate. |
| `.cursor/skills/verify-call-agent/features/verify-quick-ask.md` | shipped | Five-check stub run, defaults, live gate, exit codes. |
| `.cursor/skills/verify-call-agent/scripts/verify_stub.sh` | shipped | One isolated stub drive of those recipes. |

## Alignment with design.md

Open questions in [design.md](design.md) are not shipped.

| design.md claim | status on `main` |
| --- | --- |
| Day 1 is quick-ask only | shipped. [README.md](../README.md) names booking, hold queues, packaging, and iMessage as out of scope. |
| Branded tokens and a closed `QuickAskResult` | shipped in `call_agent/domain.py` |
| Six-track `Call` and `reduce` | shipped in `call_agent/domain.py` |
| Hangup waits for the goodbye, one `HANGUP_MARK`, trail then hangup | shipped |
| Verdict from `assess_transcript`, never from the fake | shipped |
| Three ports, stubs replay raw JSON through the parsers | shipped |
| `CallHandle` and `CallBook` | shipped in `call_agent/runtime.py` |
| Artifacts plus `CallReport` are the proof | shipped |
| Idempotent `did provision` | shipped |
| `verify_quick_ask(VerifyInput(), workspace)` is the programmatic check | shipped |
| Tool-call verdict via GPT-Live delegation | not shipped. Named as a day-2 upgrade in design.md "Tradeoffs accepted". |
| Webhook signature verification with `TELNYX_PUBLIC_KEY` | not shipped. `.env.example` says leave it empty until signature checks are wired. |

## Not shipped

| name | status | what exists | proof |
| --- | --- | --- | --- |
| Restaurant booking | not shipped | `book_table` raises `NotImplementedError("restaurant booking is phase 2")`. No CLI verb. | `call_agent/booking.py` |
| Hold queues | not shipped | No module. GPT-Live instructions say never to book, reserve, or hold. | [README.md](../README.md), `build_instructions` in `call_agent/domain.py` |
| Packaging | not shipped | Named out of scope. No packaging code. | [README.md](../README.md) |
| iMessage | not shipped | Named out of scope. No iMessage code. | [README.md](../README.md) |

No host or providers layer exists in this repository.
