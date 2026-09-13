from __future__ import annotations

import asyncio
import json

import typer

from .accounts import (
    AccountConflict,
    AccountWrite,
    open_accounts,
)
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
from .runtime import assert_live_dial_origins, run_call, validate_live
from .server import probe_live_ingress, serve_in_process
from .telnyx import LiveTelnyxHttp, StubTelnyxHttp, TelnyxHttp
from .verify import (
    BookVerifyInput,
    VerifyInput,
    format_report,
    verify_book,
    verify_quick_ask,
)
from .verify_linq_call import (
    LinqCallVerifyInput,
    format_linq_call_report,
    verify_linq_call,
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


app = typer.Typer(no_args_is_help=True, rich_markup_mode=None)
did_app = typer.Typer(no_args_is_help=True, rich_markup_mode=None)
account_app = typer.Typer(no_args_is_help=True, rich_markup_mode=None)
linq_app = typer.Typer(no_args_is_help=True, rich_markup_mode=None)
app.add_typer(did_app, name="did")
app.add_typer(account_app, name="account")
app.add_typer(linq_app, name="linq")

LIVE_GATE_MESSAGE = (
    "live mode is gated: set QUICK_ASK_LIVE=1 and the keys in .env.example"
)
STUB_FROM_DID = E164("+15555550100")


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


@account_app.command("upsert")
def account_upsert(
    phone: str = typer.Option(..., "--phone"),
    name: str | None = typer.Option(None, "--name"),
    from_did: str | None = typer.Option(None, "--from-did"),
    retain: bool = typer.Option(False, "--retain"),
    minutes: int = typer.Option(0, "--minutes"),
    user: str | None = typer.Option(None, "--user"),
    plan: str | None = typer.Option(None, "--plan"),
) -> None:
    settings = Settings()
    try:
        write = AccountWrite(
            phone=E164(phone),
            first_name=name,
            from_did=E164(from_did) if from_did is not None else None,
            user_id=UserId(user) if user is not None else None,
            plan=plan,
            minutes_included=minutes,
            retain_call_artifacts=retain,
        )
        result = open_accounts(settings).upsert(write)
    except (ValueError, PersistenceConfigError) as error:
        if isinstance(error, PersistenceConfigError):
            typer.echo(str(error), err=True)
            raise typer.Exit(code=2) from error
        if isinstance(error, AccountConflict):
            typer.echo(str(error), err=True)
            raise typer.Exit(code=1) from error
        raise typer.BadParameter(str(error)) from error
    typer.echo(json.dumps(result.record.model_dump(mode="json"), sort_keys=True))


@account_app.command("get")
def account_get(
    phone: str = typer.Option(..., "--phone"),
) -> None:
    settings = Settings()
    try:
        parsed = E164(phone)
        record = open_accounts(settings).get_by_phone(parsed)
    except PersistenceConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    except ValueError as error:
        raise typer.BadParameter(str(error), param_hint="--phone") from error
    if record is None:
        typer.echo(f"no account for {phone}", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(record.model_dump(mode="json"), sort_keys=True))


@account_app.command("list")
def account_list() -> None:
    settings = Settings()
    try:
        records = open_accounts(settings).list()
    except PersistenceConfigError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(
        f"{'USER':<16} {'PHONE':<16} {'NAME':<12} {'FROM_DID':<16} MINUTES RETAIN"
    )
    for record in records:
        typer.echo(
            f"{str(record.user_id):<16} "
            f"{str(record.phone):<16} "
            f"{(record.first_name or '-'):<12} "
            f"{str(record.from_did or '-'):<16} "
            f"{record.minutes_included:<7} "
            f"{'on' if record.retain_call_artifacts else 'off'}"
        )


@app.command("quick-ask")
def quick_ask(
    to: str = typer.Option(..., "--to"),
    ask: QuestionType = typer.Option(..., "--ask"),
    name: str = typer.Option(..., "--name"),
    item: str | None = typer.Option(None, "--item"),
    user: str = typer.Option("default", "--user"),
    live: bool = typer.Option(False, "--live"),
    from_did: str | None = typer.Option(None, "--from-did"),
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
        resolved_from = _resolve_from_did(
            settings, request.user_id, from_did, request.mode
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    try:
        result = asyncio.run(_run_request(request, settings, resolved_from))
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
    from_did: str | None = typer.Option(None, "--from-did"),
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
        resolved_from = _resolve_from_did(
            settings, request.user_id, from_did, request.mode
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    try:
        result = asyncio.run(_run_request(request, settings, resolved_from))
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


@app.command("verify-linq-call")
def verify_linq_call_command(
    to: str = typer.Option("+14155552671", "--to"),
    from_did: str = typer.Option("+15555550100", "--from-did"),
    text: str = typer.Option(
        "can you make a reservation with this restaurant at 7:30 "
        "for a party of two?",
        "--text",
    ),
) -> None:
    try:
        report = verify_linq_call(
            LinqCallVerifyInput(
                from_did=E164(from_did),
                to=E164(to),
                text=text,
            )
        )
    except ValueError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    typer.echo(format_linq_call_report(report))
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


def _resolve_from_did(
    settings: Settings,
    user_id: UserId,
    flag: str | None,
    mode: Mode,
) -> E164:
    if flag:
        return E164(flag)
    account = open_accounts(settings).get(user_id)
    if account is not None and account.from_did is not None:
        return account.from_did
    if mode is Mode.STUB:
        return STUB_FROM_DID
    raise ValueError(
        f"no from-DID for user {user_id}: pass --from-did or assign one with "
        "`call-agent account upsert --phone <iMessage> --from-did "
        "<Treena DID>`. Live calls never order a number."
    )


async def _run_request(
    request: CallRequest, settings: Settings, from_did: E164
) -> CallResult:
    if request.mode is Mode.STUB:
        return await run_call(request, settings, from_did=from_did)
    validate_live(settings)
    assert_live_dial_origins(settings)
    running = await serve_in_process(settings)
    try:
        await probe_live_ingress(
            settings.public_base_url,
            running.serve_token,
            settings.live_ingress_timeout_s,
        )
        return await run_call(request, settings, from_did=from_did)
    finally:
        await running.stop()
