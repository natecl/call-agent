# GPT-Live prompt

A stub `quick-ask` or `book` call writes `session_start.json` with the thin live prompt as `session.instructions` and Responses delegation under `session.delegation.responses`. Line 0 is role plus personality. Line 1 is the opener. Line 2 is the ask or the party, time, and constraints. The rest of the string keeps a short goodbye, the product guardrails, silence-continue, and a Delegation policy that names `record_task_result` and `end_call`. `speak_first` is a unit path. Stub mode records the opener and does not send the append frames.

## Sub-features

- `live-prompt` writes personality, opener, ask or facts, short goodbye, silence-continue, guardrails, and the Delegation policy labels into `session_start.json`.
- `speak-first` is proven by pytest. `LiveGptLive.speak_first` sends `session.instructions.append` then `session.commentary.append` with `delegation_id: None`, the exact opener, and "Speak first, then listen". It does not say `verbatim`.
- `realtime-reject` is proven by pytest. `parse_event` unwraps `response.event` and still raises on `session.created` and `input_audio_buffer.*` as well as top-level Realtime `response.*`.
- `responses-delegation` puts `session.delegation.responses` on `session.start` with `record_task_result` and `end_call`. Client-mode `session.delegation.created` is still answered in-process.
- `silence-continue` tells the model not to go quiet. The 20 s silence failsafe steers; it is not a spoken commentary nudge.

## How to get to it (user POV)

- Run `call-agent quick-ask` or `call-agent book` in stub mode, then read `session_start.json` in the call's artifact directory.
- Run `pytest tests/test_domain.py tests/test_wire.py` for the opener indexes, the append frames, the tool-result frames, and the Realtime reject.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`.
- `CALL_AGENT_ARTIFACTS_DIR` is empty.

- **Live prompt.** Run `.venv/bin/call-agent quick-ask --to +14155552671 --ask hours --name Nathan`. Exit code `0`. Open `session_start.json` under the call's artifact directory. `session.instructions` splitlines index 1 is `Hi, I'm calling on behalf of Nathan. I have one quick question.` Index 2 is `What are your hours today?`. The string contains `Delegation policy:`, `Backend tools:`, `Delegate to the backend when:`, `Do not delegate to the backend when:`, `record_task_result`, `end_call`, and `If the other party pauses`. It does not contain `Backchannel policy:` or `Interruption policy.`. `session.delegation.type` is `responses`.
- **Book prefix.** Run `.venv/bin/call-agent book --to +14155552671 --name Nathan --party 2 --time "Friday 7pm" --notes "outdoor if possible"`. Exit code `0`. `session.instructions` splitlines index 1 is `Hi, I'm calling on behalf of Nathan. I'd like to book a table.` Index 2 starts with `Party size: 2.`
- **speak_first and Realtime reject.** Run `.venv/bin/pytest -q tests/test_wire.py tests/test_domain.py`. Exit code `0`. The speak_first tests send instructions.append then commentary.append. The Realtime test raises on `session.created` and `input_audio_buffer.append`. Nested `response.event` is accepted.
- **Proof.** Copy `session_start.json` from the quick-ask artifact directory into the evidence directory. Keep the pytest exit code in `transcript.txt`.

## Gotchas

- `StubGptLive.speak_first` only records the opener. It does not emit the two append frames. Prove those frames with `tests/test_wire.py`, not with stub artifacts.
- Line 0 can also contain `on behalf of Nathan`. The stub and `disclosure_first` still require the opener on line 1 and the ask or facts on line 2.
- 16 kHz L16 passthrough is already matched. It is not the silence fix. Silence is the Responses tool loop plus app-owned `end_call`.
