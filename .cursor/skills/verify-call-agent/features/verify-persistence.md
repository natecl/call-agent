# Verify persistence

`verify-persistence` is the Neon and R2 port lever. It reads env, selects sqlite plus local when Neon and R2 are unset, exercises those stub ports, and prints four `[PASS]` or `[FAIL]` lines. It exits `0` only when all four pass. Partial `R2_*` or a bad `NEON_DATABASE_URL` exits `2` before any I/O. CI runs it with the cloud vars unset.

## Sub-features

- `persist-pass` prints four `[PASS]` lines and `4/4 passed` when Neon and R2 are unset.
- `persist-exit` exits `0` on pass and `2` when the env is misconfigured.
- `persist-config` names `sqlite` plus `local` when unset, and `neon` plus `r2` when those vars are complete, while still exercising stubs.
- `persist-misconfig` refuses a partial `R2_*` set and a `NEON_DATABASE_URL` that is not a `postgresql://` URL.

## How to get to it (user POV)

- Run `call-agent verify-persistence` in a terminal.
- CI runs `call-agent verify-persistence` with `NEON_DATABASE_URL` and `R2_*` unset.
- `pytest tests/test_verify_persistence.py` calls the same function.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. This verb creates its own temporary workspace for the stub roundtrips. Leave `NEON_DATABASE_URL` and every `R2_*` var unset unless you are driving the misconfig path.

- **CI command.** Run `.venv/bin/call-agent verify-persistence`. Exit code `0`. Stdout has exactly four lines that start with `[PASS]`, in this order: `config`, `ledger_roundtrip`, `account_roundtrip`, `artifact_put_get`. The `config` line contains `sqlite ledger + local artifacts`. The last line is `4/4 passed`.
- **Partial R2.** Run the same command with `R2_ACCOUNT_ID=acct` and the other `R2_*` vars unset. Exit code `2`. Stderr contains `R2 is misconfigured` and `R2_BUCKET`.
- **Proof.** Save the passing stdout as `verify-persistence.txt` in the evidence directory. The partial-R2 run's stderr stays in `transcript.txt` and must contain `R2 is misconfigured`.

## Gotchas

- The verb always exercises SQLite and a local directory for the I/O checks. A complete Neon or R2 env changes only the `config` line. It does not open a cloud socket.
- `account_roundtrip` upserts a `users` row on the same SQLite file as `dids`.
- `verify-quick-ask` still uses a throwaway SQLite workspace. Do not point it at Neon.
- A leftover `R2_ACCOUNT_ID` in the shell is enough to fail the lever. Unset the four required `R2_*` vars and `NEON_DATABASE_URL` before a stub drive.
- Fly `hidden-beacon-080` needs `psycopg[binary]` when `NEON_DATABASE_URL` is set. pyproject lists that extra as a package dependency and the Dockerfile `pip install .` plus `import psycopg`. Live Fly sha `bb91810` (PR #39): health 200, Neon/psycopg baked. A machine-local `pip install` is wiped on redeploy.
