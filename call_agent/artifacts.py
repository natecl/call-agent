from __future__ import annotations

import hashlib
import hmac
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

from .config import Settings
from .persistence import (
    ArtifactConfig,
    LocalArtifactConfig,
    PersistenceConfigError,
    R2ArtifactConfig,
)


class ArtifactStore(Protocol):
    def put(
        self,
        key: str,
        body: bytes,
        content_type: str = "application/octet-stream",
    ) -> None: ...

    def get(self, key: str) -> bytes: ...


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(
        self,
        key: str,
        body: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        del content_type
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def get(self, key: str) -> bytes:
        path = self.root / key
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()


class R2ArtifactStore:
    def __init__(
        self,
        config: R2ArtifactConfig,
        client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._client = client

    def put(
        self,
        key: str,
        body: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        response = self._request("PUT", key, body, content_type)
        if response.status_code not in {200, 201}:
            raise PersistenceConfigError(
                f"R2 put failed for {key}: HTTP {response.status_code}"
            )

    def get(self, key: str) -> bytes:
        response = self._request("GET", key, b"", "")
        if response.status_code == 404:
            raise FileNotFoundError(key)
        if response.status_code != 200:
            raise PersistenceConfigError(
                f"R2 get failed for {key}: HTTP {response.status_code}"
            )
        return response.content

    def _request(
        self,
        method: str,
        key: str,
        body: bytes,
        content_type: str,
    ) -> httpx.Response:
        url = (
            f"{self.config.endpoint}/"
            f"{self.config.bucket}/{quote(key, safe='/')}"
        )
        now = datetime.now(UTC)
        headers = sign_r2_request(
            method,
            url,
            body,
            content_type,
            self.config.access_key_id,
            self.config.secret_access_key,
            now,
        )
        client = self._client or httpx.Client()
        owns_client = self._client is None
        try:
            return client.request(method, url, content=body, headers=headers)
        finally:
            if owns_client:
                client.close()


def sign_r2_request(
    method: str,
    url: str,
    body: bytes,
    content_type: str,
    access_key_id: str,
    secret_access_key: str,
    now: datetime,
) -> dict[str, str]:
    parsed = httpx.URL(url)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {
        "host": parsed.host or "",
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if method == "PUT" and content_type:
        headers["content-type"] = content_type
    signed_names = ";".join(sorted(headers))
    canonical_headers = "".join(
        f"{name}:{headers[name]}\n" for name in sorted(headers)
    )
    canonical_request = "\n".join(
        [
            method,
            parsed.path,
            "",
            canonical_headers,
            signed_names,
            payload_hash,
        ]
    )
    credential_scope = f"{datestamp}/auto/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    signing_key = _signing_key(secret_access_key, datestamp)
    signature = hmac.new(
        signing_key, string_to_sign.encode(), hashlib.sha256
    ).hexdigest()
    headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_names}, Signature={signature}"
    )
    return headers


def _signing_key(secret: str, datestamp: str) -> bytes:
    date_key = hmac.new(
        f"AWS4{secret}".encode(), datestamp.encode(), hashlib.sha256
    ).digest()
    region_key = hmac.new(date_key, b"auto", hashlib.sha256).digest()
    service_key = hmac.new(region_key, b"s3", hashlib.sha256).digest()
    return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()


def open_artifact_store(
    settings: Settings,
    client_factory: Callable[[], httpx.Client] | None = None,
) -> ArtifactStore:
    config = settings.artifact_config()
    return store_from_config(config, client_factory)


def store_from_config(
    config: ArtifactConfig,
    client_factory: Callable[[], httpx.Client] | None = None,
) -> ArtifactStore:
    if isinstance(config, LocalArtifactConfig):
        return LocalArtifactStore(config.root)
    client = client_factory() if client_factory is not None else None
    return R2ArtifactStore(config, client=client)


DURABLE_RELATIVE = (
    "transcript.json",
    "result.json",
    "call_report.json",
)


def durable_keys(
    call_control_id: str, audio_names: tuple[str, str]
) -> tuple[str, ...]:
    inbound, outbound = audio_names
    names = (
        *DURABLE_RELATIVE,
        f"audio/{inbound}",
        f"audio/{outbound}",
    )
    return tuple(f"{call_control_id}/{name}" for name in names)


def publish_durable_artifacts(
    root: Path,
    call_control_id: str,
    audio_names: tuple[str, str],
    store: ArtifactStore,
) -> None:
    run_dir = root / call_control_id
    for key in durable_keys(call_control_id, audio_names):
        relative = key.removeprefix(f"{call_control_id}/")
        path = run_dir / relative
        if not path.is_file():
            raise FileNotFoundError(key)
        content_type = (
            "application/json"
            if relative.endswith(".json")
            else "audio/wav" if relative.endswith(".wav")
            else "application/octet-stream"
        )
        store.put(key, path.read_bytes(), content_type)


def wipe_run_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
