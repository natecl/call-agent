from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from call_agent.artifacts import (
    LocalArtifactStore,
    R2ArtifactStore,
    artifact_slug,
    durable_keys,
    open_artifact_store,
    publish_durable_artifacts,
    sign_r2_request,
)
from call_agent.config import Settings
from call_agent.persistence import R2ArtifactConfig


def test_local_artifact_put_get_roundtrip(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    key = "stub-call-0001/transcript.json"
    body = b'{"turns":[]}\n'

    store.put(key, body, "application/json")

    assert store.get(key) == body
    assert (tmp_path / key).read_bytes() == body


def test_r2_put_get_with_httpx_mock() -> None:
    objects: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.removeprefix("/calls/")
        if request.method == "PUT":
            objects[key] = request.content
            return httpx.Response(200)
        if request.method == "GET" and key in objects:
            return httpx.Response(200, content=objects[key])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = R2ArtifactStore(
        R2ArtifactConfig(
            account_id="acct",
            access_key_id="key",
            secret_access_key="secret",
            bucket="calls",
            endpoint="https://acct.r2.cloudflarestorage.com",
        ),
        client=client,
    )
    key = "stub-call-0001/call_report.json"
    body = b'{"status":"answered"}\n'

    store.put(key, body, "application/json")

    assert store.get(key) == body
    assert "AWS4-HMAC-SHA256" in sign_r2_request(
        "PUT",
        "https://acct.r2.cloudflarestorage.com/calls/x",
        body,
        "application/json",
        "key",
        "secret",
        datetime(2026, 9, 11, 12, 0, tzinfo=UTC),
    )["authorization"]


def test_open_artifact_store_defaults_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    for name in (
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET",
        "R2_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(
        _env_file=None,
        call_agent_artifacts_dir=tmp_path,
    )
    store = open_artifact_store(settings)
    store.put("a/b.txt", b"hi")
    assert store.get("a/b.txt") == b"hi"


def test_publish_durable_artifacts_puts_documented_subset(tmp_path: Path) -> None:
    call_id = "stub-call-0001"
    run = tmp_path / call_id
    (run / "audio").mkdir(parents=True)
    (run / "transcript.json").write_bytes(b"t")
    (run / "result.json").write_bytes(b"r")
    (run / "call_report.json").write_bytes(b"c")
    (run / "audio" / "inbound.wav").write_bytes(b"in")
    (run / "audio" / "outbound.wav").write_bytes(b"out")
    (run / "events.jsonl").write_bytes(b"local-only")
    published: dict[str, bytes] = {}

    class Capture:
        def put(self, key: str, body: bytes, content_type: str = "") -> None:
            published[key] = body

        def get(self, key: str) -> bytes:
            return published[key]

    publish_durable_artifacts(
        tmp_path, call_id, ("inbound.wav", "outbound.wav"), Capture()
    )

    assert published == {
        "stub-call-0001/transcript.json": b"t",
        "stub-call-0001/result.json": b"r",
        "stub-call-0001/call_report.json": b"c",
        "stub-call-0001/audio/inbound.wav": b"in",
        "stub-call-0001/audio/outbound.wav": b"out",
    }


def test_artifact_slug_sanitizes_colon_and_keeps_safe_chars() -> None:
    assert artifact_slug("v3:stub_colon_0001") == "v3_stub_colon_0001"
    assert artifact_slug("stub-call-0001") == "stub-call-0001"
    assert artifact_slug("a.b-c_d") == "a.b-c_d"


def test_durable_keys_use_slug_not_raw_ccid() -> None:
    keys = durable_keys("v3:stub_colon_0001", ("inbound.wav", "outbound.wav"))
    assert keys[0] == "v3_stub_colon_0001/transcript.json"
    assert all(":" not in key for key in keys)


def test_sign_r2_request_uses_raw_path() -> None:
    from urllib.parse import quote

    key = "v3:stub_colon_0001/result.json"
    url = (
        "https://acct.r2.cloudflarestorage.com/calls/"
        f"{quote(key, safe='/')}"
    )
    parsed = httpx.URL(url)
    assert ":" in parsed.path
    assert "%3A" in parsed.raw_path.decode()
    headers = sign_r2_request(
        "PUT",
        url,
        b"{}",
        "application/json",
        "key",
        "secret",
        datetime(2026, 9, 11, 12, 0, tzinfo=UTC),
    )
    decoded_headers = sign_r2_request(
        "PUT",
        f"https://acct.r2.cloudflarestorage.com/calls/{key}",
        b"{}",
        "application/json",
        "key",
        "secret",
        datetime(2026, 9, 11, 12, 0, tzinfo=UTC),
    )
    assert headers["authorization"] != decoded_headers["authorization"]
    assert "AWS4-HMAC-SHA256" in headers["authorization"]
