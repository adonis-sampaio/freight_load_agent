"""Environment-based application settings."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration loaded from the process environment."""

    model_config = SettingsConfigDict(
        env_prefix="FREIGHT_",
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = "freight-load-sop-agent"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: SecretStr | None = None
    llm_provider: str | None = None
    llm_model_id: str | None = None
    llm_api_key: SecretStr | None = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_max_input_tokens: int = Field(default=12000, gt=0)
    llm_max_output_tokens: int = Field(default=2048, gt=0)
    llm_max_calls_per_load: int = Field(default=20, gt=0)
    llm_max_cost_per_load_usd: float = Field(default=1.0, gt=0)
    llm_reserved_cost_per_call_usd: float = Field(default=0.05, gt=0)
    eta_request_timeout_seconds: int = Field(default=1800, gt=0, le=86400)
    max_eta_reminders: int = Field(default=2, ge=1, le=10)


@lru_cache
def get_settings() -> Settings:
    """Return cached process settings."""
    return Settings()
