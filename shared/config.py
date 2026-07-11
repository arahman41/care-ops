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

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
