"""Application configuration loaded from environment variables.

Configuration values are read from, in order of precedence:

1. Real environment variables
2. A local ``.env`` file (see ``.env.example`` for the supported keys)
3. The defaults declared on :class:`Settings`

Usage::

    from app.core.config import get_settings

    settings = get_settings()
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly typed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application metadata ---
    app_name: str = Field(default="Messenger API", description="Human readable service name.")
    app_env: str = Field(default="development", description="Deployment environment name.")
    app_debug: bool = Field(default=False, description="Enable debug behaviour and verbose errors.")
    app_version: str = Field(default="0.1.0", description="Semantic version of the API.")

    # --- API ---
    api_v1_prefix: str = Field(default="/api/v1", description="Prefix for version 1 endpoints.")

    # --- Server ---
    host: str = Field(default="127.0.0.1", description="Bind host for the ASGI server.")
    port: int = Field(default=8000, ge=1, le=65535, description="Bind port for the ASGI server.")

    # --- CORS ---
    cors_origins: list[str] = Field(
        default_factory=list,
        description="Origins allowed to call the API from a browser.",
    )

    @field_validator("api_v1_prefix")
    @classmethod
    def _normalise_api_prefix(cls, value: str) -> str:
        """Ensure the API prefix always starts with ``/`` and has no trailing ``/``."""
        prefix = value.strip()
        if not prefix.startswith("/"):
            prefix = f"/{prefix}"
        return prefix.rstrip("/")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Allow CORS origins to be provided as a comma-separated string."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance.

    The cache keeps configuration reads cheap while still allowing the
    environment to be loaded only once per process.
    """
    return Settings()
