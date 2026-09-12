# Verify persistence

`verify-persistence` is the Neon and R2 port lever. It reads env, selects sqlite plus local when Neon and R2 are unset, exercises those stub ports, and prints three `[PASS]` or `[FAIL]` lines. It exits `0` only when all three pass. Partial `R2_*` or a bad `NEON_DATABASE_URL` exits `2` before any I/O. CI runs it with the cloud vars unset.

## Sub-features

- `persist-pass` prints three `[PASS]` lines and `3/3 passed` when Neon and R2 are unset.
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

- **CI command.** Run `.venv/bin/call-agent verify-persistence`. Exit code `0`. Stdout has exactly three lines that start with `[PASS]`, in this order: `config`, `ledger_roundtrip`, `artifact_put_get`. The `config` line contains `sqlite ledger + local artifacts`. The last line is `3/3 passed`.
- **Partial R2.** Run the same command with `R2_ACCOUNT_ID=acct` and the other `R2_*` vars unset. Exit code `2`. Stderr contains `R2 is misconfigured` and `R2_BUCKET`.
- **Proof.** Save stdout as `verify-persistence.txt` in the evidence directory.

## Gotchas

- The verb always exercises SQLite and a local directory for the two I/O checks. A complete Neon or R2 env changes only the `config` line. It does not open a cloud socket.
- `verify-quick-ask` still uses a throwaway SQLite workspace. Do not point it at Neon.
- A leftover `R2_ACCOUNT_ID` in the shell is enough to fail the lever. Unset the four required `R2_*` vars and `NEON_DATABASE_URL` before a stub drive.
