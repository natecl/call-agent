from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PersistenceConfigError(ValueError):
    pass


class SqliteLedgerConfig(FrozenModel):
    kind: Literal["sqlite"] = "sqlite"
    path: Path


class NeonLedgerConfig(FrozenModel):
    kind: Literal["neon"] = "neon"
    database_url: str = Field(min_length=1)


class LocalArtifactConfig(FrozenModel):
    kind: Literal["local"] = "local"
    root: Path


class R2ArtifactConfig(FrozenModel):
    kind: Literal["r2"] = "r2"
    account_id: str = Field(min_length=1)
    access_key_id: str = Field(min_length=1)
    secret_access_key: str = Field(min_length=1)
    bucket: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)


LedgerConfig = SqliteLedgerConfig | NeonLedgerConfig
ArtifactConfig = LocalArtifactConfig | R2ArtifactConfig

R2_REQUIRED = (
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
)


def _present(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def parse_ledger_config(
    neon_database_url: str | None,
    sqlite_path: Path,
) -> LedgerConfig:
    url = _present(neon_database_url)
    if url is None:
        return SqliteLedgerConfig(path=sqlite_path)
    if "://" not in url:
        raise PersistenceConfigError(
            "NEON_DATABASE_URL is misconfigured: "
            "expected a postgresql:// URL"
        )
    return NeonLedgerConfig(database_url=url)


def parse_artifact_config(
    r2_account_id: str | None,
    r2_access_key_id: str | None,
    r2_secret_access_key: str | None,
    r2_bucket: str | None,
    artifacts_dir: Path,
    r2_endpoint: str | None = None,
) -> ArtifactConfig:
    values = {
        "R2_ACCOUNT_ID": _present(r2_account_id),
        "R2_ACCESS_KEY_ID": _present(r2_access_key_id),
        "R2_SECRET_ACCESS_KEY": _present(r2_secret_access_key),
        "R2_BUCKET": _present(r2_bucket),
    }
    set_names = [name for name, value in values.items() if value is not None]
    if not set_names:
        return LocalArtifactConfig(root=artifacts_dir)
    missing = [name for name in R2_REQUIRED if values[name] is None]
    if missing:
        raise PersistenceConfigError(
            "R2 is misconfigured: missing "
            + ", ".join(missing)
            + " (need "
            + ", ".join(R2_REQUIRED)
            + ")"
        )
    account_id = values["R2_ACCOUNT_ID"]
    access_key_id = values["R2_ACCESS_KEY_ID"]
    secret_access_key = values["R2_SECRET_ACCESS_KEY"]
    bucket = values["R2_BUCKET"]
    if (
        account_id is None
        or access_key_id is None
        or secret_access_key is None
        or bucket is None
    ):
        raise PersistenceConfigError(
            "R2 is misconfigured: need " + ", ".join(R2_REQUIRED)
        )
    endpoint = _present(r2_endpoint) or (
        f"https://{account_id}.r2.cloudflarestorage.com"
    )
    return R2ArtifactConfig(
        account_id=account_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        bucket=bucket,
        endpoint=endpoint.rstrip("/"),
    )
