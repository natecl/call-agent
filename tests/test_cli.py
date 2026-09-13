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
        "NO_COLOR": "1",
        "TERM": "dumb",
        "COLUMNS": "120",
    }


def test_verify_quick_ask_stub_exits_zero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-quick-ask", "--mode", "stub"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 0
    assert "8/8 passed" in result.stdout
    assert result.stdout.count("[PASS]") == 8


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
        "OPENAI_API_KEY, OPENAI_RESPONSES_MODEL, PUBLIC_BASE_URL"
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
    assert "6/8 passed" in result.stdout


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
    run_dir = tmp_path / "artifacts" / payload["call_control_id"]
    assert run_dir.is_dir()
    assert not (tmp_path / "artifacts" / payload["transcript_ref"]).is_file()
    assert not (tmp_path / "artifacts" / payload["audio_ref"]).is_file()


def test_quick_ask_retains_artifacts_when_opted_in(tmp_path: Path) -> None:
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
        env=environment(tmp_path) | {"RETAIN_CALL_ARTIFACTS": "1"},
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "answered"
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


def test_book_stub_prints_booked_result(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "book",
            "--to",
            "+14155552671",
            "--name",
            "Nathan",
            "--party",
            "2",
            "--time",
            "Friday 7pm",
            "--notes",
            "outdoor if possible",
        ],
        env=environment(tmp_path),
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "booked"
    assert "booked" in payload["confirmation_text"].casefold()
    assert payload["call_control_id"] == "stub-call-0001"
    assert not (
        tmp_path / "artifacts" / payload["transcript_ref"]
    ).is_file()


def test_book_requires_party(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "book",
            "--to",
            "+14155552671",
            "--name",
            "Nathan",
            "--time",
            "Friday 7pm",
        ],
        env=environment(tmp_path),
    )

    assert result.exit_code == 2
    assert "--party" in result.output


def test_book_requires_time(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "book",
            "--to",
            "+14155552671",
            "--name",
            "Nathan",
            "--party",
            "2",
        ],
        env=environment(tmp_path),
    )

    assert result.exit_code == 2
    assert "--time" in result.output


def test_verify_book_stub_exits_zero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-book", "--mode", "stub"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 0
    assert "10/10 passed" in result.stdout
    assert result.stdout.count("[PASS]") == 10


def test_verify_book_live_requires_gate(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-book", "--mode", "live"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 2
    assert LIVE_GATE_MESSAGE in result.output


def test_verify_book_wrong_did_exits_one_with_fail_lines(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        ["verify-book", "--mode", "stub", "--from-did", "+15555559999"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 1
    assert "[FAIL] did_in_ledger" in result.stdout
    assert "8/10 passed" in result.stdout


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


def test_verify_message_stub_exits_zero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-message"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 0
    assert "6/6 passed" in result.stdout
    assert result.stdout.count("[PASS]") == 6
    assert "[PASS] stub_send" in result.stdout
    assert "[PASS] inbound_map" in result.stdout
    assert "[PASS] inbound_reply" in result.stdout


def test_linq_register_webhook_fails_without_token(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        ["linq", "register-webhook"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 2
    assert "LINQ_API_TOKEN" in result.output


def test_linq_register_webhook_fails_without_url(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        ["linq", "register-webhook"],
        env=environment(tmp_path) | {"LINQ_API_TOKEN": "token-from-env"},
    )

    assert result.exit_code == 2
    assert "LINQ_WEBHOOK_URL" in result.output


def test_verify_webhooks_stub_exits_zero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-webhooks"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 0
    assert "5/5 passed" in result.stdout
    assert result.stdout.count("[PASS]") == 5


def test_verify_linq_call_stub_exits_zero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-linq-call"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 0
    assert "14/14 passed" in result.stdout
    assert result.stdout.count("[PASS]") == 14
    assert "[PASS] parse_book" in result.stdout
    assert "[PASS] collect_time" in result.stdout
    assert "[PASS] collect_phone" in result.stdout
    assert "[PASS] confirm_before_dial" in result.stdout
    assert "[PASS] account_lookup" in result.stdout
    assert "[PASS] account_gate" in result.stdout
    assert "[PASS] kickoff_reply" in result.stdout
    assert "[PASS] cross_link" in result.stdout
    assert "[PASS] from_did_users_row" in result.stdout
    assert "[PASS] no_number_order" in result.stdout


def test_verify_accounts_stub_exits_zero(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-accounts"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 0
    assert "5/5 passed" in result.stdout
    assert result.stdout.count("[PASS]") == 5
    assert "[PASS] cli_roundtrip" in result.stdout
    assert "[PASS] omit_preserve" in result.stdout
    assert "[PASS] sync_hmac" in result.stdout
    assert "[PASS] dashboard_get" in result.stdout
    assert "[PASS] no_name_column" in result.stdout


def test_book_live_without_from_did_exits_two_before_any_socket(
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "book",
            "--to",
            "+14155552671",
            "--name",
            "Nathan",
            "--party",
            "2",
            "--time",
            "Friday 7pm",
            "--live",
        ],
        env=environment(tmp_path)
        | {
            "QUICK_ASK_LIVE": "1",
            "TELNYX_API_KEY": "k",
            "TELNYX_CONNECTION_ID": "c",
            "OPENAI_API_KEY": "o",
            "OPENAI_RESPONSES_MODEL": "gpt-5.6-terra",
            "PUBLIC_BASE_URL": "https://example.test",
            "PUBLIC_WEBHOOK_URL": "https://example.test",
        },
    )

    assert result.exit_code == 2
    assert "--from-did" in result.output
    assert "account upsert" in result.output
    artifacts = tmp_path / "artifacts"
    assert not artifacts.exists() or not any(artifacts.iterdir())


def test_book_stub_uses_users_row_from_did_when_present(
    tmp_path: Path,
) -> None:
    env = environment(tmp_path) | {"RETAIN_CALL_ARTIFACTS": "1"}
    seeded = runner.invoke(
        app,
        [
            "account",
            "upsert",
            "--phone",
            "+14155552671",
            "--name",
            "Nathan",
            "--user",
            "acctnathan",
            "--from-did",
            "+15555550177",
        ],
        env=env,
    )
    result = runner.invoke(
        app,
        [
            "book",
            "--to",
            "+14155552671",
            "--name",
            "Nathan",
            "--party",
            "2",
            "--time",
            "Friday 7pm",
            "--user",
            "acctnathan",
        ],
        env=env,
    )

    assert seeded.exit_code == 0
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    dial = json.loads(
        (
            tmp_path
            / "artifacts"
            / payload["call_control_id"]
            / "dial.json"
        ).read_text()
    )
    assert dial["from"] == "+15555550177"


def test_verify_webhooks_broken_url_fails_loud(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["verify-webhooks", "--url", "http://127.0.0.1:9"],
        env=environment(tmp_path),
    )

    assert result.exit_code == 1
    assert "[FAIL]" in result.stdout
    assert "5/5 passed" not in result.stdout
