"""
Application configuration.

CONCEPT: "Never hard-code API keys."

All secrets and environment-specific values live in a `.env` file (git-ignored)
and are read here exactly once. Every other module imports `settings` from this
file instead of calling `os.getenv(...)` all over the place. That gives you:

  * one place to see every knob the app has,
  * type validation (a typo'd port becomes a clear startup error, not a
    mysterious runtime crash),
  * sensible defaults for local development.

`pydantic-settings` reads, in priority order:
    1. real environment variables
    2. the `.env` file
    3. the defaults declared below
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # ignore unrelated env vars instead of failing
    )

    # ----- LLM ---------------------------------------------------------------
    # "<provider>:<model>" string understood by langchain's init_chat_model.
    llm_model: str = "openai:gpt-4o-mini"
    # Not read directly by our code: langchain-openai picks OPENAI_API_KEY up
    # from the environment. Declared here so a missing key is visible.
    openai_api_key: str | None = None

    # ----- External API -----------------------------------------------------
    openlibrary_base_url: str = "https://openlibrary.org"
    openlibrary_contact: str = "student@example.com"
    external_api_timeout_seconds: float = 10.0

    # For your assignment you would add something like:
    # ticketmaster_api_key: str
    # ticketmaster_base_url: str = "https://app.ticketmaster.com/discovery/v2"

    # ----- App ----------------------------------------------------------------
    cors_origins: str = "http://localhost:8501,http://localhost:3000"
    checkpoint_backend: Literal["memory", "sqlite"] = "memory"
    sqlite_path: str = "./sessions.db"
    log_level: str = "INFO"
    # Safety valve: how many tool-call rounds the agent may take per message.
    max_agent_steps: int = Field(default=12, ge=1, le=50)
    # Corporate networks (Zscaler, etc.) re-sign HTTPS traffic and Python's
    # bundled CA list rejects it. Setting this to true makes Python trust the
    # OS keychain instead (pip install truststore). Affects ALL outbound HTTPS:
    # the external API *and* the LLM provider.
    use_system_certs: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is parsed only once per process."""
    return Settings()


settings = get_settings()
