import json
from pathlib import Path

import pytest

from call_agent.artifacts import wipe_run_dir
from call_agent.config import Settings
from call_agent.domain import (
    BookRequest,
    E164,
    FirstName,
    Mode,
    Question,
    QuestionType,
    QuickAskRequest,
    UserId,
)
from call_agent.runtime import run_call, should_retain_call_artifacts


WIPED_NAMES = (
    "transcript.json",
    "result.json",
    "call_report.json",
    "call_report.redacted.json",
    "events.jsonl",
    "hangup.json",
    "session_start.json",
    "dial.json",
)

FROM_DID = E164("+15555550100")


def _settings(tmp_path: Path, **updates: object) -> Settings:
    values = dict(
        _env_file=None,
        call_agent_db_path=tmp_path / "ledger.sqlite3",
        call_agent_artifacts_dir=tmp_path / "artifacts",
        hangup_trailing_silence_s=0,
    )
    values.update(updates)
    return Settings(**values)


def _quick_ask(
    retain_call_artifacts: bool | None = None,
) -> QuickAskRequest:
    return QuickAskRequest(
        user_id=UserId("user-1"),
        to=E164("+14155552671"),
        question=Question.from_cli(QuestionType.HOURS, None),
        caller=FirstName("Nathan"),
        mode=Mode.STUB,
        retain_call_artifacts=retain_call_artifacts,
    )


def test_wipe_run_dir_removes_files_and_keeps_folder(tmp_path: Path) -> None:
    run = tmp_path / "stub-call-0001"
    (run / "audio").mkdir(parents=True)
    (run / "transcript.json").write_text("{}\n")
    (run / "audio" / "inbound.wav").write_bytes(b"in")

    wipe_run_dir(run)

    assert run.is_dir()
    assert list(run.iterdir()) == []


def test_should_retain_defaults_off_and_request_overrides_settings() -> None:
    settings_off = Settings(_env_file=None, retain_call_artifacts=False)
    settings_on = Settings(_env_file=None, retain_call_artifacts=True)

    assert should_retain_call_artifacts(settings_off, _quick_ask()) is False
    assert should_retain_call_artifacts(settings_on, _quick_ask()) is True
    assert (
        should_retain_call_artifacts(settings_off, _quick_ask(True)) is True
    )
    assert (
        should_retain_call_artifacts(settings_on, _quick_ask(False)) is False
    )


@pytest.mark.asyncio
async def test_hangup_deletes_call_artifacts_when_opt_in_absent(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    result = await run_call(_quick_ask(), settings, from_did=FROM_DID)
    run_dir = settings.call_agent_artifacts_dir / result.call_control_id

    assert result.status == "answered"
    assert result.transcript_ref == f"{result.call_control_id}/transcript.json"
    assert run_dir.is_dir()
    for name in WIPED_NAMES:
        assert not (run_dir / name).is_file(), name
    assert not (run_dir / "audio").exists()


@pytest.mark.asyncio
async def test_hangup_keeps_call_artifacts_when_settings_opt_in(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, retain_call_artifacts=True)
    result = await run_call(_quick_ask(), settings, from_did=FROM_DID)
    run_dir = settings.call_agent_artifacts_dir / result.call_control_id

    assert result.status == "answered"
    assert (run_dir / "transcript.json").is_file()
    assert (run_dir / "audio" / "inbound.wav").is_file()
    assert (run_dir / "hangup.json").is_file()
    report = json.loads((run_dir / "call_report.json").read_text())
    assert report["hangup"]["path"] == "tool"


@pytest.mark.asyncio
async def test_hangup_keeps_call_artifacts_when_request_opt_in(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, retain_call_artifacts=False)
    result = await run_call(_quick_ask(True), settings, from_did=FROM_DID)
    run_dir = settings.call_agent_artifacts_dir / result.call_control_id

    assert (run_dir / "transcript.json").is_file()
    assert (run_dir / "audio" / "outbound.wav").is_file()


@pytest.mark.asyncio
async def test_book_hangup_wipes_unless_opted_in(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    result = await run_call(
        BookRequest(
            user_id=UserId("user-1"),
            to=E164("+14155552671"),
            caller=FirstName("Nathan"),
            mode=Mode.STUB,
            party_size=2,
            time_text="Friday 7pm",
            constraints="outdoor if possible",
        ),
        settings,
        from_did=FROM_DID,
    )
    run_dir = settings.call_agent_artifacts_dir / result.call_control_id

    assert result.status == "booked"
    assert not (run_dir / "transcript.json").is_file()
    assert not (run_dir / "call_report.json").is_file()


@pytest.mark.asyncio
async def test_wipe_skips_durable_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published: list[str] = []

    def capture(
        root: Path,
        call_control_id: str,
        audio_names: tuple[str, str],
        store: object,
    ) -> None:
        del root, audio_names, store
        published.append(call_control_id)

    monkeypatch.setattr(
        "call_agent.runtime.publish_durable_artifacts", capture
    )
    settings = _settings(tmp_path)
    result = await run_call(_quick_ask(), settings, from_did=FROM_DID)

    assert published == []
    assert not (
        settings.call_agent_artifacts_dir
        / result.call_control_id
        / "transcript.json"
    ).is_file()


@pytest.mark.asyncio
async def test_retain_publishes_durable_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    published: list[str] = []

    def capture(
        root: Path,
        call_control_id: str,
        audio_names: tuple[str, str],
        store: object,
    ) -> None:
        del root, audio_names, store
        published.append(call_control_id)

    monkeypatch.setattr(
        "call_agent.runtime.publish_durable_artifacts", capture
    )
    settings = _settings(tmp_path, retain_call_artifacts=True)
    result = await run_call(_quick_ask(), settings, from_did=FROM_DID)

    assert published == [result.call_control_id]
    assert (
        settings.call_agent_artifacts_dir
        / result.call_control_id
        / "transcript.json"
    ).is_file()
