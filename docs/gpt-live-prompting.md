# GPT-Live prompting

Source of truth: [Prompting GPT-Live](https://developers.openai.com/api/docs/guides/live-prompting) on developers.openai.com. This note records what V1 takes from that page and where it lands in the code. It does not restate the page. When the two disagree, the page wins and this note gets fixed.

## V1 position

V1 talks to `gpt-live-1` over the Live sessions WebSocket (`wss://api.openai.com/v1/live/sessions`) and prompts it the live-prompting way, not the Realtime way. `OPENAI_LIVE_MODEL` defaults to `gpt-live-1` in `config.py` and `.env.example`. The parser in `call_agent/gpt_live.py` rejects Realtime event shapes on purpose, so a Realtime-era prompt or event stream fails loud instead of half-working. `serialize_session_start` still sends no `delegation` object. V1 has no tool-calling backend.

## What the guide asks for, and what V1 does

- **Short live prompt.** `build_instructions` and `build_book_instructions` in `call_agent/domain.py` join one string. Line 0 is role plus personality (tone, pace, frustration). Line 1 is the opener (`first_spoken_line` or `first_book_line`). Line 2 is `question.text` or party, time, and constraints. `StubGptLive.start` reads those two indexes. After that come the existing closing line, the existing product guardrails (never book, never waitlist, two-sentence cap), and a `Delegation policy` block. `serialize_session_start` sends the string as `session.instructions` inside `session.start`. Per-question wording still comes from `QUESTION_TABLE`.
- **Greet with an append.** After `session.started`, `LiveGptLive.speak_first` sends `session.instructions.append` then `session.commentary.append`, both with `delegation_id: None`. Instructions name the exact opener and say speak first, then listen. They do not say `verbatim`. The reducer still emits the `speak_first` effect on `session.started`.
- **Model speech is not verbatim playback.** The guide says so. The `disclosure_first` check in `verify_quick_ask` therefore reads the agent's transcript for "on behalf of", not only the instructions we sent.
- **One exact-wording rule, kept on purpose.** The guide says to test which exact-wording rules survive a move from Realtime. V1 keeps one: `say exactly: Thanks, that's all I needed. Goodbye.` `assess_transcript` and `assess_book` keep listening until that line appears in the agent's transcript, so it is the terminal signal, not a style preference.
- **Honest Delegation policy, no tools.** The live prompt uses the guide's labels (`Delegation policy:`, `Backend tools:`, `Delegate to the backend when:`, `Do not delegate to the backend when:`). It lists no backend tools and tells the model never to delegate. It also says not to guess or promise a result the other party has not said. The verdict is still `assess_transcript` or `assess_book` over the transcript. A tool-call verdict via Responses delegation is the day-2 upgrade named in [design.md](design.md).
- **Optional controls stay out until a live transcript asks for them.** Backchannel, interruption, silence, and language rules are opt-in per the guide. V1 does not add them.
