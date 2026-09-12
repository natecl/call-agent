import json
from pathlib import Path

from typer.testing import CliRunner

from call_agent.cli import LIVE_GATE_MESSAGE, app


runner = CliRunner()


def environment(tmp_path: Path) -> dict[str, str]:
    return {
        "CALL_AGENT_DB_PATH": str(tmp_path / "call-agent.sqlite3"),
        "CALL_AGENT_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
        "QUICK_ASK_LIVE": "0",
    }


def test_verify_quick_ask_stub_exits_zero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-quick-ask", "--mode", "stub"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 0
    assert "5/5 passed" in result.stdout
    assert result.stdout.count("[PASS]") == 5


def test_verify_quick_ask_live_requires_gate(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-quick-ask", "--mode", "live"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 2
    assert LIVE_GATE_MESSAGE in result.output


def test_verify_quick_ask_live_names_missing_keys(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-quick-ask", "--mode", "live"],
        env=environment(tmp_path) | {"QUICK_ASK_LIVE": "1"},
    )

    assert result.exit_code == 2
    assert (
        "live mode requires: TELNYX_API_KEY, TELNYX_CONNECTION_ID, "
        "OPENAI_API_KEY, PUBLIC_BASE_URL"
    ) in result.output


def test_verify_quick_ask_wrong_did_exits_one_with_fail_lines(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        ["verify-quick-ask", "--mode", "stub", "--from-did", "+15555559999"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 1
    assert "[FAIL] did_in_ledger" in result.stdout
    assert "expected from_did +15555559999" in result.stdout
    assert "3/5 passed" in result.stdout


def test_quick_ask_stub_prints_answered_result(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "quick-ask",
            "--to",
            "+14155552671",
            "--ask",
            "hours",
            "--name",
            "Nathan",
        ],
        env=environment(tmp_path),
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "status": "answered",
        "answer_text": (
            "We're open Monday through Friday, 9 AM to 5 PM."
        ),
        "call_control_id": "stub-call-0001",
        "transcript_ref": "stub-call-0001/transcript.json",
        "audio_ref": "stub-call-0001/audio/inbound.wav",
    }
    assert (
        tmp_path / "artifacts" / payload["transcript_ref"]
    ).is_file()
    assert (tmp_path / "artifacts" / payload["audio_ref"]).is_file()


def test_quick_ask_stock_requires_item(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "quick-ask",
            "--to",
            "+14155552671",
            "--ask",
            "stock",
            "--name",
            "Nathan",
        ],
        env=environment(tmp_path),
    )

    assert result.exit_code == 2
    assert "stock questions require --item" in result.output


def test_did_provision_live_is_gated(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["did", "provision", "--user", "default", "--live"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 2
    assert LIVE_GATE_MESSAGE in result.output


def test_quick_ask_requires_name(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["quick-ask", "--to", "+14155552671", "--ask", "hours"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 2
    assert "--name" in result.output


def test_did_provision_twice_prints_same_did(
    tmp_path: Path,
) -> None:
    first = runner.invoke(
        app,
        ["did", "provision", "--user", "default"],
        env=environment(tmp_path),
    )
    second = runner.invoke(
        app,
        ["did", "provision", "--user", "default"],
        env=environment(tmp_path),
    )

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert first.stdout == "+15555550100\n"
    assert second.stdout == first.stdout


def test_verify_webhooks_stub_exits_zero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-webhooks"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 0
    assert "5/5 passed" in result.stdout
    assert result.stdout.count("[PASS]") == 5


def test_verify_webhooks_broken_url_fails_loud(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-webhooks", "--url", "http://127.0.0.1:9"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 1
    assert "[FAIL]" in result.stdout
    assert "5/5 passed" not in result.stdout
