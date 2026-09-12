import json
from pathlib import Path

import pytest

from call_agent.config import Settings
from call_agent.domain import (
    BookRequest,
    E164,
    Failed,
    FirstName,
    Mode,
    Question,
    QuestionType,
    QuickAskRequest,
    UserId,
)
from call_agent.kickoff import (
    ASK_USAGE,
    BOOK_USAGE,
    KickoffMalformed,
    KickoffParsed,
    KickoffSession,
    kickoff_ack_text,
    kickoff_host_mismatch,
    make_session,
    malformed_reply_text,
    parse_kickoff,
    result_reply_text,
    run_kickoff,
)
from call_agent.linq import StubLinq, parse_inbound
from call_agent.messaging import IMESSAGE
from call_agent.verify_linq_call import (
    ASK_KICKOFF_TEXT,
    BOOK_KICKOFF_TEXT,
    kickoff_inbound_bytes,
    verify_linq_call,
)
from call_agent.verify_message import INBOUND_CHAT_ID, INBOUND_SENDER


def _settings(tmp_path: Path, **updates: object) -> Settings:
    values = dict(
        _env_file=None,
        linq_api_token=None,
        linq_from_e164="+15555550100",
        call_agent_db_path=tmp_path / "ledger.sqlite3",
        call_agent_artifacts_dir=tmp_path / "artifacts",
        hangup_trailing_silence_s=0,
        public_base_url="https://stub.invalid",
        quick_ask_live=False,
    )
    values.update(updates)
    settings = Settings(**values)
    settings.call_agent_artifacts_dir.mkdir(parents=True, exist_ok=True)
    return settings


def test_parse_book_literal_shape(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parsed = parse_kickoff(BOOK_KICKOFF_TEXT, settings)

    assert isinstance(parsed, KickoffParsed)
    assert parsed.verb == "book"
    assert parsed.request == BookRequest(
        user_id=UserId("default"),
        to=E164("+14155552671"),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
        party_size=2,
        time_text="Friday 7pm",
        constraints="outdoor if possible",
    )


def test_parse_quick_ask_hours(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parsed = parse_kickoff(ASK_KICKOFF_TEXT, settings)

    assert isinstance(parsed, KickoffParsed)
    assert parsed.request == QuickAskRequest(
        user_id=UserId("default"),
        to=E164("+14155552671"),
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
    )


def test_parse_ask_stock_item(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parsed = parse_kickoff(
        "ask +14155552671 stock oat milk name Nathan", settings
    )

    assert isinstance(parsed, KickoffParsed)
    assert isinstance(parsed.request, QuickAskRequest)
    assert parsed.request.question.kind is QuestionType.STOCK
    assert parsed.request.question.item == "oat milk"


def test_parse_uses_kickoff_caller_when_name_omitted(tmp_path: Path) -> None:
    settings = _settings(tmp_path, linq_kickoff_caller="Nathan")
    parsed = parse_kickoff(
        "book +14155552671 party 2 at Friday 7pm", settings
    )

    assert isinstance(parsed, KickoffParsed)
    assert parsed.request.caller == FirstName("Nathan")


def test_parse_missing_name_is_malformed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parsed = parse_kickoff(
        "book +14155552671 party 2 at Friday 7pm", settings
    )

    assert isinstance(parsed, KickoffMalformed)
    assert parsed.missing == ("name",)
    assert "Couldn't parse that." in malformed_reply_text(parsed)
    assert BOOK_USAGE in malformed_reply_text(parsed)


def test_parse_bad_party_is_malformed(tmp_path: Path) -> None:
    settings = _settings(tmp_path, linq_kickoff_caller="Nathan")
    parsed = parse_kickoff("book +14155552671 party two", settings)

    assert isinstance(parsed, KickoffMalformed)
    assert "party" in parsed.missing
    assert "at" in parsed.missing


def test_parse_non_kickoff_is_none(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert parse_kickoff("hello there", settings) is None
    assert parse_kickoff("reply", settings) is None


def test_parse_ask_missing_item_is_malformed(tmp_path: Path) -> None:
    settings = _settings(tmp_path, linq_kickoff_caller="Nathan")
    parsed = parse_kickoff("ask +14155552671 stock", settings)

    assert isinstance(parsed, KickoffMalformed)
    assert parsed.usage == ASK_USAGE


def test_result_reply_text_covers_statuses() -> None:
    to = E164("+14155552671")
    booked = BookRequest(
        user_id=UserId("default"),
        to=to,
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
        party_size=2,
        time_text="Friday 7pm",
    )
    del booked
    from call_agent.domain import Answered, Booked, Declined, NoAnswer

    assert (
        result_reply_text(
            Booked(confirmation_text="table for 2"), to
        )
        == "Booked: table for 2"
    )
    assert result_reply_text(Answered(answer_text="9 to 5"), to) == (
        "Answer: 9 to 5"
    )
    assert result_reply_text(Declined(), to) == "They declined."
    assert result_reply_text(NoAnswer(), to) == "No answer at +14155552671."
    assert (
        result_reply_text(Failed(reason="no tables"), to)
        == "Call failed: no tables"
    )


def test_kickoff_ack_text() -> None:
    book = BookRequest(
        user_id=UserId("default"),
        to=E164("+14155552671"),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
        party_size=2,
        time_text="Friday 7pm",
    )
    ask = QuickAskRequest(
        user_id=UserId("default"),
        to=E164("+14155552671"),
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
    )
    assert kickoff_ack_text(book) == (
        "Calling +14155552671 to book a table for 2 at Friday 7pm."
    )
    assert kickoff_ack_text(ask) == (
        "Calling +14155552671 to ask: What are your hours today?"
    )


def test_kickoff_host_mismatch_skips_stub() -> None:
    settings = Settings(
        _env_file=None,
        quick_ask_live=False,
        public_base_url="https://hidden-beacon-080.fly.dev",
    )
    assert kickoff_host_mismatch(settings, "testserver") is None


def test_kickoff_host_mismatch_names_hosts() -> None:
    settings = Settings(
        _env_file=None,
        quick_ask_live=True,
        public_base_url="https://hidden-beacon-080.fly.dev",
        public_webhook_url="https://hidden-beacon-080.fly.dev",
    )
    message = kickoff_host_mismatch(settings, "other.example")
    assert message is not None
    assert "hidden-beacon-080.fly.dev" in message
    assert "other.example" in message
    assert message.startswith("Call not placed:")


async def test_run_kickoff_cross_links_and_replies(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    inbound = parse_inbound(
        kickoff_inbound_bytes(BOOK_KICKOFF_TEXT, "evt-book-1")
    )
    assert inbound is not None
    parsed = parse_kickoff(BOOK_KICKOFF_TEXT, settings)
    assert isinstance(parsed, KickoffParsed)
    port = StubLinq()
    session = make_session(inbound, settings, port)

    receipt = await run_kickoff(session, parsed.request)

    assert receipt.result is not None
    assert receipt.result.status == "booked"
    assert len(port.sent) == 2
    rows = [
        json.loads(line)
        for line in (
            tmp_path / "artifacts" / "linq" / "evt-book-1" / "replies.jsonl"
        )
        .read_text()
        .splitlines()
    ]
    assert [row["kind"] for row in rows] == ["ack", "result"]
    assert rows[0]["chat_id"] == INBOUND_CHAT_ID
    call_link = json.loads(
        (tmp_path / "artifacts" / "linq" / "evt-book-1" / "call.json")
        .read_text()
    )
    linq_link = json.loads(
        (tmp_path / "artifacts" / "stub-call-0001" / "linq.json").read_text()
    )
    assert call_link["call_control_id"] == "stub-call-0001"
    assert linq_link["event_id"] == "evt-book-1"
    assert linq_link["chat_id"] == INBOUND_CHAT_ID


async def test_run_kickoff_live_preflight_fails_loud_without_sockets(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
        quick_ask_live=True,
        telnyx_api_key="k",
        telnyx_connection_id="c",
        openai_api_key="o",
        public_base_url="https://hidden-beacon-080.fly.dev",
        public_webhook_url=None,
    )
    inbound = parse_inbound(
        kickoff_inbound_bytes(BOOK_KICKOFF_TEXT, "evt-live-1")
    )
    assert inbound is not None
    request = BookRequest(
        user_id=UserId("default"),
        to=E164("+14155552671"),
        caller=FirstName("Nathan"),
        mode=Mode.LIVE,
        party_size=2,
        time_text="Friday 7pm",
    )
    port = StubLinq()
    session = make_session(inbound, settings, port)

    receipt = await run_kickoff(session, request)

    assert receipt.result is None
    assert receipt.error is not None
    assert "PUBLIC_WEBHOOK_URL" in receipt.error
    result_text = port.sent[-1][1]["message"]["parts"][0]["value"]
    assert result_text.startswith("Call not placed:")
    assert not (tmp_path / "artifacts" / "stub-call-0001").exists()


async def test_kickoff_session_ask_is_reserved(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    inbound = parse_inbound(
        kickoff_inbound_bytes(BOOK_KICKOFF_TEXT, "evt-ask-seam")
    )
    assert inbound is not None
    session = make_session(inbound, settings, StubLinq())
    with pytest.raises(NotImplementedError, match="mid-call chat"):
        await session.ask("can you take 8pm instead?")


class _RecordingPort:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def send(self, message):  # noqa: ANN001
        from call_agent.linq import STUB_CHAT_ID, STUB_MESSAGE_ID
        from call_agent.messaging import SendReceipt

        self.texts.append(message.parts[0].value)
        return SendReceipt(chat_id=STUB_CHAT_ID, message_id=STUB_MESSAGE_ID)

    def receive(self, raw: bytes):  # noqa: ANN001
        return None


async def test_run_kickoff_uses_session_text(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    inbound = parse_inbound(
        kickoff_inbound_bytes(BOOK_KICKOFF_TEXT, "evt-session-text")
    )
    assert inbound is not None
    parsed = parse_kickoff(BOOK_KICKOFF_TEXT, settings)
    assert isinstance(parsed, KickoffParsed)
    port = _RecordingPort()
    session = KickoffSession(
        inbound=inbound,
        from_did=E164("+15555550100"),
        port=port,
        settings=settings,
    )

    await run_kickoff(session, parsed.request)

    assert len(port.texts) == 2
    assert port.texts[0].startswith("Calling +14155552671")
    assert port.texts[1].startswith("Booked:")


def test_verify_linq_call_passes() -> None:
    report = verify_linq_call()

    assert report.passed
    assert [check.name for check in report.checks] == [
        "parse_book",
        "parse_quick_ask",
        "parse_fail_loud",
        "kickoff_reply",
        "call_ran",
        "cross_link",
        "same_host_dial",
    ]
    assert all(check.passed for check in report.checks)


def test_sender_constant_is_imessage_handle() -> None:
    assert INBOUND_SENDER.startswith("+")
    assert IMESSAGE == "iMessage"
