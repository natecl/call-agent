from __future__ import annotations

from .domain import CallResult, E164

BANNED_USER_TEXT_TOKENS = (
    "Call failed:",
    "Call not placed:",
    "R2 put",
    "R2 get",
    "HTTP ",
    "PersistenceConfigError",
    "NEON_DATABASE_URL",
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
    "R2_ENDPOINT",
    "PUBLIC_BASE_URL",
    "PUBLIC_WEBHOOK_URL",
    "OPENAI_API_KEY",
    "TELNYX_API_KEY",
)

NOT_PLACED_TEXT = (
    "I couldn't place the call. Please try again in a moment."
)
CALL_INCOMPLETE_TEXT = "I couldn't finish the call. Please try again."


def contains_banned_user_text(text: str) -> bool:
    return any(token in text for token in BANNED_USER_TEXT_TOKENS)


def not_placed_text() -> str:
    return NOT_PLACED_TEXT


def intake_text(reason: str) -> str:
    text = " ".join(reason.split()) or "I need a bit more to place the call."
    return text


def result_text(result: CallResult, to: E164) -> str:
    if result.status == "booked":
        return f"Booked: {result.confirmation_text}"
    if result.status == "answered":
        return f"Answer: {result.answer_text}"
    if result.status == "declined":
        return "They declined."
    if result.status == "no_answer":
        return f"No answer at {to}."
    return CALL_INCOMPLETE_TEXT
