from pathlib import Path

from call_agent.domain import (
    Answered,
    Booked,
    Declined,
    E164,
    Failed,
    NoAnswer,
)
from call_agent.kickoff import failed_reply_text, result_reply_text
from call_agent.user_text import (
    BANNED_USER_TEXT_TOKENS,
    CALL_INCOMPLETE_TEXT,
    NOT_PLACED_TEXT,
    contains_banned_user_text,
    intake_text,
    not_placed_text,
    result_text,
)

TO = E164("+14155552671")
REPO = Path(__file__).resolve().parents[1]


def test_result_text_covers_statuses_without_banned_tokens() -> None:
    assert result_text(Booked(confirmation_text="table for 2"), TO) == (
        "Booked: table for 2"
    )
    assert result_text(Answered(answer_text="9 to 5"), TO) == "Answer: 9 to 5"
    assert result_text(Declined(), TO) == "They declined."
    assert result_text(NoAnswer(), TO) == "No answer at +14155552671."
    failed = result_text(Failed(reason="R2 put failed for v3:x: HTTP 403"), TO)
    assert failed == CALL_INCOMPLETE_TEXT
    assert not contains_banned_user_text(failed)
    assert "R2" not in failed
    assert "403" not in failed


def test_not_placed_text_is_friendly() -> None:
    text = not_placed_text()
    assert text == NOT_PLACED_TEXT
    assert not contains_banned_user_text(text)


def test_intake_text_is_plain() -> None:
    reason = "I need the restaurant's phone number."
    text = intake_text(reason)
    assert text == reason
    assert not contains_banned_user_text(text)
    assert failed_reply_text(reason) == text
    assert "Call failed:" not in text


def test_kickoff_wrappers_use_user_text() -> None:
    assert result_reply_text(Booked(confirmation_text="table for 2"), TO) == (
        "Booked: table for 2"
    )
    assert result_reply_text(Failed(reason="HTTP 403"), TO) == CALL_INCOMPLETE_TEXT
    assert failed_reply_text("I need you to confirm before I call.") == (
        "I need you to confirm before I call."
    )


def test_banned_tokens_only_live_in_the_banned_list() -> None:
    allowed = {
        REPO / "call_agent" / "user_text.py",
        REPO / "tests" / "test_user_text.py",
    }
    hits: list[str] = []
    for path in (REPO / "call_agent").rglob("*.py"):
        if path in allowed:
            continue
        text = path.read_text()
        for token in ("Call failed:", "Call not placed:"):
            if token in text:
                hits.append(f"{path.relative_to(REPO)}:{token}")
    assert hits == []
    listed = "\n".join(f'    "{token}",' for token in BANNED_USER_TEXT_TOKENS)
    assert "Call failed:" in listed
    assert "Call not placed:" in listed


def test_no_say_a_short_goodbye_in_product_source() -> None:
    allowed = {
        REPO / "call_agent" / "verify.py",
    }
    hits: list[str] = []
    for path in (REPO / "call_agent").rglob("*.py"):
        if path in allowed:
            continue
        text = path.read_text()
        if "say a short goodbye" in text:
            hits.append(str(path.relative_to(REPO)))
    assert hits == []
