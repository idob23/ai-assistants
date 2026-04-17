"""Загрузка и валидация конфигурации из .env файла."""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    CLAUDE_API_KEY: str
    CLAUDE_MODEL: str = "claude-sonnet-4-20250514"
    CLAUDE_BASE_URL: str = "https://api.anthropic.com"
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5433/autoelectric"
    WHISPER_MODEL: str = "small"
    LOG_LEVEL: str = "INFO"
    TELEGRAM_ALLOWED_USERS: str = ""

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    @property
    def allowed_user_ids(self) -> list[int]:
        if not self.TELEGRAM_ALLOWED_USERS:
            return []
        return [int(x.strip()) for x in self.TELEGRAM_ALLOWED_USERS.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
