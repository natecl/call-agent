# Design: quick-ask call agent, day 1

## Problem

A CLI user pastes a business's E.164 number and asks one question (`hours`, `stock`, or `aisle`). The app dials from a Telnyx number that belongs to that user, says whose behalf it calls on, captures the answer, hangs up, and writes a result plus transcript and audio. Three transports feed one call and arrive in no fixed order: Telnyx Call Control webhooks, the Telnyx media WebSocket, and the OpenAI GPT-Live WebSocket. CI has no network and no keys, yet a stub run must exercise the same call logic a live run uses. The stack is fixed: Python 3.12, Pydantic v2, Typer, FastAPI for two routes, SQLite, pytest. The bridge mirrors [Telnyx's outbound GPT-Live recipe](https://telnyx.com/resources/outbound-ai-calls-python-openai-live).

## Usage (caller's view)

```console
$ call-agent did provision --user default
+15555550100
$ call-agent did list
USER      DID            STATUS
default   +15555550100   active
$ call-agent quick-ask --to +14155552671 --ask hours --name Nathan
{"status": "answered", "answer_text": "Monday through Friday, 9 AM to 5 PM.", "call_control_id": "stub-call-0001", "transcript_ref": "stub-call-0001/transcript.json", "audio_ref": "stub-call-0001/audio/inbound.wav"}
$ call-agent verify-quick-ask
[PASS] did_in_ledger      one row for user verify, from_did +15555550100
[PASS] dial_payload       from=+15555550100 to=+14155552671 stream_url=wss://... rtp/self
[PASS] disclosure_first   "on behalf of Nathan" at 41, ask at 118
[PASS] clean_hangup       verdict answered, one final mark echoed, trail, then hangup, then call.hangup
[PASS] result_artifact    result.json and call_report.json valid, every ref exists
5/5 passed
```

`quick-ask --ask stock` and `--ask aisle` also take `--item`. `--live` requires `QUICK_ASK_LIVE=1`, the keys in `.env.example`, and a public `PUBLIC_BASE_URL` that reaches this process. The programmatic caller and the pytest use the same two functions:

```python
result = await run_quick_ask(request, settings)
report = verify_quick_ask(VerifyInput(), workspace)
```

## Shape

**Branded tokens.** `E164`, `UserId`, `FirstName`, `CallControlId`, `StreamId`, `RunId` are `str` subclasses validated once at construction and accepted by Pydantic. Two phone numbers cannot be swapped by accident, and downstream code never re-validates. Per type-system-discipline.

**One closed result.** `QuickAskResult` is a discriminated union on `status`. `answered` requires `answer_text`. `declined`, `no_answer`, and `failed` pin it to `None`. Completed variants carry `transcript_ref` and `audio_ref`. The contradictory combination cannot be built.

**The call is a product of tracks, reduced by one pure function.** `Call` holds six small discriminated unions, one per fact that can advance on its own: dial, Telnyx call state, media stream, GPT-Live session, conversation, hangup. `reduce(call, event) -> (call, effects)` is the only place a transition happens. Each event advances one track, and a few guards read across tracks: open GPT-Live when Telnyx is answered and media is streaming, forward audio only when GPT-Live is active, drain the playback once the conversation is terminal and hang up once after it, finish when the hangup is confirmed or Telnyx ends the call. A media `start` that arrives before `call.answered` is a legal state, so the live race between the webhook and the socket needs no special case. Per model-the-domain.

**The hangup waits for the goodbye.** GPT-Live sends the closing line's transcript while it is still streaming that line's audio, and Telnyx plays what it has buffered in real time, so a hangup at the verdict cuts the last word. The verdict moves the hangup track to `HangupDraining`, the live track to `LiveClosing`, and emits `CloseLiveEffect`. GPT-Live flushes the audio it still holds and confirms with `session.closed`. Each flushed chunk plays without a mark. `LiveClosedEvent` moves the hangup track to `HangupMarked` and sends the one Telnyx `mark` frame, named `HANGUP_MARK`, behind the last chunk. Telnyx echoes a mark when the media before it has finished playing, so that echo is the playback-complete signal. It schedules `HangupTrailElapsedEvent` after `hangup_trailing_silence_s` (0.4 s), and the trail emits `HangupEffect` with `HangupRequested(via="trail")`. `ScheduleEffect(delay_s, event)` keeps the reducer clockless. The runtime performs it with `loop.call_later`. If no echo arrives, `HangupDrainTimeoutEvent` hangs up after `hangup_drain_timeout_s` (10 s) with `via="drain_timeout"`, the journal and `call_report.json` record it, and `verify-quick-ask` fails `clean_hangup`. Failures and a far-party hangup are not delayed. Both durations are `Settings` fields.

A first version sent a new mark behind every chunk during the drain and armed the trail only on the newest mark's echo. On the first live call GPT-Live kept streaming while its session stayed open, the newest mark moved 97 times, the trail never armed, and the callee hung up before the drain timeout. Closing the session at the verdict is what stops the target moving. `LiveGptLive.close` sends `session.close`, waits up to `close_timeout_s` for the server to flush and end the stream, then closes the socket, and its reader submits exactly one `LiveClosedEvent` whether or not the server sent `session.closed`.

**Verdict from evidence.** `assess_transcript(transcript, question) -> Verdict | None` is pure. It reads what the business said and what the agent said and returns `Answered(answer_text)` or `Declined`, or `None` to keep listening. Fakes supply transcript deltas. They never supply the verdict. The question table (`QuestionType` to default text, spoken ask, and answer needles) is the one place per-question policy lives.

**Three ports, fakes only there.** `TelnyxHttp` (numbers, dial, hangup), `TelnyxMedia` (frames and mark echoes in, playback and marks out), and `GptLive` (`session.start` out, events in) are Protocols. Wire JSON is a private Pydantic model inside `telnyx.py` and `gpt_live.py`. Parsers return domain events. Stub adapters replay raw JSON fixtures through those same parsers, so CI runs the production parsing code and zero network. Per boundary-discipline.

**One owner per call.** `CallHandle` owns one `Call`, one `asyncio.Queue` of events, the three ports, and the run directory. Its loop pops an event, reduces, performs the effects, and journals. `CallBook` maps `CallControlId` and `RunId` to a handle so the FastAPI routes can find the owner. The book stores owners, never call fields. Stub ports enqueue their scripted events with `put_nowait` inside the port call that triggers them, so the loop never waits and never sleeps. Per separate-before-serializing-shared-state.

**Artifacts are the proof.** Each run writes only under `CALL_AGENT_ARTIFACTS_DIR/<call_control_id>/`: `dial.json` and `session_start.json` as the boundary serialized them, `events.jsonl` in reduction order, `transcript.json`, `audio/inbound.wav` and `audio/outbound.wav` (L16) or `.pcmu`, `hangup.json`, `result.json`, `call_report.json`, and `call_report.redacted.json`. Every `events.jsonl` row carries `seq` and `t_ms`. Event rows list the tracks whose kind changed under `transitions`. Effect rows name the effect under `effect`, so the mark and the hangup sit in the same sequence as the events that caused them. `call_report.json` is a `CallReport`: ids, status, `timing_ms` per track transition and for the final mark echo, the hangup path (`trail`, `drain_timeout`, `failure`, or `far_party`) with the Telnyx cause and source, counts of marks, echoes, and audio chunks, and refs to every artifact. `RunDir.finish` also writes `call_report.redacted.json` from `redact_call_report`. `verify_quick_ask` reads the full `call_report.json` and the ledger. It does not spy on mocks. Per prove-it-works and build-the-lever.

**Idempotent by shape.** `did provision` returns the active row when one exists, resumes an `ordering` row against the Telnyx order, and only then orders a new number. `UNIQUE(user_id)` and `UNIQUE(did)` hold in SQLite. The reducer emits the hangup effect on exactly one edge, the move from `HangupDraining` to `HangupRequested`, and the Telnyx adapter treats "already ended" as success. Per make-operations-idempotent.

## Synthesis decision

Three runners were fanned out. Candidate 1 produced no design in time and was dropped. Candidate 2 (six-track aggregate, raw-fixture stubs, discriminated result) is the base. Candidate 3 (linear seven-phase fold, pre-parsed stub events) lost on two points: its phase chain drops a media `start` that arrives before `call.answered`, and its stubs bypass the parsers CI most needs to run. A cross-judge on a model that was not a runner scored 16 to 13 for candidate 2 and reached the same base.

Grafted from candidate 3: `CallHandle` plus `CallBook` in place of an actor, registry, and durable effect outbox; `str`-subclass branded tokens in place of `RootModel` wrappers; a typed `ClientState` with `encode` and `decode`; building the GPT-Live audio format from the media `start` frame rather than at plan time; `VerifyInput` defaults so the CLI verb and the pytest call the lever with no arguments; an explicit rejection of Realtime API shapes in the GPT-Live parser.

Deleted from candidate 2: the restart-replay effect outbox, `LifecycleEvidence`, `CallStage`, the eleven-method artifact store, and the pass-through `run_quick_ask` wrapper. Added: a `failed` result variant, a `--user` option (default `default`), and `--item` for `stock` and `aisle`.

## Tradeoffs accepted

- We accept six small unions on `Call` in exchange for never special-casing transport arrival order.
- We accept a heuristic `assess_transcript` in exchange for the fake never owning the verdict. A tool-call verdict via GPT-Live delegation is the day-2 upgrade.
- We accept raw L16 written as WAV and PCMU written raw in exchange for zero transcoding at 16 kHz. An L16 stream that Telnyx opens at 8 kHz is resampled 2x in the reducer so GPT-Live still runs at 16 kHz.
- We accept `quick-ask --live` hosting the FastAPI app in the same process in exchange for one command and no job service. `PUBLIC_BASE_URL` must route to it.
- We accept no mid-call crash resume. A crashed live call ends with Telnyx's own timeout.
- We accept up to `hangup_drain_timeout_s` of extra call time when Telnyx never echoes the final mark, in exchange for never cutting the closing line. The timeout is journaled and fails `verify-quick-ask`.

## Alternatives considered

- A linear phase state machine. Smaller, but it orders three independent transports and drops legal interleavings. It lost on correctness in live mode.
- A mutable `CallSession` with one method per socket message. Callers and stubs would learn the phase rules, and stub mode would grow a second script. It lost on interface depth.
- A shared SQLite call table written by both the CLI and the server. It exposes leases and polling that an in-process owner hides. It lost on complexity.

## Open questions and risks

- Which phrases should end a `stock` or `aisle` call without a false terminal decision? The needles live in one table and are the first thing to tune against live transcripts.
- Should webhook signature verification (`TELNYX_PUBLIC_KEY`) gate the live route before the first public tunnel test?
- The GPT-Live version header used by the launch recipe (`OpenAI-Alpha: quicksilver=v3`) may have changed. It is a config value, not code.
- The dial now sends `stream_bidirectional_sampling_rate: 16000` for L16. No live call has yet confirmed that Telnyx opens the media stream at 16 kHz in response. If it opens at 8 kHz, the resample path carries the call at narrowband quality.
- The first live call confirmed that Telnyx echoes `mark` on a bidirectional RTP stream opened by `dial`. No live call has yet confirmed that GPT-Live flushes the rest of the closing line after `session.close`. If it drops that audio instead, the last word is cut and `call_report.json` shows `output_audio_chunks_after_verdict` near zero.

## Next implementation step

`domain.py` first: tokens, `Call` with its six tracks, `reduce`, `assess_transcript`, and the question table, driven by a failing `tests/test_verify_quick_ask.py` that calls `verify_quick_ask` against an empty workspace.
