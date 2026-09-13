from call_agent.tools import (
    ASK_OUTCOMES,
    BOOK_OUTCOMES,
    check_record_task_result,
    parse_end_call,
    parse_record_task_result,
)


def test_confirmed_requires_party_and_time() -> None:
    args = parse_record_task_result(
        '{"outcome":"confirmed","summary":"booked","confirmed_fields":{"party_size":null,"time_text":null,"name_on_record":null,"answer_text":null}}'
    )

    assert check_record_task_result(args, BOOK_OUTCOMES) == (
        "confirmed requires party_size and time_text"
    )


def test_answered_is_illegal_for_book() -> None:
    args = parse_record_task_result(
        '{"outcome":"answered","summary":"open","confirmed_fields":{"party_size":null,"time_text":null,"name_on_record":null,"answer_text":"Open 9 to 5."}}'
    )

    assert "not allowed" in (check_record_task_result(args, BOOK_OUTCOMES) or "")


def test_answered_requires_answer_text() -> None:
    args = parse_record_task_result(
        '{"outcome":"answered","summary":"open","confirmed_fields":{"party_size":null,"time_text":null,"name_on_record":null,"answer_text":null}}'
    )

    assert check_record_task_result(args, ASK_OUTCOMES) == (
        "answered requires answer_text"
    )


def test_end_call_parses_reason() -> None:
    args = parse_end_call('{"reason":"task_done"}')

    assert args.reason == "task_done"
