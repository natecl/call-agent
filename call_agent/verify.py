from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .config import Settings
from .domain import (
    DISCLOSURE_PHRASE,
    QUICK_ASK_RESULT_ADAPTER,
    E164,
    FirstName,
    Mode,
    Question,
    QuestionType,
    QuickAskRequest,
    UserId,
)
from .gpt_live import StubScenario
from .ledger import Ledger
from .runtime import run_quick_ask
from .telnyx import StubTelnyxHttp


class VerifyInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    to: E164 = E164("+14155552671")
    question_type: QuestionType = QuestionType.HOURS
    question_text: str = "What are your hours today?"
    caller_first_name: FirstName = FirstName("Nathan")
    from_did: E164 = E164("+15555550100")
    mode: Mode = Mode.STUB
    user_id: UserId = UserId("verify")


CheckName = Literal[
    "did_in_ledger",
    "dial_payload",
    "disclosure_first",
    "clean_hangup",
    "result_artifact",
]


class VerifyCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: CheckName
    passed: bool
    detail: str


class VerifyReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    checks: tuple[
        VerifyCheck,
        VerifyCheck,
        VerifyCheck,
        VerifyCheck,
        VerifyCheck,
    ]
    call_control_id: str | None


class VerifyWorkspace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    db_path: Path
    artifacts_dir: Path


def verify_quick_ask(
    inp: VerifyInput | None = None,
    workspace: VerifyWorkspace | None = None,
    *,
    scenario: StubScenario | None = None,
) -> VerifyReport:
    verify_input = inp or VerifyInput()
    active_workspace = workspace or _temporary_workspace()
    settings = Settings(
        _env_file=None,
        call_agent_db_path=active_workspace.db_path,
        call_agent_artifacts_dir=active_workspace.artifacts_dir,
    )
    ledger = Ledger(active_workspace.db_path)
    telnyx = StubTelnyxHttp()
    connection_id = "stub-connection"
    first_provision = ledger.provision(
        verify_input.user_id, telnyx, connection_id
    )
    second_provision = ledger.provision(
        verify_input.user_id, telnyx, connection_id
    )
    item = (
        None
        if verify_input.question_type is QuestionType.HOURS
        else "item"
    )
    question = Question(
        kind=verify_input.question_type,
        text=verify_input.question_text,
        item=item,
    )
    request = QuickAskRequest(
        user_id=verify_input.user_id,
        to=verify_input.to,
        question=question,
        caller=verify_input.caller_first_name,
        mode=verify_input.mode,
    )
    result = asyncio.run(
        run_quick_ask(
            request,
            settings,
            scenario=scenario,
        )
    )
    run_dir = (
        active_workspace.artifacts_dir / result.call_control_id
    )

    rows = [
        record
        for record in ledger.list()
        if record.user_id == verify_input.user_id
    ]
    did_passed = (
        len(rows) == 1
        and rows[0].did == verify_input.from_did
        and first_provision.did == second_provision.did
        and first_provision.did == verify_input.from_did
    )
    did_check = VerifyCheck(
        name="did_in_ledger",
        passed=did_passed,
        detail=(
            f"{len(rows)} row(s) for user {verify_input.user_id}, "
            f"ledger has {rows[0].did if rows else None}, "
            f"expected from_did {verify_input.from_did}, "
            f"provisions agree={first_provision.did == second_provision.did}"
        ),
    )

    dial = _read_json(run_dir / "dial.json")
    dial_passed = (
        dial.get("from") == str(verify_input.from_did)
        and dial.get("to") == str(verify_input.to)
        and str(dial.get("stream_url", "")).startswith("wss://")
        and dial.get("stream_bidirectional_mode") == "rtp"
        and dial.get("stream_bidirectional_target_legs") == "self"
        and dial.get("stream_codec")
        == dial.get("stream_bidirectional_codec")
    )
    dial_check = VerifyCheck(
        name="dial_payload",
        passed=dial_passed,
        detail=(
            f"from={dial.get('from')} to={dial.get('to')} "
            f"stream_url={dial.get('stream_url')} "
            f"mode={dial.get('stream_bidirectional_mode')}/"
            f"{dial.get('stream_bidirectional_target_legs')} "
            f"codec={dial.get('stream_codec')}/"
            f"{dial.get('stream_bidirectional_codec')}, "
            f"expected from={verify_input.from_did} to={verify_input.to} "
            "wss:// rtp/self matching codecs"
        ),
    )

    session_start = _read_json(run_dir / "session_start.json")
    instructions = str(
        session_start.get("session", {}).get("instructions", "")
    )
    disclosure = (
        f"{DISCLOSURE_PHRASE}{verify_input.caller_first_name}"
    )
    has_disclosure = disclosure in instructions
    has_ask = verify_input.question_text in instructions
    ordered = (
        has_disclosure
        and has_ask
        and instructions.index(disclosure)
        < instructions.index(verify_input.question_text)
    )
    transcript = _read_json(run_dir / "transcript.json")
    agent_turns = [
        turn
        for turn in transcript.get("turns", [])
        if turn.get("speaker") == "agent"
    ]
    spoken = bool(
        agent_turns and disclosure in str(agent_turns[0].get("text", ""))
    )
    disclosure_check = VerifyCheck(
        name="disclosure_first",
        passed=ordered and spoken,
        detail=(
            f"instructions disclosure_at={instructions.find(disclosure)} "
            f"ask_at={instructions.find(verify_input.question_text)} "
            f"first_agent_turn_discloses={spoken}, "
            f"expected '{disclosure}' before the ask and spoken first"
        ),
    )

    events = _read_json_lines(run_dir / "events.jsonl")
    transcript_sequences = [
        int(event["seq"])
        for event in events
        if event.get("kind")
        in {"live_input_transcript", "live_output_transcript"}
    ]
    hangup_sequences = [
        int(event["seq"])
        for event in events
        if event.get("kind") == "webhook"
        and event.get("event_type") == "call.hangup"
    ]
    artifact_result = _read_json(run_dir / "result.json")
    terminal_before_hangup = bool(
        transcript_sequences
        and hangup_sequences
        and transcript_sequences[-1] < hangup_sequences[-1]
    )
    clean_hangup_passed = (
        (run_dir / "hangup.json").is_file()
        and terminal_before_hangup
        and artifact_result.get("status") in {"answered", "declined"}
    )
    clean_hangup_check = VerifyCheck(
        name="clean_hangup",
        passed=clean_hangup_passed,
        detail=(
            f"status={artifact_result.get('status')} "
            f"hangup_json={(run_dir / 'hangup.json').is_file()} "
            f"transcript_seq="
            f"{transcript_sequences[-1] if transcript_sequences else None} "
            f"hangup_seq="
            f"{hangup_sequences[-1] if hangup_sequences else None}, "
            "expected status answered|declined and verdict before call.hangup"
            + (
                f" (reason: {artifact_result.get('reason')})"
                if artifact_result.get("reason")
                else ""
            )
        ),
    )

    parsed_result = QUICK_ASK_RESULT_ADAPTER.validate_python(
        artifact_result
    )
    transcript_ref = parsed_result.transcript_ref
    audio_ref = parsed_result.audio_ref
    refs_exist = bool(
        transcript_ref
        and audio_ref
        and (
            active_workspace.artifacts_dir / transcript_ref
        ).is_file()
        and (active_workspace.artifacts_dir / audio_ref).is_file()
    )
    result_passed = parsed_result == result and refs_exist
    result_check = VerifyCheck(
        name="result_artifact",
        passed=result_passed,
        detail=(
            f"artifacts_dir={active_workspace.artifacts_dir} "
            f"result={run_dir / 'result.json'} refs_exist={refs_exist}"
        ),
    )

    checks = (
        did_check,
        dial_check,
        disclosure_check,
        clean_hangup_check,
        result_check,
    )
    return VerifyReport(
        passed=all(check.passed for check in checks),
        checks=checks,
        call_control_id=result.call_control_id,
    )


def format_report(report: VerifyReport) -> str:
    lines = [
        (
            f"[{'PASS' if check.passed else 'FAIL'}] "
            f"{check.name:<18} {check.detail}"
        )
        for check in report.checks
    ]
    passed = sum(check.passed for check in report.checks)
    lines.append(f"{passed}/5 passed")
    return "\n".join(lines)


def _temporary_workspace() -> VerifyWorkspace:
    root = Path(tempfile.mkdtemp(prefix="verify-quick-ask-"))
    return VerifyWorkspace(
        db_path=root / "call-agent.sqlite3",
        artifacts_dir=root / "artifacts",
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _read_json_lines(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line
    ]
