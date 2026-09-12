from __future__ import annotations

import asyncio
import json

import typer

from .config import Settings
from .domain import (
    BookRequest,
    CallRequest,
    CallResult,
    E164,
    FirstName,
    Mode,
    Question,
    QuestionType,
    QuickAskRequest,
    UserId,
)
from .ledger import open_ledger
from .linq import (
    LinqConfigError,
    LiveLinqHttp,
    resolve_webhook_url,
    validate_linq_live_token,
)
from .persistence import PersistenceConfigError
from .runtime import run_call, validate_live
from .server import serve_in_process
from .telnyx import LiveTelnyxHttp, StubTelnyxHttp, TelnyxHttp
from .verify import (
    BookVerifyInput,
    VerifyInput,
    format_report,
    verify_book,
    verify_quick_ask,
)
from .verify_message import (
    MessageVerifyInput,
    format_message_report,
    verify_message,
)
from .verify_persistence import format_persistence_report, verify_persistence
from .verify_webhooks import (
    VerifyWebhooksInput,
    format_webhooks_report,
    verify_webhooks,
)


app = typer.Typer(no_args_is_help=True)
did_app = typer.Typer(no_args_is_help=True)
linq_app = typer.Typer(no_args_is_help=True)
app.add_typer(did_app, name="did")
app.add_typer(linq_app, name="linq")

LIVE_GATE_MESSAGE = (
    "live mode is gated: set QUICK_ASK_LIVE=1 and the keys in .env.example"
)


@did_app.command("provision")
def did_provision(
    user: str = typer.Option("default", "--user"),
    live: bool = typer.Option(False, "--live"),
) -> None:
    settings = Settings()
    try:
        user_id = UserId(user)
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--user") from error
    telnyx: TelnyxHttp = StubTelnyxHttp()
    if live:
        _require_live_gate(settings)
        try:
            validate_live(settings)
        except ValueError as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(code=2) from error
        telnyx = LiveTelnyxHttp(settings.telnyx_api_key or "")
    try:
        record = open_ledger(settings).provision(
            user_id,
            telnyx,
            settings.telnyx_connection_id or "stub-connection",
        )
    except PersistenceConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    if record.did is None:
        typer.echo(
            f"DID order {record.order_id} is still pending at Telnyx; "
            "re-run did provision to resume it",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(record.did)


@did_app.command("list")
def did_list() -> None:
    settings = Settings()
    try:
        records = open_ledger(settings).list()
    except PersistenceConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(f"{'USER':<10} {'DID':<16} STATUS")
    for record in records:
        typer.echo(
            f"{record.user_id:<10} "
            f"{str(record.did or ''):<16} "
            f"{record.status}"
        )


@app.command("quick-ask")
def quick_ask(
    to: str = typer.Option(..., "--to"),
    ask: QuestionType = typer.Option(..., "--ask"),
    name: str = typer.Option(..., "--name"),
    item: str | None = typer.Option(None, "--item"),
    user: str = typer.Option("default", "--user"),
    live: bool = typer.Option(False, "--live"),
) -> None:
    settings = Settings()
    if live:
        _require_live_gate(settings)
    try:
        request = QuickAskRequest(
            user_id=UserId(user),
            to=E164(to),
            question=Question.from_cli(ask, item),
            caller=FirstName(name),
            mode=Mode.LIVE if live else Mode.STUB,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    try:
        result = asyncio.run(_run_request(request, settings))
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(json.dumps(result.model_dump(mode="json")))


@app.command("book")
def book(
    to: str = typer.Option(..., "--to"),
    name: str = typer.Option(..., "--name"),
    party: int = typer.Option(..., "--party"),
    time: str = typer.Option(..., "--time"),
    notes: str | None = typer.Option(None, "--notes"),
    user: str = typer.Option("default", "--user"),
    live: bool = typer.Option(False, "--live"),
) -> None:
    settings = Settings()
    if live:
        _require_live_gate(settings)
    try:
        request = BookRequest(
            user_id=UserId(user),
            to=E164(to),
            caller=FirstName(name),
            mode=Mode.LIVE if live else Mode.STUB,
            party_size=party,
            time_text=time,
            constraints=notes,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    try:
        result = asyncio.run(_run_request(request, settings))
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(json.dumps(result.model_dump(mode="json")))


@app.command("verify-quick-ask")
def verify_quick_ask_command(
    to: str = typer.Option("+14155552671", "--to"),
    ask: QuestionType = typer.Option(
        QuestionType.HOURS, "--ask"
    ),
    question: str = typer.Option(
        "What are your hours today?", "--question"
    ),
    name: str = typer.Option("Nathan", "--name"),
    from_did: str = typer.Option("+15555550100", "--from-did"),
    mode: Mode = typer.Option(Mode.STUB, "--mode"),
    user: str = typer.Option("verify", "--user"),
) -> None:
    settings = Settings()
    if mode is Mode.LIVE:
        _require_live_gate(settings)
    try:
        verify_input = VerifyInput(
            to=E164(to),
            question_type=ask,
            question_text=question,
            caller_first_name=FirstName(name),
            from_did=E164(from_did),
            mode=mode,
            user_id=UserId(user),
        )
        report = verify_quick_ask(verify_input)
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(format_report(report))
    if not report.passed:
        raise typer.Exit(code=1)


@app.command("verify-book")
def verify_book_command(
    to: str = typer.Option("+14155552671", "--to"),
    name: str = typer.Option("Nathan", "--name"),
    party: int = typer.Option(2, "--party"),
    time: str = typer.Option("Friday 7pm", "--time"),
    notes: str | None = typer.Option("outdoor if possible", "--notes"),
    from_did: str = typer.Option("+15555550100", "--from-did"),
    mode: Mode = typer.Option(Mode.STUB, "--mode"),
    user: str = typer.Option("verify", "--user"),
) -> None:
    settings = Settings()
    if mode is Mode.LIVE:
        _require_live_gate(settings)
    try:
        verify_input = BookVerifyInput(
            to=E164(to),
            caller_first_name=FirstName(name),
            party_size=party,
            time_text=time,
            constraints=notes,
            from_did=E164(from_did),
            mode=mode,
            user_id=UserId(user),
        )
        report = verify_book(verify_input)
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(format_report(report))
    if not report.passed:
        raise typer.Exit(code=1)


@app.command("verify-message")
def verify_message_command(
    to: str = typer.Option("+14155552671", "--to"),
    from_did: str = typer.Option("+15555550100", "--from-did"),
    text: str = typer.Option("kickoff", "--text"),
) -> None:
    try:
        report = verify_message(
            MessageVerifyInput(
                from_did=E164(from_did),
                to=E164(to),
                text=text,
            )
        )
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(format_message_report(report))
    if not report.passed:
        raise typer.Exit(code=1)


@app.command("verify-webhooks")
def verify_webhooks_command(
    url: str | None = typer.Option(
        None,
        "--url",
        help="Deployed Worker origin. Omit for the in-process stub.",
    ),
) -> None:
    try:
        report = verify_webhooks(
            VerifyWebhooksInput(url=url) if url else VerifyWebhooksInput()
        )
    except (ValueError, RuntimeError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(format_webhooks_report(report))
    if not report.passed:
        raise typer.Exit(code=1)


@linq_app.command("register-webhook")
def linq_register_webhook() -> None:
    settings = Settings()
    try:
        validate_linq_live_token(settings.linq_api_token)
        target_url = resolve_webhook_url(
            settings.linq_webhook_url,
            settings.public_webhook_url,
        )
    except LinqConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    subscription = LiveLinqHttp(settings.linq_api_token or "").register_webhook(
        target_url
    )
    typer.echo(f"subscription_id {subscription.subscription_id}")
    typer.echo(f"target_url {subscription.target_url}")
    typer.echo(f"signing_secret {subscription.signing_secret}")
    typer.echo(
        "store signing_secret as LINQ_WEBHOOK_SIGNING_SECRET; "
        "Linq will not return it again"
    )


@app.command("verify-persistence")
def verify_persistence_command() -> None:
    settings = Settings()
    try:
        report = verify_persistence(settings)
    except PersistenceConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(format_persistence_report(report))
    if not report.passed:
        raise typer.Exit(code=1)



def _require_live_gate(settings: Settings) -> None:
    if not settings.quick_ask_live:
        typer.echo(LIVE_GATE_MESSAGE, err=True)
        raise typer.Exit(code=2)


async def _run_request(
    request: CallRequest, settings: Settings
) -> CallResult:
    if request.mode is Mode.STUB:
        return await run_call(request, settings)
    running = await serve_in_process(settings)
    try:
        return await run_call(request, settings)
    finally:
        await running.stop()
