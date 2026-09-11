# Provision a number

`did provision` gives a user one Telnyx number and records it in the SQLite ledger. Running it again for the same user returns the same number. `did list` shows every user's number and its status. In stub mode the numbers are fake and start at `+15555550100`.

## Sub-features

- `did-first` provisions a number for a user with no ledger row.
- `did-repeat` returns the same number on a second run.
- `did-second-user` gives a different user a different number.
- `did-list` prints every row as `USER DID STATUS`.

## How to get to it (user POV)

- Run `call-agent did provision --user <user>` in a terminal.
- Run `call-agent did list` in a terminal.
- `quick-ask` provisions on demand when the user has no row.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. The ledger file does not exist yet.

- **First provision.** Run `.venv/bin/call-agent did provision --user default`. Exit code `0`. Stdout is one line, `+15555550100`.
- **Repeat provision.** Run the same command again. Exit code `0`. Stdout is `+15555550100`, the same number.
- **Second user.** Run `.venv/bin/call-agent did provision --user nathan`. Exit code `0`. Stdout is `+15555550101`.
- **List.** Run `.venv/bin/call-agent did list`. Exit code `0`. Stdout has a column-padded header row that starts with `USER` and two rows, `default` with `+15555550100` and `active`, then `nathan` with `+15555550101` and `active`.
- **Proof.** Keep the stdout of all four commands in `transcript.txt`. The repeat run's number equal to the first run's number is the idempotency proof.

## Gotchas

- A leftover ledger from a previous run makes the first provision return a number that is not `+15555550100`. Use a fresh `CALL_AGENT_DB_PATH`.
- Stub numbers are assigned in order of first provision, not by user name.
- Live provisioning orders a real number and costs money. This skill never runs it.
