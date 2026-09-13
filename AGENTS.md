# Agent notes

This repository is `natecl/call-agent`. Product truth for this tree is [`docs/FEATURE_MAP.md`](docs/FEATURE_MAP.md). The wave-1 hold (map + verifiers must match reality, no false live claims) is [`docs/WAVE1_HOLD_FEATURE_MAP_PLAN.md`](docs/WAVE1_HOLD_FEATURE_MAP_PLAN.md). How to drive the CLI in stub mode is [`.cursor/skills/verify-call-agent/SKILL.md`](.cursor/skills/verify-call-agent/SKILL.md). Recipes live under [`.cursor/skills/verify-call-agent/features/`](.cursor/skills/verify-call-agent/features/README.md).

Update FEATURE_MAP and the matching `features/*.md` file in the same change that ships or changes a verb. A one-shot map edit without a recipe is not enough.

Do not `fly deploy`. Do not L1 redial. Do not invent product. Do not claim Pages/Fly live beyond what FEATURE_MAP already records as Cap/ops fact. Live hangup wipe: prove-retain override OFF; wipe-unless-opted-in is the live default; account opt-in still wins.

## Verify (stub)

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.cursor/skills/verify-call-agent/scripts/verify_stub.sh
.venv/bin/pytest -q
```

Levers: `verify-quick-ask` (8/8), `verify-book` (10/10), `verify-message` (6/6), `verify-linq-call` (14/14), `verify-webhooks` (5/5), `verify-persistence` (4/4), `verify-accounts` (5/5).
