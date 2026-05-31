"""Pulse — Configuration management.

Loads settings from environment variables or .env file.
All sensitive values (secret keys, DB URLs) are configured via env.
"""

from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr


# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent  # project root
SRC_DIR = BASE_DIR / "src"
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
ALEMBIC_DIR = BASE_DIR / "alembic"


class Settings(BaseSettings):
    """Application settings loaded from environment.

    Priority: env vars > .env file > defaults.
    """

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────────
    app_name: str = "Pulse"
    app_version: str = "1.0.0"
    app_description: str = "Production-ready monitoring and alerting platform"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./pulse.db"
    db_echo: bool = False
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_recycle: int = 3600

    # ── Security ──────────────────────────────────────────────────────────────
    secret_key: SecretStr = SecretStr("change-me-in-production-very-long-secret-key-here")
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    algorithm: str = "HS256"
    bcrypt_rounds: int = 12

    # ── Monitoring defaults ───────────────────────────────────────────────────
    default_check_interval_seconds: int = 60
    default_timeout_seconds: int = 10
    default_retries: int = 3
    default_retry_delay_seconds: int = 5
    max_check_interval_seconds: int = 3600
    min_check_interval_seconds: int = 10
    max_monitors_per_user: int = 100
    max_alerts_per_monitor: int = 20

    # ── Alerting ──────────────────────────────────────────────────────────────
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = "alerts@pulse.local"
    slack_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_default_chat_id: str = ""
    
    # ── Scheduler ─────────────────────────────────────────────────────────────
    scheduler_enabled: bool = True
    scheduler_max_instances: int = 3
    scheduler_coalesce: bool = True

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: str = "*"

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    rate_limit_requests: int = 100
    rate_limit_period_seconds: int = 60

    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def async_database_url(self) -> str:
        """Return URL compatible with async SQLAlchemy."""
        url = self.database_url
        if url.startswith("sqlite:///"):
            return url.replace("sqlite:///", "sqlite+aiosqlite:///")
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton settings instance."""
    return Settings()
