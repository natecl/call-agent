from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .persistence import (
    ArtifactConfig,
    LedgerConfig,
    parse_artifact_config,
    parse_ledger_config,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", populate_by_name=True
    )

    telnyx_api_key: str | None = None
    telnyx_connection_id: str | None = None
    telnyx_public_key: str | None = None
    telnyx_stream_codec: str = "L16"
    linq_api_token: str | None = None
    # Shared Treena iMessage bot (Cap). One number for everyone.
    # Sandbox +12138653024 until ~Sep 18. Not a per-user DID.
    linq_from_e164: str | None = None
    linq_webhook_signing_secret: str | None = None
    linq_webhook_url: str | None = None
    account_sync_secret: str | None = None
    openai_api_key: str | None = None
    openai_live_model: str = "gpt-live-1"
    openai_live_voice: str = "marin"
    openai_live_extra_headers: str | None = None
    openai_responses_model: str | None = None
    public_base_url: str = "https://stub.invalid"
    public_webhook_url: str | None = None
    port: int = Field(default=8000, validation_alias="CALL_AGENT_PORT")
    call_agent_db_path: Path = Path("./call_agent.sqlite3")
    call_agent_artifacts_dir: Path = Path("./artifacts")
    neon_database_url: str | None = None
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None
    r2_endpoint: str | None = None
    quick_ask_live: bool = False
    event_timeout_s: float = 30.0
    live_ingress_timeout_s: float = 15.0
    hangup_trailing_silence_s: float = 0.4
    hangup_drain_timeout_s: float = 10.0
    live_silence_failsafe_s: float = 20.0
    farewell_idle_s: float = 0.8
    farewell_max_s: float = 4.0
    hangup_confirm_timeout_s: float = 5.0
    retain_call_artifacts: bool = False

    def ledger_config(self) -> LedgerConfig:
        return parse_ledger_config(
            self.neon_database_url, self.call_agent_db_path
        )

    def artifact_config(self) -> ArtifactConfig:
        return parse_artifact_config(
            self.r2_account_id,
            self.r2_access_key_id,
            self.r2_secret_access_key,
            self.r2_bucket,
            self.call_agent_artifacts_dir,
            self.r2_endpoint,
        )
