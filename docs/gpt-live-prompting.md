# GPT-Live prompting

Source of truth: [Prompting GPT-Live](https://developers.openai.com/api/docs/guides/live-prompting) on developers.openai.com. This note records what V1 takes from that page and where it lands in the code. It does not restate the page. When the two disagree, the page wins and this note gets fixed.

## V1 position

V1 talks to `gpt-live-1` over the Live sessions WebSocket (`wss://api.openai.com/v1/live/sessions`) and prompts it the live-prompting way, not the Realtime way. `OPENAI_LIVE_MODEL` defaults to `gpt-live-1` in `config.py` and `.env.example`. The parser in `call_agent/gpt_live.py` rejects Realtime event shapes on purpose, so a Realtime-era prompt or event stream fails loud instead of half-working.

## What the guide asks for, and what V1 does

- **Short live prompt; procedures belong in the backend.** `build_instructions` in `call_agent/domain.py` is five lines: role and caller, the opening line, the one question, the closing line, and two guardrails (never book, keep turns under two sentences). `serialize_session_start` in `call_agent/gpt_live.py` sends it as `session.instructions` inside `session.start`. Per-question wording comes from `QUESTION_TABLE`, the one place per-question policy lives.
- **Greet the callee with an append.** The guide's pattern for speaking before the other party does is a `session.instructions.append` carrying the exact opening line, followed by a short `session.commentary.append`. `LiveGptLive.speak_first` sends both, driven by the `speak_first` effect the reducer emits on `session.started`.
- **Model speech is not verbatim playback.** The guide says so. The `disclosure_first` check in `verify_quick_ask` therefore reads the agent's transcript for "on behalf of", not only the instructions we sent.
- **One exact-wording rule, kept on purpose.** The guide says to test which exact-wording rules survive a move from Realtime. V1 keeps one: `say exactly: Thanks, that's all I needed. Goodbye.` `assess_transcript` keeps listening until that line appears in the agent's transcript, so it is the terminal signal, not a style preference.
- **No delegation block.** The guide's template centres on a `Delegation policy` that hands reasoning and tools to a backend. V1 has no backend; the verdict is `assess_transcript` over the transcript. A tool-call verdict via delegation is the day-2 upgrade named in [design.md](design.md).
- **Optional controls stay out until a live transcript asks for them.** Backchannel, interruption, silence, and language rules are opt-in per the guide. Add them one at a time against real calls, not ahead of them.
