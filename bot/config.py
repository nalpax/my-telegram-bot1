"""
Application configuration.

All secrets (bot token, payment provider token, database credentials) are
read from environment variables / a .env file — never hardcoded.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Telegram
    bot_token: str
    admin_ids: str = ""  # comma-separated telegram user ids, e.g. "123456,789012"

    # Database
    # Leave DATABASE_URL empty to auto-use a local SQLite file under
    # DATA_DIR (this is what file-storage-only hosts like BotHost expect —
    # they persist DATA_DIR across restarts but don't offer a managed
    # Postgres server). Set DATABASE_URL explicitly (e.g. for the
    # docker-compose Postgres setup) to override this.
    database_url: str = ""
    data_dir: str = "/app/data"

    # Payments
    # Leave empty to sell in Telegram Stars (currency must then be "XTR").
    payment_provider_token: str = ""
    currency: str = "RUB"

    # Misc
    log_level: str = "INFO"
    broadcast_delay_seconds: float = 0.05
    notify_admins_on_sale: bool = True

    @property
    def admin_id_list(self) -> list[int]:
        result: list[int] = []
        for raw in self.admin_ids.split(","):
            raw = raw.strip()
            if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
                result.append(int(raw))
        return result

    @property
    def uses_stars(self) -> bool:
        return self.currency.upper() == "XTR"

    @property
    def payments_enabled(self) -> bool:
        """Stars need no provider token; any other currency does."""
        return self.uses_stars or bool(self.payment_provider_token)

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.data_dir.rstrip('/')}/shop.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
