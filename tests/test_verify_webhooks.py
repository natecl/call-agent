from pathlib import Path

from call_agent.config import Settings
from call_agent.runtime import webhook_and_stream_urls
from call_agent.verify_webhooks import VerifyWebhooksInput, verify_webhooks

REPO = Path(__file__).resolve().parents[1]


def test_verify_webhooks_stub_passes() -> None:
    report = verify_webhooks()
    assert report.passed
    assert [check.name for check in report.checks] == [
        "health",
        "signature",
        "idempotency",
        "linq_hook",
        "media_gap",
    ]
    assert all(check.passed for check in report.checks)
    assert "5/5 passed" in report.stdout


def test_verify_webhooks_missing_key_fails_signature() -> None:
    report = verify_webhooks(VerifyWebhooksInput(omit_public_key=True))
    assert report.passed is False
    by_name = {check.name: check for check in report.checks}
    assert by_name["signature"].passed is False
    assert "5/5 passed" not in report.stdout


def test_webhook_url_splits_from_media_origin() -> None:
    settings = Settings(
        _env_file=None,
        public_base_url="https://tunnel.example.test",
        public_webhook_url="https://call-agent-webhooks.example.workers.dev",
    )
    webhook_url, stream_url = webhook_and_stream_urls(settings)
    assert (
        webhook_url
        == "https://call-agent-webhooks.example.workers.dev/webhooks/telnyx"
    )
    assert stream_url == "wss://tunnel.example.test/media-stream"


def test_webhook_url_follows_public_base_when_unset() -> None:
    settings = Settings(
        _env_file=None,
        public_base_url="https://tunnel.example.test",
    )
    webhook_url, stream_url = webhook_and_stream_urls(settings)
    assert webhook_url == "https://tunnel.example.test/webhooks/telnyx"
    assert stream_url == "wss://tunnel.example.test/media-stream"


def test_feature_maps_record_always_on_versus_tunnel() -> None:
    feature_map = (REPO / "docs" / "FEATURE_MAP.md").read_text()
    assert "## Always-on webhooks" in feature_map
    assert "`POST /webhooks/telnyx` Worker" in feature_map
    assert "local FastAPI tunnel" in feature_map
    assert "`verify-webhooks`" in feature_map
    index = (
        REPO
        / ".cursor"
        / "skills"
        / "verify-call-agent"
        / "features"
        / "README.md"
    ).read_text()
    assert "./webhooks.md" in index
    assert "./verify-webhooks.md" in index
    assert "./linq-inbound.md" in index
    linq_inbound = (
        REPO
        / ".cursor"
        / "skills"
        / "verify-call-agent"
        / "features"
        / "linq-inbound.md"
    ).read_text()
    assert "Linq inbound (closed alpha)" in linq_inbound
    assert "does not send SMS" in linq_inbound or "does not send or receive SMS" in linq_inbound
    webhooks = (
        REPO
        / ".cursor"
        / "skills"
        / "verify-call-agent"
        / "features"
        / "webhooks.md"
    ).read_text()
    assert "Always-on webhooks versus local tunnel" in webhooks
    assert "PUBLIC_WEBHOOK_URL" in webhooks
    assert "call-agent-webhooks" in webhooks
    assert "no `call-agent-web` map" in webhooks
    linq_url = (
        "https://call-agent-webhooks.blossom-linen.workers.dev/linq/webhook"
    )
    assert linq_url in feature_map
    assert linq_url in webhooks
    assert linq_url in linq_inbound
    readme = (REPO / "README.md").read_text()
    worker_readme = (REPO / "workers" / "webhooks" / "README.md").read_text()
    assert linq_url in readme
    assert linq_url in worker_readme
    assert "Closed alpha includes iMessage inbound" in readme
    assert "SMS are out of scope" in readme
