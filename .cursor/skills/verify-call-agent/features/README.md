# call-agent verification map

This directory is the standing feature map for call-agent. Each implementation keeps its own file current in the same change that ships or changes that verb. `docs/FEATURE_MAP.md` holds the same rows for this tree. A one-shot edit of `FEATURE_MAP.md` without a file here is not enough. There is no `call-agent-web` map in this repository.

Read this index before driving, then use the matching feature file as the recipe.

## Baseline preconditions

- The package is installed in `.venv` (`.venv/bin/pip install -e ".[dev]"`).
- `CALL_AGENT_DB_PATH` and `CALL_AGENT_ARTIFACTS_DIR` point under `/tmp/verify-call-agent-$RUN_ID/` so concurrent runs share nothing.
- `QUICK_ASK_LIVE` is `0` or unset, and `TELNYX_API_KEY`, `TELNYX_CONNECTION_ID`, `OPENAI_API_KEY`, `PUBLIC_BASE_URL`, `LINQ_API_TOKEN`, `LINQ_WEBHOOK_SIGNING_SECRET`, `LINQ_WEBHOOK_URL`, `PUBLIC_WEBHOOK_URL`, `NEON_DATABASE_URL`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, and `R2_ENDPOINT` are unset.
- No ledger row exists yet for `--user default`.

## Driving conventions

- Start every recipe from the baseline unless its preconditions say otherwise.
- Run verbs through `.venv/bin/call-agent`. Treat every flag and value as literal.
- Read state back through a second verb (`did list`) or through the files a verb wrote, not through Python internals.
- Keep the scratch directory until the recipe's proof step has copied what it needs.

## Proof and skip reporting

- CLI proof is the command, stdout, stderr, and exit code.
- Mutation proof includes a read-only second view of the stored value.
- Artifact proof includes the file path and its parsed content, not only its existence.
- Report an unreachable path with the attempted command and the unmet precondition. Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with call-agent` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

## Features

- [Provision a number](./did-provision.md) covers first provision, repeat provision, a second user, and listing.
- [Accounts](./accounts.md) covers `users` row seed (`account upsert --from-did` / `get` / `list`), omit-preserve on signed Fly sync, signed `GET /accounts/dashboard`, and `/start` name in settings.
- [Verify accounts](./verify-accounts.md) covers the five-check `users` lever in stub mode and its exit codes.
- [Ask a quick question](./quick-ask.md) covers a stub call for each question type, the result JSON, the artifacts, and the live gate.
- [GPT-Live prompt](./gpt-live-prompt.md) covers the live prompt lines in `session_start.json`, Responses delegation, `speak_first` append frames, and Realtime reject.
- [Verify quick-ask](./verify-quick-ask.md) covers the eight-check lever in stub mode and its exit codes.
- [Book a table](./book.md) covers a stub booking call, the result JSON, the artifacts, and the live gate.
- [Verify book](./verify-book.md) covers the ten-check lever in stub mode and its exit codes.
- [Verify message](./verify-message.md) covers the MessagingPort + Linq V3 stub lever, inbound reply artifacts, and the register-webhook gate.
- [Always-on webhooks versus local tunnel](./webhooks.md) covers the Worker prod path at `https://call-agent-webhooks.usetreena.workers.dev/linq/webhook`, the laptop tunnel, `PUBLIC_WEBHOOK_URL`, and the Linq hook.
- [Linq inbound (closed alpha)](./linq-inbound.md) covers the iMessage L1 ingress hook. SMS stays off.
- [Linq to call (closed alpha)](./linq-to-call.md) covers iMessage natural collect + confirm-before-dial → Fly `run_call` → result on the same chat.
- [Verify linq-to-call](./verify-linq-call.md) covers the fourteen-check stub lever for that path.
- [Verify webhooks](./verify-webhooks.md) covers the always-on Worker lever in stub mode and against a deployed URL.
- [Verify persistence](./verify-persistence.md) covers the Neon and R2 port lever, the SQLite and local fallback, and the misconfig exit.
- [Default wipe at hangup](./privacy-wipe.md) covers write-then-delete after hangup unless retention is on. Live Fly: prove-retain override OFF; wipe-unless-opted-in is the live default. The privacy page stays not shipped.
