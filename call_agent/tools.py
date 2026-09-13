from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenToolModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FunctionToolSpec(FrozenToolModel):
    type: Literal["function"] = "function"
    name: str
    description: str
    strict: bool = True
    parameters: dict[str, Any]


class ConfirmedFields(FrozenToolModel):
    party_size: int | None = None
    time_text: str | None = None
    name_on_record: str | None = None
    answer_text: str | None = None


class RecordTaskResultArgs(FrozenToolModel):
    outcome: Literal["confirmed", "answered", "declined", "no_result"]
    summary: str
    confirmed_fields: ConfirmedFields


class EndCallArgs(FrozenToolModel):
    reason: Literal[
        "task_done", "declined", "cannot_help", "wrong_number", "voicemail"
    ]


class RecordedTask(FrozenToolModel):
    call_id: str
    name: str
    outcome: str
    summary: str
    confirmed_fields: ConfirmedFields


RECORD_TASK_RESULT_TOOL = FunctionToolSpec(
    name="record_task_result",
    description=(
        "Record the outcome of the task once the other party has given a "
        "final answer. Call exactly once. Use confirmed values only; "
        "never invent a success."
    ),
    parameters={
        "type": "object",
        "additionalProperties": False,
        "required": ["outcome", "summary", "confirmed_fields"],
        "properties": {
            "outcome": {
                "type": "string",
                "enum": ["confirmed", "answered", "declined", "no_result"],
            },
            "summary": {
                "type": "string",
                "description": (
                    "One or two sentences, the other party's words where possible."
                ),
            },
            "confirmed_fields": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "party_size",
                    "time_text",
                    "name_on_record",
                    "answer_text",
                ],
                "properties": {
                    "party_size": {"type": ["integer", "null"]},
                    "time_text": {"type": ["string", "null"]},
                    "name_on_record": {"type": ["string", "null"]},
                    "answer_text": {"type": ["string", "null"]},
                },
            },
        },
    },
)

END_CALL_TOOL = FunctionToolSpec(
    name="end_call",
    description=(
        "Ask the application to hang up. Call after record_task_result, "
        "or when the other party cannot help, it is a wrong number, or "
        "voicemail. A successful record_task_result already arms hangup. "
        "The application drains remaining speech, then hangs up."
    ),
    parameters={
        "type": "object",
        "additionalProperties": False,
        "required": ["reason"],
        "properties": {
            "reason": {
                "type": "string",
                "enum": [
                    "task_done",
                    "declined",
                    "cannot_help",
                    "wrong_number",
                    "voicemail",
                ],
            }
        },
    },
)

SESSION_TOOLS: tuple[FunctionToolSpec, ...] = (
    RECORD_TASK_RESULT_TOOL,
    END_CALL_TOOL,
)

BOOK_OUTCOMES = frozenset({"confirmed", "declined", "no_result"})
ASK_OUTCOMES = frozenset({"answered", "declined", "no_result"})


def tool_error(message: str, **extra: Any) -> str:
    payload = {"ok": False, "error": message, **extra}
    return json.dumps(payload, sort_keys=True)


def tool_ok(**extra: Any) -> str:
    payload = {"ok": True, **extra}
    return json.dumps(payload, sort_keys=True)


def parse_record_task_result(arguments_json: str) -> RecordTaskResultArgs:
    return RecordTaskResultArgs.model_validate_json(arguments_json)


def parse_end_call(arguments_json: str) -> EndCallArgs:
    return EndCallArgs.model_validate_json(arguments_json)


def check_record_task_result(
    args: RecordTaskResultArgs, allowed_outcomes: frozenset[str]
) -> str | None:
    if args.outcome not in allowed_outcomes:
        return (
            f"outcome {args.outcome!r} is not allowed; "
            f"allowed_outcomes={sorted(allowed_outcomes)}"
        )
    if args.outcome == "confirmed" and (
        args.confirmed_fields.party_size is None
        or not (args.confirmed_fields.time_text or "").strip()
    ):
        return "confirmed requires party_size and time_text"
    if args.outcome == "answered" and not (
        args.confirmed_fields.answer_text or ""
    ).strip():
        return "answered requires answer_text"
    return None
