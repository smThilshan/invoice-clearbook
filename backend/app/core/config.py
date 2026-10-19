"""Application settings, loaded from environment variables and the repo-root `.env`."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# backend/app/core/config.py -> repo root. `make` runs from backend/, so a relative path misses it.
REPO_ROOT = Path(__file__).resolve().parents[3]

_PLACEHOLDER_SECRETS = frozenset({"", "change-me"})

AppEnv = Literal["dev", "test", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env also holds values for docker compose and later tasks
    )

    app_env: AppEnv = "dev"
    log_level: LogLevel = "INFO"
    default_client_id: str = "missan"

    postgres_user: str = "clearbook"
    postgres_password: SecretStr = SecretStr("change-me")
    postgres_db: str = "clearbook"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    @property
    def database_url(self) -> URL:
        # URL escapes the password and masks it in repr().
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )

    @model_validator(mode="after")
    def _no_placeholder_secrets_in_prod(self) -> Self:
        if self.app_env == "prod" and (
            self.postgres_password.get_secret_value() in _PLACEHOLDER_SECRETS
        ):
            raise ValueError("POSTGRES_PASSWORD must be set to a real value in prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
