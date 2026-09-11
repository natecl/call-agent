# Verify message

`verify-message` is the MessagingPort + Linq V3 lever. It sends one stub chat `parts` text payload, maps a signed `message.received` fixture, rejects a bad signature, proves `LiveLinqHttp` fails without `LINQ_API_TOKEN`, and ignores SMS. It needs no token and no public URL. It exits `0` only when all five checks pass.

## Sub-features

- `verify-pass` prints five `[PASS]` lines and `5/5 passed` in stub mode.
- `verify-exit` exits `0` on pass and `1` on any failure.
- `verify-inputs` accepts `--to`, `--from-did`, and `--text`.
- `register-gate` is the sibling live verb `linq register-webhook`, which exits `2` without `LINQ_API_TOKEN` or `LINQ_WEBHOOK_URL`.

## How to get to it (user POV)

- Run `call-agent verify-message` in a terminal.
- CI runs `call-agent verify-message`.
- `pytest tests/test_messaging.py` and `tests/test_cli.py` call the same functions.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. This verb opens no sockets and writes no ledger.

- **CI command.** Run `.venv/bin/call-agent verify-message`. Exit code `0`. Stdout has exactly five lines that start with `[PASS]`, in this order: `stub_send`, `inbound_map`, `signature`, `live_token`, `imessage_only`. The last line is `5/5 passed`.
- **Defaults.** Run `.venv/bin/call-agent verify-message`. Same five lines, exit code `0`.
- **Register gate.** Run `.venv/bin/call-agent linq register-webhook`. Exit code `2`. Stderr names `LINQ_API_TOKEN`.
- **Proof.** Save stdout as `verify-message.txt` in the evidence directory.

## Gotchas

- This lever never hits `api.linqapp.com`. A sandbox outbound prove on Builder's box is not this command.
- Always-on inbound needs `LINQ_WEBHOOK_URL` from the Cloudflare Workers track plus `LINQ_WEBHOOK_SECRET` from `linq register-webhook`. Until then, poll `GET /api/partner/v3/chats/{chatId}/messages`.
- SMS inbound is ignored on purpose. Do not treat an ignored SMS fixture as a failed parse of iMessage.
