"""Central configuration loaded from environment variables."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "care_ops"
    postgres_user: str = "care_ops"
    postgres_password: str = "change_me"

    whisper_model_size: str = "base"

    intake_url: str = "http://intake:8000"
    prior_auth_url: str = "http://agent-prior-auth:8000"
    care_gap_url: str = "http://agent-care-gap:8000"
    coding_url: str = "http://agent-coding:8000"

    # P2-6: how long the orchestrator waits on one agent before recording a
    # timeout and moving on. 60s is 3.9x the p95 (15,517ms) and 3.3x the max
    # (18,102ms) measured for the routed coding configuration over 113
    # held-out notes in P2-4. Prior-auth and care-gap latency are unmeasured.
    # Configurable so the timeout test runs in a second, not a minute.
    agent_timeout_seconds: float = 60.0

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
