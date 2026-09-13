from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .config import Settings
from .domain import (
    BOOK_RESULT_ADAPTER,
    DISCLOSURE_PHRASE,
    QUICK_ASK_RESULT_ADAPTER,
    BookRequest,
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
from .report import CallReport
from .runtime import run_call
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


class BookVerifyInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    to: E164 = E164("+14155552671")
    caller_first_name: FirstName = FirstName("Nathan")
    party_size: int = 2
    time_text: str = "Friday 7pm"
    constraints: str | None = "outdoor if possible"
    from_did: E164 = E164("+15555550100")
    mode: Mode = Mode.STUB
    user_id: UserId = UserId("verify")


CheckName = Literal[
    "did_in_ledger",
    "dial_payload",
    "disclosure_first",
    "clean_hangup",
    "result_artifact",
    "session_delegation",
    "tool_loop",
    "app_owned_hangup",
    "multi_turn_booking",
    "keeps_talking",
]


class VerifyCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: CheckName
    passed: bool
    detail: str


class VerifyReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    passed: bool
    checks: tuple[VerifyCheck, ...]
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
        retain_call_artifacts=True,
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
        run_call(
            request,
            settings,
            from_did=first_provision.did or verify_input.from_did,
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

    rows = _read_json_lines(run_dir / "events.jsonl")
    events = [row for row in rows if "kind" in row]
    marks_sent = sum(1 for row in rows if row.get("effect") == "mark")
    call_report = _read_json(run_dir / "call_report.json")
    hangup_path = call_report.get("hangup", {}).get("path")
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
    last_audio_sequence = max(
        (
            int(event["seq"])
            for event in events
            if event.get("kind") == "live_output_audio"
        ),
        default=0,
    )
    trail_sequences = [
        int(event["seq"])
        for event in events
        if event.get("kind") == "hangup_trail_elapsed"
    ]
    drain_timed_out = any(
        event.get("kind") == "hangup_drain_timeout" for event in events
    )
    artifact_result = _read_json(run_dir / "result.json")
    terminal_before_hangup = bool(
        transcript_sequences
        and hangup_sequences
        and transcript_sequences[-1] < hangup_sequences[-1]
    )
    drained_before_hangup = bool(
        trail_sequences
        and hangup_sequences
        and last_audio_sequence < trail_sequences[-1] < hangup_sequences[-1]
        and not drain_timed_out
    )
    clean_hangup_passed = (
        (run_dir / "hangup.json").is_file()
        and terminal_before_hangup
        and drained_before_hangup
        and marks_sent == 1
        and hangup_path == "tool"
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
            f"last_audio_seq={last_audio_sequence} "
            f"marks_sent={marks_sent} "
            f"trail_seq={trail_sequences[-1] if trail_sequences else None} "
            f"drain_timed_out={drain_timed_out} "
            f"hangup_path={hangup_path} "
            f"hangup_seq="
            f"{hangup_sequences[-1] if hangup_sequences else None}, "
            "expected status answered|declined, verdict before call.hangup, "
            "one final mark, hangup path tool, and the trailing silence after "
            "the last audio and before call.hangup"
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
    parsed_report = CallReport.model_validate(call_report)
    report_refs_exist = all(
        (active_workspace.artifacts_dir / ref).is_file()
        for ref in parsed_report.refs.model_dump().values()
    )
    result_passed = (
        parsed_result == result
        and refs_exist
        and parsed_report.call_control_id == result.call_control_id
        and report_refs_exist
    )
    result_check = VerifyCheck(
        name="result_artifact",
        passed=result_passed,
        detail=(
            f"artifacts_dir={active_workspace.artifacts_dir} "
            f"result={run_dir / 'result.json'} refs_exist={refs_exist} "
            f"call_report={run_dir / 'call_report.json'} "
            f"report_refs_exist={report_refs_exist}"
        ),
    )

    session_check = _session_delegation_check(run_dir, call_report)
    tool_loop_check = _tool_loop_check(
        run_dir, rows, events, artifact_result
    )
    app_hangup_check = _app_owned_hangup_check(
        rows, call_report, trail_sequences, hangup_sequences
    )

    checks = (
        did_check,
        dial_check,
        disclosure_check,
        clean_hangup_check,
        result_check,
        session_check,
        tool_loop_check,
        app_hangup_check,
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
    lines.append(f"{passed}/{len(report.checks)} passed")
    return "\n".join(lines)


def verify_book(
    inp: BookVerifyInput | None = None,
    workspace: VerifyWorkspace | None = None,
    *,
    scenario: StubScenario | None = None,
) -> VerifyReport:
    verify_input = inp or BookVerifyInput()
    active_workspace = workspace or _temporary_workspace("verify-book-")
    stall_s = 20.0
    farewell_idle = 0.8
    if scenario is not None and scenario.never_resumes:
        stall_s = 0.2
        farewell_idle = 0.05
    settings = Settings(
        _env_file=None,
        call_agent_db_path=active_workspace.db_path,
        call_agent_artifacts_dir=active_workspace.artifacts_dir,
        retain_call_artifacts=True,
        live_silence_failsafe_s=stall_s,
        farewell_idle_s=farewell_idle,
        farewell_max_s=0.2 if scenario is not None and scenario.never_resumes else 4.0,
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
    request = BookRequest(
        user_id=verify_input.user_id,
        to=verify_input.to,
        caller=verify_input.caller_first_name,
        mode=verify_input.mode,
        party_size=verify_input.party_size,
        time_text=verify_input.time_text,
        constraints=verify_input.constraints,
    )
    result = asyncio.run(
        run_call(
            request,
            settings,
            from_did=first_provision.did or verify_input.from_did,
            scenario=scenario,
        )
    )
    run_dir = active_workspace.artifacts_dir / result.call_control_id

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
    disclosure = f"{DISCLOSURE_PHRASE}{verify_input.caller_first_name}"
    ask_text = "I'd like to book a table"
    facts = (
        f"Party size: {verify_input.party_size}. "
        f"Requested time: {verify_input.time_text}."
    )
    has_disclosure = disclosure in instructions
    has_ask = ask_text in instructions and facts in instructions
    ordered = (
        has_disclosure
        and has_ask
        and instructions.index(disclosure) < instructions.index(ask_text)
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
            f"ask_at={instructions.find(ask_text)} "
            f"first_agent_turn_discloses={spoken}, "
            f"expected '{disclosure}' before the ask and spoken first"
        ),
    )

    event_rows = _read_json_lines(run_dir / "events.jsonl")
    events = [row for row in event_rows if "kind" in row]
    marks_sent = sum(1 for row in event_rows if row.get("effect") == "mark")
    call_report = _read_json(run_dir / "call_report.json")
    hangup_path = call_report.get("hangup", {}).get("path")
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
    last_audio_sequence = max(
        (
            int(event["seq"])
            for event in events
            if event.get("kind") == "live_output_audio"
        ),
        default=0,
    )
    trail_sequences = [
        int(event["seq"])
        for event in events
        if event.get("kind") == "hangup_trail_elapsed"
    ]
    drain_timed_out = any(
        event.get("kind") == "hangup_drain_timeout" for event in events
    )
    artifact_result = _read_json(run_dir / "result.json")
    terminal_before_hangup = bool(
        transcript_sequences
        and hangup_sequences
        and transcript_sequences[-1] < hangup_sequences[-1]
    )
    drained_before_hangup = bool(
        trail_sequences
        and hangup_sequences
        and last_audio_sequence < trail_sequences[-1] < hangup_sequences[-1]
        and not drain_timed_out
    )
    clean_hangup_passed = (
        (run_dir / "hangup.json").is_file()
        and terminal_before_hangup
        and drained_before_hangup
        and marks_sent == 1
        and hangup_path == "tool"
        and artifact_result.get("status") in {"booked", "declined"}
    )
    clean_hangup_check = VerifyCheck(
        name="clean_hangup",
        passed=clean_hangup_passed,
        detail=(
            f"status={artifact_result.get('status')} "
            f"hangup_json={(run_dir / 'hangup.json').is_file()} "
            f"transcript_seq="
            f"{transcript_sequences[-1] if transcript_sequences else None} "
            f"last_audio_seq={last_audio_sequence} "
            f"marks_sent={marks_sent} "
            f"trail_seq={trail_sequences[-1] if trail_sequences else None} "
            f"drain_timed_out={drain_timed_out} "
            f"hangup_path={hangup_path} "
            f"hangup_seq="
            f"{hangup_sequences[-1] if hangup_sequences else None}, "
            "expected status booked|declined, verdict before call.hangup, "
            "one final mark, hangup path tool, and the trailing silence after "
            "the last audio and before call.hangup"
            + (
                f" (reason: {artifact_result.get('reason')})"
                if artifact_result.get("reason")
                else ""
            )
        ),
    )

    parsed_result = BOOK_RESULT_ADAPTER.validate_python(artifact_result)
    transcript_ref = parsed_result.transcript_ref
    audio_ref = parsed_result.audio_ref
    refs_exist = bool(
        transcript_ref
        and audio_ref
        and (active_workspace.artifacts_dir / transcript_ref).is_file()
        and (active_workspace.artifacts_dir / audio_ref).is_file()
    )
    parsed_report = CallReport.model_validate(call_report)
    report_refs_exist = all(
        (active_workspace.artifacts_dir / ref).is_file()
        for ref in parsed_report.refs.model_dump().values()
    )
    result_passed = (
        parsed_result == result
        and refs_exist
        and parsed_report.call_control_id == result.call_control_id
        and report_refs_exist
    )
    result_check = VerifyCheck(
        name="result_artifact",
        passed=result_passed,
        detail=(
            f"artifacts_dir={active_workspace.artifacts_dir} "
            f"result={run_dir / 'result.json'} refs_exist={refs_exist} "
            f"call_report={run_dir / 'call_report.json'} "
            f"report_refs_exist={report_refs_exist}"
        ),
    )

    turns = transcript.get("turns", [])
    business_turns = [
        turn for turn in turns if turn.get("speaker") == "business"
    ]
    spoken_text = " ".join(str(turn.get("text", "")) for turn in turns)
    spoken_lower = spoken_text.casefold()
    party_mentioned = str(verify_input.party_size) in spoken_text
    time_mentioned = verify_input.time_text.casefold() in spoken_lower
    notes = verify_input.constraints or ""
    notes_mentioned = not notes or notes.casefold() in spoken_lower
    multi_passed = (
        len(agent_turns) >= 3
        and len(business_turns) >= 2
        and party_mentioned
        and time_mentioned
        and notes_mentioned
        and artifact_result.get("status") in {"booked", "declined"}
    )
    multi_check = VerifyCheck(
        name="multi_turn_booking",
        passed=multi_passed,
        detail=(
            f"agent_turns={len(agent_turns)} business_turns="
            f"{len(business_turns)} party_mentioned={party_mentioned} "
            f"time_mentioned={time_mentioned} "
            f"notes_mentioned={notes_mentioned} "
            f"status={artifact_result.get('status')}, "
            "expected >=3 agent turns, >=2 business turns, "
            "party/time/constraints spoken, status booked|declined"
        ),
    )

    delegation_sequences = [
        int(event["seq"])
        for event in events
        if event.get("kind") == "live_delegation"
        and event.get("target") == "responses"
    ]
    tool_result_sequences = [
        int(row["seq"])
        for row in event_rows
        if row.get("effect") == "tool_result"
    ]
    continue_sequences = [
        int(row["seq"])
        for row in event_rows
        if row.get("effect") == "continue_response"
    ]
    output_after_continue = bool(
        continue_sequences
        and any(
            event.get("kind") == "live_output_transcript"
            and int(event["seq"]) > continue_sequences[0]
            for event in events
        )
    )
    steer_effects = any(
        row.get("effect") == "steer_live" for row in event_rows
    )
    keeps_passed = (
        bool(delegation_sequences)
        and bool(tool_result_sequences)
        and bool(continue_sequences)
        and delegation_sequences[0] < tool_result_sequences[0]
        < continue_sequences[0]
        and output_after_continue
        and not steer_effects
        and hangup_path == "tool"
        and artifact_result.get("status") in {"booked", "declined"}
    )
    keeps_check = VerifyCheck(
        name="keeps_talking",
        passed=keeps_passed,
        detail=(
            f"delegation_seq="
            f"{delegation_sequences[0] if delegation_sequences else None} "
            f"tool_result_seq="
            f"{tool_result_sequences[0] if tool_result_sequences else None} "
            f"continue_seq="
            f"{continue_sequences[0] if continue_sequences else None} "
            f"output_after_continue={output_after_continue} "
            f"steer_live={steer_effects} "
            f"hangup_path={hangup_path} "
            f"status={artifact_result.get('status')}, "
            "expected live_delegation then tool_result then "
            "continue_response then more agent speech, hangup tool, "
            "status booked|declined"
        ),
    )

    session_check = _session_delegation_check(run_dir, call_report)
    tool_loop_check = _tool_loop_check(
        run_dir, event_rows, events, artifact_result
    )
    app_hangup_check = _app_owned_hangup_check(
        event_rows, call_report, trail_sequences, hangup_sequences
    )

    checks = (
        did_check,
        dial_check,
        disclosure_check,
        clean_hangup_check,
        result_check,
        multi_check,
        keeps_check,
        session_check,
        tool_loop_check,
        app_hangup_check,
    )
    return VerifyReport(
        passed=all(check.passed for check in checks),
        checks=checks,
        call_control_id=result.call_control_id,
    )


def _session_delegation_check(
    run_dir: Path, call_report: dict
) -> VerifyCheck:
    session_start = _read_json(run_dir / "session_start.json")
    session = session_start.get("session") or {}
    delegation = session.get("delegation") or {}
    responses = delegation.get("responses") or {}
    tools = responses.get("tools") or []
    names = {
        tool.get("name") for tool in tools if isinstance(tool, dict)
    }
    forbidden = {"turn_detection", "modalities", "input_audio_format"}
    has_session_tools = "tools" in session or "tool_choice" in session
    audio_rate = (
        session.get("audio", {}).get("format", {}).get("rate")
        if isinstance(session.get("audio"), dict)
        else None
    )
    resampled = (call_report.get("audio") or {}).get("resampled")
    media_start = next(
        (
            row
            for row in _read_json_lines(run_dir / "events.jsonl")
            if row.get("kind") == "media_start"
        ),
        {},
    )
    passed = (
        delegation.get("type") == "responses"
        and bool(responses.get("model"))
        and names == {"record_task_result", "end_call"}
        and responses.get("parallel_tool_calls") is False
        and not (set(session) & forbidden)
        and not has_session_tools
        and audio_rate == 16000
        and resampled is False
        and media_start.get("sample_rate") == 16000
    )
    return VerifyCheck(
        name="session_delegation",
        passed=passed,
        detail=(
            f"type={delegation.get('type')} model={responses.get('model')} "
            f"tools={sorted(names)} parallel={responses.get('parallel_tool_calls')} "
            f"audio_rate={audio_rate} media_rate={media_start.get('sample_rate')} "
            f"resampled={resampled} session_tools={has_session_tools}"
        ),
    )


def _tool_loop_check(
    run_dir: Path,
    event_rows: list[dict],
    events: list[dict],
    artifact_result: dict,
) -> VerifyCheck:
    delegations = [
        event
        for event in events
        if event.get("kind") == "live_delegation"
        and event.get("target") == "responses"
    ]
    tool_calls = [
        event for event in events if event.get("kind") == "live_tool_call"
    ]
    tool_results = [
        row for row in event_rows if row.get("effect") == "tool_result"
    ]
    continues = [
        row for row in event_rows if row.get("effect") == "continue_response"
    ]
    call_ids = [event.get("call_id") for event in tool_calls]
    result_ids = [row.get("call_id") for row in tool_results]
    unique_once = call_ids == result_ids and len(call_ids) == len(set(call_ids))
    task_path = run_dir / "task.json"
    task = _read_json(task_path) if task_path.is_file() else {}
    status_map = {
        "confirmed": "booked",
        "answered": "answered",
        "declined": "declined",
    }
    mapped = status_map.get(str(task.get("outcome")))
    passed = (
        bool(delegations)
        and bool(tool_calls)
        and bool(tool_results)
        and bool(continues)
        and unique_once
        and task_path.is_file()
        and mapped == artifact_result.get("status")
    )
    return VerifyCheck(
        name="tool_loop",
        passed=passed,
        detail=(
            f"delegations={len(delegations)} tool_calls={len(tool_calls)} "
            f"tool_results={len(tool_results)} continues={len(continues)} "
            f"unique_once={unique_once} task_outcome={task.get('outcome')} "
            f"status={artifact_result.get('status')}"
        ),
    )


def _app_owned_hangup_check(
    event_rows: list[dict],
    call_report: dict,
    trail_sequences: list[int],
    hangup_sequences: list[int],
) -> VerifyCheck:
    steer = any(row.get("effect") == "steer_live" for row in event_rows)
    marks_sent = sum(1 for row in event_rows if row.get("effect") == "mark")
    hangup_path = call_report.get("hangup", {}).get("path")
    trail_before_hangup = bool(
        trail_sequences
        and hangup_sequences
        and trail_sequences[-1] < hangup_sequences[-1]
    )
    passed = (
        hangup_path == "tool"
        and not steer
        and marks_sent == 1
        and trail_before_hangup
    )
    return VerifyCheck(
        name="app_owned_hangup",
        passed=passed,
        detail=(
            f"hangup_path={hangup_path} steer_live={steer} "
            f"marks_sent={marks_sent} trail_before_hangup={trail_before_hangup}"
        ),
    )


def _temporary_workspace(prefix: str = "verify-quick-ask-") -> VerifyWorkspace:
    root = Path(tempfile.mkdtemp(prefix=prefix))
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
