from __future__ import annotations

import asyncio
import json

import typer

from .config import Settings
from .domain import (
    E164,
    FirstName,
    Mode,
    Question,
    QuestionType,
    QuickAskRequest,
    QuickAskResult,
    UserId,
)
from .ledger import Ledger
from .runtime import run_quick_ask, validate_live
from .server import serve_in_process
from .telnyx import LiveTelnyxHttp, StubTelnyxHttp, TelnyxHttp
from .verify import VerifyInput, format_report, verify_quick_ask


app = typer.Typer(no_args_is_help=True)
did_app = typer.Typer(no_args_is_help=True)
app.add_typer(did_app, name="did")

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
    record = Ledger(settings.call_agent_db_path).provision(
        user_id,
        telnyx,
        settings.telnyx_connection_id or "stub-connection",
    )
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
    records = Ledger(settings.call_agent_db_path).list()
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


def _require_live_gate(settings: Settings) -> None:
    if not settings.quick_ask_live:
        typer.echo(LIVE_GATE_MESSAGE, err=True)
        raise typer.Exit(code=2)


async def _run_request(
    request: QuickAskRequest, settings: Settings
) -> QuickAskResult:
    if request.mode is Mode.STUB:
        return await run_quick_ask(request, settings)
    running = await serve_in_process(settings)
    try:
        return await run_quick_ask(request, settings)
    finally:
        await running.stop()
