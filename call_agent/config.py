from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telnyx_api_key: str | None = None
    telnyx_connection_id: str | None = None
    telnyx_public_key: str | None = None
    telnyx_stream_codec: str = "L16"
    openai_api_key: str | None = None
    openai_live_model: str = "gpt-live-1"
    openai_live_voice: str = "marin"
    openai_live_extra_headers: str | None = None
    public_base_url: str = "https://stub.invalid"
    port: int = Field(default=8000, validation_alias="CALL_AGENT_PORT")
    call_agent_db_path: Path = Path("./call_agent.sqlite3")
    call_agent_artifacts_dir: Path = Path("./artifacts")
    quick_ask_live: bool = False
    event_timeout_s: float = 30.0
    hangup_trailing_silence_s: float = 0.4
    hangup_drain_timeout_s: float = 10.0
