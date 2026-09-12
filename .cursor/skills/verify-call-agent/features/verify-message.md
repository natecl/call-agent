# Verify message

`verify-message` is the MessagingPort + Linq V3 lever. It sends one stub chat `parts` text payload, maps a signed `message.received` fixture, replies on that chat with a boring ack via MessagingPort, writes inbound text into artifacts, rejects a bad signature, proves `LiveLinqHttp` fails without `LINQ_API_TOKEN`, and ignores SMS. It needs no token and no public URL. It exits `0` only when all six checks pass.

## Sub-features

- `verify-pass` prints six `[PASS]` lines and `6/6 passed` in stub mode.
- `verify-exit` exits `0` on pass and `1` on any failure.
- `verify-inputs` accepts `--to`, `--from-did`, and `--text`.
- `inbound-reply` maps `message.received`, sends `POST /chats/{chat_id}/messages` with `Received: {inbound text}`, and writes `inbound.json` whose `text` is the inbound body.
- `register-gate` is the sibling live verb `linq register-webhook`, which exits `2` without `LINQ_API_TOKEN` or `LINQ_WEBHOOK_URL`.

## How to get to it (user POV)

- Run `call-agent verify-message` in a terminal.
- CI runs `call-agent verify-message`.
- `pytest tests/test_messaging.py` and `tests/test_cli.py` call the same functions.

## Driving it with call-agent

Preconditions:

- Baseline env from `README.md`. This verb opens no sockets and writes no ledger. It may write under `CALL_AGENT_ARTIFACTS_DIR/linq/<event_id>/`.

- **CI command.** Run `.venv/bin/call-agent verify-message`. Exit code `0`. Stdout has exactly six lines that start with `[PASS]`, in this order: `stub_send`, `inbound_map`, `inbound_reply`, `signature`, `live_token`, `imessage_only`. The last line is `6/6 passed`.
- **Defaults.** Run `.venv/bin/call-agent verify-message`. Same six lines, exit code `0`.
- **Register gate.** Run `.venv/bin/call-agent linq register-webhook`. Exit code `2`. Stderr names `LINQ_API_TOKEN`.
- **Proof.** Save stdout as `verify-message.txt` in the evidence directory. The `inbound_reply` line names the inbound text and the ack.

## Gotchas

- This lever never hits `api.linqapp.com`. The closed-alpha outbound prove on the sandbox line (Builder's box) is not this command.
- The Workers URL is live and the Linq subscription is registered. The Worker forwards Linq POST to `APP_BASE_URL`. This lever proves HMAC verify + `message.received` map + MessagingPort reply locally with `LINQ_WEBHOOK_SIGNING_SECRET` unset. The box secret is not this command.
- SMS inbound is ignored on purpose. Do not treat an ignored SMS fixture as a failed parse of iMessage.
- Reply copy is a plain `Received: {text}` ack until a later voice lands. Do not treat a missing cute product line as a failed reply.
- Live inbound reply on Fly needs `LINQ_API_TOKEN`, `LINQ_FROM_E164`, `LINQ_WEBHOOK_SIGNING_SECRET`, and a real `CALL_AGENT_ARTIFACTS_DIR`. Missing artifact dir fails the webhook instead of returning `{ok:true}` without a send.
