# GPT-Live prompt

A stub `quick-ask` or `book` call writes `session_start.json` with the live prompt as `session.instructions`. Line 0 is role plus personality. Line 1 is the opener. Line 2 is the ask or the party, time, and constraints. The rest of the string keeps `say exactly: Thanks, that's all I needed. Goodbye.`, the product guardrails, and an honest `Delegation policy` that lists no tools. `speak_first` is a unit path. Stub mode records the opener and does not send the append frames.

## Sub-features

- `live-prompt` writes personality, opener, ask or facts, closing line, silence-continue, guardrails, and the four Delegation policy labels into `session_start.json`.
- `speak-first` is proven by pytest. `LiveGptLive.speak_first` sends `session.instructions.append` then `session.commentary.append` with `delegation_id: None`, the exact opener, and "Speak first, then listen". It does not say `verbatim`.
- `realtime-reject` is proven by pytest. `parse_event` raises on `session.created` and `input_audio_buffer.*` as well as the older Realtime types.
- `no-tool-delegation` leaves `delegation` off `session.start` and lists no backend tools in the live prompt. Client-mode `session.delegation.created` is answered in-process.
- `silence-continue` tells the model not to go quiet waiting for a backend. The stall watchdog is a separate runtime path.

## How to get to it (user POV)

- Run `call-agent quick-ask` or `call-agent book` in stub mode, then read `session_start.json` in the call's artifact directory.
- Run `pytest tests/test_domain.py tests/test_wire.py` for the opener indexes, the append frames, and the Realtime reject.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`.
- `CALL_AGENT_ARTIFACTS_DIR` is empty.

- **Live prompt.** Run `.venv/bin/call-agent quick-ask --to +14155552671 --ask hours --name Nathan`. Exit code `0`. Open `session_start.json` under the call's artifact directory. `session.instructions` splitlines index 1 is `Hi, I'm calling on behalf of Nathan. I have one quick question.` Index 2 is `What are your hours today?`. The string contains `Delegation policy:`, `Backend tools:`, `Delegate to the backend when:`, `Do not delegate to the backend when:`, `no tool-calling backend`, `never pause or go quiet waiting for one`, `If the other party pauses`, and `say exactly: Thanks, that's all I needed. Goodbye.` It does not contain `Backchannel policy:`, `Interruption policy:`, or a `delegation` key next to `session`.
- **Book prefix.** Run `.venv/bin/call-agent book --to +14155552671 --name Nathan --party 2 --time "Friday 7pm" --notes "outdoor if possible"`. Exit code `0`. `session.instructions` splitlines index 1 is `Hi, I'm calling on behalf of Nathan. I'd like to book a table.` Index 2 starts with `Party size: 2.`
- **speak_first and Realtime reject.** Run `.venv/bin/pytest -q tests/test_wire.py tests/test_domain.py`. Exit code `0`. The speak_first tests send instructions.append then commentary.append. The Realtime test raises on `session.created` and `input_audio_buffer.append`.
- **Proof.** Copy `session_start.json` from the quick-ask artifact directory into the evidence directory. Keep the pytest exit code in `transcript.txt`.

## Gotchas

- `StubGptLive.speak_first` only records the opener. It does not emit the two append frames. Prove those frames with `tests/test_wire.py`, not with stub artifacts.
- Line 0 can also contain `on behalf of Nathan`. The stub and `disclosure_first` still require the opener on line 1 and the ask or facts on line 2.
- Do not add Responses `delegation` on `session.start` to make the policy look live. V1 has no tool backend. Client-mode `session.delegation.created` is answered with thinking plus commentary, not a tool.
