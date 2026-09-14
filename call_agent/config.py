from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .domain import DEFAULT_RESPONSES_EFFORT_ENV, normalize_responses_effort
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
    # Shared Telnyx FROM for every Treena user (Cap). Fly live sets both
    # TELNYX_FROM_NUMBER and TELNYX_PHONE_NUMBER to the same E.164.
    # Do not default the digits here.
    telnyx_from_number: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "telnyx_from_number",
            "TELNYX_FROM_NUMBER",
            "TELNYX_PHONE_NUMBER",
        ),
    )
    linq_api_token: str | None = None
    # Shared Treena iMessage bot (Cap). One number for everyone.
    # Sandbox +12138653024 until ~Sep 18. Not a per-user DID.
    linq_from_e164: str | None = None
    linq_webhook_signing_secret: str | None = None
    linq_webhook_url: str | None = None
    account_sync_secret: str | None = None
    # Cap locked 2026-09-14: $1.00 per 5 connected minutes (100 cents).
    # Env 0 blocks purchase (ops kill switch). Stripe TEST only (sk_test_).
    metered_pack_price_cents: int | None = 100
    stripe_secret_key: str | None = None
    openai_api_key: str | None = None
    openai_live_model: str = "gpt-live-1"
    openai_live_voice: str = "marin"
    openai_live_extra_headers: str | None = None
    openai_responses_model: str | None = None
    # Cap light → OpenAI session.delegation.responses.reasoning.effort=low.
    openai_responses_effort: str = DEFAULT_RESPONSES_EFFORT_ENV
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

    @field_validator("openai_responses_effort", mode="before")
    @classmethod
    def _normalize_openai_responses_effort(cls, value: object) -> str:
        return normalize_responses_effort(value)

    @field_validator("metered_pack_price_cents", mode="before")
    @classmethod
    def _empty_pack_price(cls, value: object) -> object:
        if value is None or value == "":
            return 100
        return value

    @field_validator("stripe_secret_key", mode="before")
    @classmethod
    def _empty_stripe_secret(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

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
