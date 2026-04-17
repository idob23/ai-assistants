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

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
