import json
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from call_agent.accounts import UserRecord
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
    CONFIRM_ASK_TEXT,
    DIAL_NOW_TEXT,
    NO_ACCOUNT_TEXT,
    NO_FROM_DID_TEXT,
    NO_NAME_TEXT,
    PHONE_ASK_TEXT,
    TIME_ASK_TEXT,
    KickoffBlocked,
    KickoffCollecting,
    KickoffFailed,
    KickoffParsed,
    KickoffSession,
    PendingCollect,
    collect_reply_text,
    failed_reply_text,
    intake_kickoff as _intake_kickoff,
    kickoff_ack_text,
    kickoff_host_mismatch,
    load_pending_collect,
    make_session,
    parse_kickoff as _parse_kickoff,
    pending_collect_path,
    result_reply_text,
    run_kickoff,
    save_pending_collect,
)
from call_agent.linq import StubLinq, parse_inbound
from call_agent.messaging import IMESSAGE
from call_agent.verify_linq_call import (
    APPROVE_FOLLOWUP_TEXT,
    ASK_KICKOFF_TEXT,
    NATURAL_BOOK_TEXT,
    ONESHOT_BOOK_TEXT,
    PARTY_FOLLOWUP_TEXT,
    PHONE_FOLLOWUP_TEXT,
    POKE_PALACE_BOOK_TEXT,
    TIME_FOLLOWUP_TEXT,
    kickoff_inbound_bytes,
    verify_linq_call,
)
from call_agent.verify_message import INBOUND_CHAT_ID, INBOUND_SENDER


def _account(**updates: object) -> UserRecord:
    values: dict[str, object] = dict(
        user_id=UserId("acctnathan"),
        phone=E164("+14155552671"),
        first_name="Nathan",
        from_did=E164("+15555550177"),
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    values.update(updates)
    return UserRecord.model_validate(values)


def intake_kickoff(
    text: str,
    settings: Settings,
    pending: object = None,
    *,
    account: UserRecord | None | bool = True,
) -> object:
    resolved = _account() if account is True else account
    return _intake_kickoff(text, settings, pending=pending, account=resolved)


def parse_kickoff(
    text: str,
    settings: Settings,
    *,
    account: UserRecord | None | bool = True,
) -> object:
    resolved = _account() if account is True else account
    return _parse_kickoff(text, settings, account=resolved)


def _settings(tmp_path: Path, **updates: object) -> Settings:
    values = dict(
        _env_file=None,
        linq_api_token=None,
        linq_from_e164="+15555550100",
        call_agent_db_path=tmp_path / "ledger.sqlite3",
        call_agent_artifacts_dir=tmp_path / "artifacts",
    )
    values.update(updates)
    settings = Settings(**values)
    settings.call_agent_artifacts_dir.mkdir(parents=True, exist_ok=True)
    return settings


def test_parse_natural_book_asks_only_for_phone(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parsed = intake_kickoff(NATURAL_BOOK_TEXT, settings)

    assert isinstance(parsed, KickoffCollecting)
    assert parsed.reply == PHONE_ASK_TEXT
    assert parsed.reply == (
        "okay, sounds good. What's the restaurant's phone number?"
    )
    assert parsed.pending.waiting_for == "to"
    assert parsed.pending.party_size == 2
    assert parsed.pending.time_text == "7:30"
    assert parsed.pending.to is None
    assert not (tmp_path / "artifacts" / "stub-call-0001").exists()


def test_phone_followup_asks_to_confirm(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = intake_kickoff(NATURAL_BOOK_TEXT, settings)
    assert isinstance(first, KickoffCollecting)

    parsed = intake_kickoff(
        PHONE_FOLLOWUP_TEXT, settings, pending=first.pending
    )

    assert isinstance(parsed, KickoffCollecting)
    assert parsed.reply == CONFIRM_ASK_TEXT
    assert parsed.reply == (
        'Okay, I\'m going to call this number. Do you confirm? Reply "approve"'
    )
    assert parsed.pending.waiting_for == "confirm"
    assert parsed.pending.to == "+14155552671"
    assert not (tmp_path / "artifacts" / "stub-call-0001").exists()


def test_approve_after_phone_completes_book_request(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = intake_kickoff(NATURAL_BOOK_TEXT, settings)
    assert isinstance(first, KickoffCollecting)
    phone = intake_kickoff(
        PHONE_FOLLOWUP_TEXT, settings, pending=first.pending
    )
    assert isinstance(phone, KickoffCollecting)

    parsed = intake_kickoff(
        APPROVE_FOLLOWUP_TEXT, settings, pending=phone.pending
    )

    assert isinstance(parsed, KickoffParsed)
    assert parsed.request == BookRequest(
        user_id=UserId("acctnathan"),
        to=E164("+14155552671"),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
        party_size=2,
        time_text="7:30",
    )


def test_confirm_accepts_yes_and_i_confirm(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = intake_kickoff(NATURAL_BOOK_TEXT, settings)
    assert isinstance(first, KickoffCollecting)
    phone = intake_kickoff(
        PHONE_FOLLOWUP_TEXT, settings, pending=first.pending
    )
    assert isinstance(phone, KickoffCollecting)

    yes = intake_kickoff("yes", settings, pending=phone.pending)
    i_confirm = intake_kickoff("I confirm", settings, pending=phone.pending)

    assert isinstance(yes, KickoffParsed)
    assert isinstance(i_confirm, KickoffParsed)
    assert yes.request.to == E164("+14155552671")
    assert i_confirm.request.to == E164("+14155552671")


def test_first_message_never_dials(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parsed = parse_kickoff(ONESHOT_BOOK_TEXT, settings)

    assert isinstance(parsed, KickoffCollecting)
    assert parsed.pending.waiting_for == "confirm"
    assert parsed.reply == CONFIRM_ASK_TEXT
    assert not isinstance(parsed, KickoffParsed)
    assert not (tmp_path / "artifacts" / "stub-call-0001").exists()


def test_parse_quick_ask_hours(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = parse_kickoff(ASK_KICKOFF_TEXT, settings)

    assert isinstance(first, KickoffCollecting)
    assert first.pending.waiting_for == "confirm"
    assert first.pending.question_kind == "hours"
    parsed = intake_kickoff(
        APPROVE_FOLLOWUP_TEXT, settings, pending=first.pending
    )
    assert isinstance(parsed, KickoffParsed)
    assert parsed.request == QuickAskRequest(
        user_id=UserId("acctnathan"),
        to=E164("+14155552671"),
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
    )


def test_parse_ask_stock_item(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = parse_kickoff(
        "ask +14155552671 stock oat milk name Nathan", settings
    )

    assert isinstance(first, KickoffCollecting)
    parsed = intake_kickoff(
        APPROVE_FOLLOWUP_TEXT, settings, pending=first.pending
    )
    assert isinstance(parsed, KickoffParsed)
    assert isinstance(parsed.request, QuickAskRequest)
    assert parsed.request.question.kind is QuestionType.STOCK
    assert parsed.request.question.item == "oat milk"


def test_parse_uses_account_name_when_text_omits_name(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = parse_kickoff(
        "book +14155552671 party 2 at Friday 7pm", settings
    )

    assert isinstance(first, KickoffCollecting)
    parsed = intake_kickoff(
        APPROVE_FOLLOWUP_TEXT, settings, pending=first.pending
    )
    assert isinstance(parsed, KickoffParsed)
    assert parsed.request.caller == FirstName("Nathan")


def test_no_account_replies_plain_and_does_not_dial(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parsed = parse_kickoff(
        "book +14155552671 party 2 at Friday 7pm",
        settings,
        account=None,
    )

    assert isinstance(parsed, KickoffBlocked)
    assert parsed.reply == NO_ACCOUNT_TEXT
    assert not parsed.reply.startswith("Call failed:")
    assert "missing caller name" not in parsed.reply
    assert not (tmp_path / "artifacts" / "stub-call-0001").exists()


def test_no_name_replies_plain_and_does_not_dial(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parsed = parse_kickoff(
        "book +14155552671 party 2 at Friday 7pm",
        settings,
        account=_account(first_name=None),
    )

    assert isinstance(parsed, KickoffBlocked)
    assert parsed.reply == NO_NAME_TEXT
    assert not parsed.reply.startswith("Call failed:")
    assert "missing caller name" not in parsed.reply
    assert not (tmp_path / "artifacts" / "stub-call-0001").exists()


def test_name_token_in_text_is_ignored(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = parse_kickoff(
        "book +14155552671 party 2 at Friday 7pm name Alice",
        settings,
        account=_account(first_name="Nathan"),
    )
    assert isinstance(first, KickoffCollecting)
    parsed = intake_kickoff(
        APPROVE_FOLLOWUP_TEXT, settings, pending=first.pending
    )
    assert isinstance(parsed, KickoffParsed)
    assert parsed.request.caller == FirstName("Nathan")


def test_account_retain_flag_reaches_request(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = parse_kickoff(
        "book +14155552671 party 2 at Friday 7pm",
        settings,
        account=_account(retain_call_artifacts=True),
    )
    assert isinstance(first, KickoffCollecting)
    parsed = intake_kickoff(
        APPROVE_FOLLOWUP_TEXT,
        settings,
        pending=first.pending,
        account=_account(retain_call_artifacts=True),
    )
    assert isinstance(parsed, KickoffParsed)
    assert parsed.request.retain_call_artifacts is True


def test_no_from_did_replies_plain_and_does_not_dial(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parsed = parse_kickoff(
        "book +14155552671 party 2 at Friday 7pm",
        settings,
        account=_account(from_did=None),
    )

    assert isinstance(parsed, KickoffBlocked)
    assert parsed.reply == NO_FROM_DID_TEXT
    assert not parsed.reply.startswith("Call failed:")
    assert "missing caller name" not in parsed.reply
    assert not (tmp_path / "artifacts" / "stub-call-0001").exists()


def test_no_missing_caller_name_path() -> None:
    root = Path(__file__).resolve().parents[1] / "call_agent"
    hits = []
    for path in root.rglob("*.py"):
        if path.name.startswith("verify"):
            continue
        text = path.read_text()
        if (
            "missing caller name" in text
            or "LINQ_KICKOFF_CALLER" in text
            or "linq_kickoff_caller" in text
            or "LINQ_KICKOFF_USER_ID" in text
            or "linq_kickoff_user_id" in text
            or "TELNYX_FROM_NUMBER" in text
            or "telnyx_from_number" in text
        ):
            hits.append(path)
    assert hits == []


def test_missing_time_asks_for_time_not_phone(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parsed = intake_kickoff(
        "can you make a reservation for a party of two?", settings
    )

    assert isinstance(parsed, KickoffCollecting)
    assert parsed.reply == TIME_ASK_TEXT
    assert parsed.pending.waiting_for == "time"
    assert parsed.pending.party_size == 2
    assert parsed.reply != PHONE_ASK_TEXT


def test_missing_party_and_time_does_not_ask_for_phone(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parsed = intake_kickoff("can you make a reservation?", settings)

    assert isinstance(parsed, KickoffCollecting)
    assert parsed.pending.waiting_for == "party_and_time"
    assert parsed.reply == collect_reply_text("party_and_time")
    assert parsed.reply != PHONE_ASK_TEXT


def test_at_restaurant_does_not_invent_time(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parsed = intake_kickoff(POKE_PALACE_BOOK_TEXT, settings)

    assert isinstance(parsed, KickoffCollecting)
    assert parsed.pending.waiting_for == "party_and_time"
    assert parsed.reply == collect_reply_text("party_and_time")
    assert parsed.pending.party_size is None
    assert parsed.pending.time_text is None
    assert parsed.reply != PHONE_ASK_TEXT
    assert parsed.reply != TIME_ASK_TEXT


def test_party_only_followup_asks_for_time_not_phone(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = intake_kickoff(POKE_PALACE_BOOK_TEXT, settings)
    assert isinstance(first, KickoffCollecting)

    parsed = intake_kickoff(
        PARTY_FOLLOWUP_TEXT, settings, pending=first.pending
    )

    assert isinstance(parsed, KickoffCollecting)
    assert parsed.pending.waiting_for == "time"
    assert parsed.reply == TIME_ASK_TEXT
    assert parsed.pending.party_size == 2
    assert parsed.pending.time_text is None
    assert parsed.reply != PHONE_ASK_TEXT


def test_poke_palace_collects_time_before_phone(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = intake_kickoff(POKE_PALACE_BOOK_TEXT, settings)
    assert isinstance(first, KickoffCollecting)
    second = intake_kickoff(
        PARTY_FOLLOWUP_TEXT, settings, pending=first.pending
    )
    assert isinstance(second, KickoffCollecting)
    assert second.pending.waiting_for == "time"
    third = intake_kickoff(
        TIME_FOLLOWUP_TEXT, settings, pending=second.pending
    )
    assert isinstance(third, KickoffCollecting)
    assert third.reply == PHONE_ASK_TEXT
    assert third.pending.waiting_for == "to"
    assert third.pending.time_text == "7:30"
    fourth = intake_kickoff(
        PHONE_FOLLOWUP_TEXT, settings, pending=third.pending
    )
    assert isinstance(fourth, KickoffCollecting)
    assert fourth.pending.waiting_for == "confirm"
    fifth = intake_kickoff(
        APPROVE_FOLLOWUP_TEXT, settings, pending=fourth.pending
    )
    assert isinstance(fifth, KickoffParsed)
    assert isinstance(fifth.request, BookRequest)
    assert fifth.request.party_size == 2
    assert fifth.request.time_text == "7:30"


def test_bundled_phone_and_time_after_party(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = intake_kickoff(POKE_PALACE_BOOK_TEXT, settings)
    assert isinstance(first, KickoffCollecting)
    second = intake_kickoff(
        PARTY_FOLLOWUP_TEXT, settings, pending=first.pending
    )
    assert isinstance(second, KickoffCollecting)

    parsed = intake_kickoff(
        "7272701288 and at 7:30", settings, pending=second.pending
    )

    assert isinstance(parsed, KickoffCollecting)
    assert parsed.pending.waiting_for == "confirm"
    assert parsed.pending.party_size == 2
    assert parsed.pending.time_text == "7:30"
    assert parsed.pending.to == "+17272701288"
    assert parsed.reply != PHONE_ASK_TEXT
    approved = intake_kickoff(
        APPROVE_FOLLOWUP_TEXT, settings, pending=parsed.pending
    )
    assert isinstance(approved, KickoffParsed)
    assert isinstance(approved.request, BookRequest)
    assert approved.request.time_text == "7:30"
    assert approved.request.to == E164("+17272701288")


def test_stale_invented_time_does_not_skip_to_phone(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    pending = PendingCollect(
        verb="book",
        waiting_for="party",
        time_text="poke palace for me?",
    )

    parsed = intake_kickoff(
        PARTY_FOLLOWUP_TEXT, settings, pending=pending
    )

    assert isinstance(parsed, KickoffCollecting)
    assert parsed.pending.waiting_for == "time"
    assert parsed.reply == TIME_ASK_TEXT
    assert parsed.pending.party_size == 2
    assert parsed.pending.time_text is None
    assert parsed.reply != PHONE_ASK_TEXT


def test_time_then_phone_collect_order(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = intake_kickoff(
        "can you make a reservation for a party of two?", settings
    )
    assert isinstance(first, KickoffCollecting)
    second = intake_kickoff("7:30", settings, pending=first.pending)
    assert isinstance(second, KickoffCollecting)
    assert second.reply == PHONE_ASK_TEXT
    assert second.pending.time_text == "7:30"
    third = intake_kickoff(
        PHONE_FOLLOWUP_TEXT, settings, pending=second.pending
    )
    assert isinstance(third, KickoffCollecting)
    assert third.pending.waiting_for == "confirm"
    fourth = intake_kickoff(
        APPROVE_FOLLOWUP_TEXT, settings, pending=third.pending
    )
    assert isinstance(fourth, KickoffParsed)
    assert isinstance(fourth.request, BookRequest)
    assert fourth.request.party_size == 2
    assert fourth.request.time_text == "7:30"


def test_bad_phone_followup_fails_loud(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = intake_kickoff(NATURAL_BOOK_TEXT, settings)
    assert isinstance(first, KickoffCollecting)
    parsed = intake_kickoff("nope", settings, pending=first.pending)

    assert isinstance(parsed, KickoffFailed)
    assert "phone number" in parsed.reason
    assert failed_reply_text(parsed.reason).startswith("Call failed:")


def test_bad_confirm_followup_fails_loud(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = intake_kickoff(NATURAL_BOOK_TEXT, settings)
    assert isinstance(first, KickoffCollecting)
    phone = intake_kickoff(
        PHONE_FOLLOWUP_TEXT, settings, pending=first.pending
    )
    assert isinstance(phone, KickoffCollecting)
    parsed = intake_kickoff("nope", settings, pending=phone.pending)

    assert isinstance(parsed, KickoffFailed)
    assert "confirm" in parsed.reason
    assert failed_reply_text(parsed.reason).startswith("Call failed:")
    assert not (tmp_path / "artifacts" / "stub-call-0001").exists()


def test_us_phone_followup_normalizes_to_e164(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = intake_kickoff(NATURAL_BOOK_TEXT, settings)
    assert isinstance(first, KickoffCollecting)
    parsed = intake_kickoff("415-555-2671", settings, pending=first.pending)

    assert isinstance(parsed, KickoffCollecting)
    assert parsed.pending.waiting_for == "confirm"
    assert parsed.pending.to == "+14155552671"
    approved = intake_kickoff(
        APPROVE_FOLLOWUP_TEXT, settings, pending=parsed.pending
    )
    assert isinstance(approved, KickoffParsed)
    assert approved.request.to == E164("+14155552671")


def test_parse_non_kickoff_is_none(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert parse_kickoff("hello there", settings) is None
    assert parse_kickoff("reply", settings) is None
    assert parse_kickoff(PHONE_FOLLOWUP_TEXT, settings) is None


def test_parse_ask_missing_item_collects_item(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    parsed = parse_kickoff("ask +14155552671 stock", settings)

    assert isinstance(parsed, KickoffCollecting)
    assert parsed.pending.waiting_for == "item"


def test_pending_collect_roundtrip_on_chat(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = intake_kickoff(NATURAL_BOOK_TEXT, settings)
    assert isinstance(first, KickoffCollecting)
    inbound = parse_inbound(
        kickoff_inbound_bytes(NATURAL_BOOK_TEXT, "evt-pending-1")
    )
    assert inbound is not None
    save_pending_collect(
        settings.call_agent_artifacts_dir,
        inbound.chat_id,
        first.pending,
        inbound=inbound,
    )

    loaded = load_pending_collect(
        settings.call_agent_artifacts_dir, inbound.chat_id
    )
    assert loaded == first.pending
    assert pending_collect_path(
        settings.call_agent_artifacts_dir, inbound.chat_id
    ).is_file()


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
    assert kickoff_ack_text(book) == DIAL_NOW_TEXT
    assert kickoff_ack_text(ask) == DIAL_NOW_TEXT
    assert kickoff_ack_text(book) == "Okay, I'm going to call them now."


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
        kickoff_inbound_bytes(PHONE_FOLLOWUP_TEXT, "evt-book-1")
    )
    assert inbound is not None
    first = intake_kickoff(NATURAL_BOOK_TEXT, settings)
    assert isinstance(first, KickoffCollecting)
    phone = intake_kickoff(
        PHONE_FOLLOWUP_TEXT, settings, pending=first.pending
    )
    assert isinstance(phone, KickoffCollecting)
    parsed = intake_kickoff(
        APPROVE_FOLLOWUP_TEXT, settings, pending=phone.pending
    )
    assert isinstance(parsed, KickoffParsed)
    port = StubLinq()
    session = make_session(inbound, settings, port)

    receipt = await run_kickoff(
        session, parsed.request, from_did=parsed.from_did
    )

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
        kickoff_inbound_bytes(PHONE_FOLLOWUP_TEXT, "evt-live-1")
    )
    assert inbound is not None
    request = BookRequest(
        user_id=UserId("acctnathan"),
        to=E164("+14155552671"),
        caller=FirstName("Nathan"),
        mode=Mode.LIVE,
        party_size=2,
        time_text="7:30",
    )
    port = StubLinq()
    session = make_session(inbound, settings, port)

    receipt = await run_kickoff(
        session, request, from_did=E164("+15555550177")
    )

    assert receipt.result is None
    assert receipt.error is not None
    assert "PUBLIC_WEBHOOK_URL" in receipt.error
    result_text = port.sent[-1][1]["message"]["parts"][0]["value"]
    assert result_text.startswith("Call not placed:")
    assert not (tmp_path / "artifacts" / "stub-call-0001").exists()


async def test_run_kickoff_empty_disconnect_reason_is_not_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args: object, **kwargs: object) -> object:
        raise WebSocketDisconnect(code=1006)

    monkeypatch.setattr("call_agent.kickoff.run_call", boom)
    settings = _settings(tmp_path)
    inbound = parse_inbound(
        kickoff_inbound_bytes(PHONE_FOLLOWUP_TEXT, "evt-empty-reason")
    )
    assert inbound is not None
    request = BookRequest(
        user_id=UserId("acctnathan"),
        to=E164("+14155552671"),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
        party_size=2,
        time_text="7:30",
    )
    port = StubLinq()
    session = make_session(inbound, settings, port)

    receipt = await run_kickoff(
        session, request, from_did=E164("+15555550177")
    )

    assert receipt.result is None
    result_text = port.sent[-1][1]["message"]["parts"][0]["value"]
    assert result_text == "Call failed: WebSocketDisconnect"
    assert not result_text.endswith("Call failed: ")
    assert str(WebSocketDisconnect(code=1006)).strip() == ""


async def test_kickoff_session_ask_is_reserved(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    inbound = parse_inbound(
        kickoff_inbound_bytes(NATURAL_BOOK_TEXT, "evt-ask-seam")
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
        kickoff_inbound_bytes(PHONE_FOLLOWUP_TEXT, "evt-session-text")
    )
    assert inbound is not None
    first = intake_kickoff(NATURAL_BOOK_TEXT, settings)
    assert isinstance(first, KickoffCollecting)
    phone = intake_kickoff(
        PHONE_FOLLOWUP_TEXT, settings, pending=first.pending
    )
    assert isinstance(phone, KickoffCollecting)
    parsed = intake_kickoff(
        APPROVE_FOLLOWUP_TEXT, settings, pending=phone.pending
    )
    assert isinstance(parsed, KickoffParsed)
    port = _RecordingPort()
    session = KickoffSession(
        inbound=inbound,
        from_did=E164("+15555550100"),
        port=port,
        settings=settings,
    )

    await run_kickoff(
        session, parsed.request, from_did=parsed.from_did
    )

    assert len(port.texts) == 2
    assert port.texts[0] == DIAL_NOW_TEXT
    assert port.texts[1].startswith("Booked:")


def test_verify_linq_call_passes() -> None:
    report = verify_linq_call()

    assert report.passed
    assert [check.name for check in report.checks] == [
        "parse_book",
        "collect_time",
        "collect_phone",
        "confirm_before_dial",
        "parse_quick_ask",
        "parse_fail_loud",
        "account_lookup",
        "account_gate",
        "kickoff_reply",
        "call_ran",
        "cross_link",
        "same_host_dial",
        "from_did_users_row",
        "no_number_order",
    ]
    assert all(check.passed for check in report.checks)


def test_sender_constant_is_imessage_handle() -> None:
    assert INBOUND_SENDER.startswith("+")
    assert IMESSAGE == "iMessage"


async def test_run_kickoff_dials_from_users_row_not_linq_line(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, retain_call_artifacts=True)
    inbound = parse_inbound(
        kickoff_inbound_bytes(APPROVE_FOLLOWUP_TEXT, "evt-from-did")
    )
    assert inbound is not None
    first = intake_kickoff(NATURAL_BOOK_TEXT, settings)
    assert isinstance(first, KickoffCollecting)
    phone = intake_kickoff(
        PHONE_FOLLOWUP_TEXT, settings, pending=first.pending
    )
    assert isinstance(phone, KickoffCollecting)
    parsed = intake_kickoff(
        APPROVE_FOLLOWUP_TEXT, settings, pending=phone.pending
    )
    assert isinstance(parsed, KickoffParsed)
    session = KickoffSession(
        inbound=inbound,
        from_did=E164("+15555550100"),
        port=StubLinq(),
        settings=settings,
    )

    await run_kickoff(
        session, parsed.request, from_did=parsed.from_did
    )

    dial = json.loads(
        (tmp_path / "artifacts" / "stub-call-0001" / "dial.json").read_text()
    )
    from call_agent.domain import ClientState
    from call_agent.ledger import open_ledger

    assert dial["from"] == "+15555550177"
    assert dial["from"] != str(session.from_did)
    assert dial["from"] != INBOUND_SENDER
    assert ClientState.decode(dial["client_state"]).user_id == UserId(
        "acctnathan"
    )
    assert open_ledger(settings).list() == []

