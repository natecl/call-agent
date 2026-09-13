# Wave-1 FEATURE_MAP hold

Cap hold: the whole-product map, verifiers, and knowledge docs on this tree must match reality. This file is the hold plan, not a product change. Implementation lives in [`FEATURE_MAP.md`](FEATURE_MAP.md). CloudAgents start at [`../AGENTS.md`](../AGENTS.md).

This wave is docs + maps + verifiers. No Fly deploy. No L1 redial. No wave 2. No minutes ledger. Do not decide remaining Cap questions.

## Locked on this tree

| surface | lock |
| --- | --- |
| Recipe counts | `verify-quick-ask` **8**, `verify-book` **10**, `verify-persistence` **4**, `verify-accounts` **5**. README PASS lines match. |
| L1 iMessage | Stub shipped. `--live` gated. Cap recorded `live-prove: PASS (#34)`. qa owns live-prove sign-off ([TRE-19](https://linear.app/usetreena/issue/TRE-19/l1-wire-imessage-fly-call)). Do not mark live shipped. |
| Landing unfreeze | [TRE-22](https://linear.app/usetreena/issue/TRE-22/public-site-unfreeze-landing) ordered. Mostly call-agent-web. This repo keeps the closed-alpha iMessage line and does not claim a public landing. |
| Dashboard UI | Stays on Pages (web). |
| Fly accounts routes | `POST /accounts/sync` and `GET /accounts/dashboard` are **intended** Fly routes, shipped in tree. Not accidental. This tree does not `fly deploy`. |
| Hangup | Shipped: done-state + far-party, not phrase-gated. |
| Hangup wipe | Stub default wipe shipped. Live Fly `hidden-beacon-080` image still sha `ebcf350` (PR #35); secrets release **v22** only. `RETAIN_CALL_ARTIFACTS` unset (`None`). `settings.retain_call_artifacts=False`. `should_retain(request=None)=False`. **Prove-retain override OFF, wipe-unless-opted-in is the live default.** Account opt-in (`users.retain_call_artifacts`) still wins. Health 200. No Fly deploy. No L1 redial. |
| Name | Website `/start` collects the name. After signup that copy is in settings. Kickoff reads `users.first_name`. Never collect a name over iMessage. |
| `verify-accounts` | CloudAgent-runnable CLI verb. Not pytest-only. |
| `AGENTS.md` | Yes, this repo. |
| Worker 401 | Unset `TELNYX_PUBLIC_KEY` → 401 `missing_public_key`. Key present but signature missing/wrong → 401 `bad_signature`. Do not guess which secret is set live. |

## Still pending Cap (do not decide here)

- TRE-19 qa live-prove sign-off.
- Website writer live (TRE-20) and dashboard GO on Pages.
- Live book redial (HOLD REDIAL).
- Minutes ledger (not shipped).
- Telnyx caller ID: kickoff still requires per-account `users.from_did` (not unique). Cap said no per-user Treena DID. Do not invent a shared Telnyx from-number. The Linq sandbox bot `+12138653024` is the iMessage bot, not Telnyx `from`.
- Stripe live flip (Cap/ops blocked on Nathan live keys). Not this climb.

## Out of this wave

Fly deploy, L1 redial, wave 2, product invent, privacy-page copy, landing pixels.
