from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings with safe local-development defaults."""

    model_config = SettingsConfigDict(
        env_prefix="RAGSCOPE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "sqlite:///./ragscope.db"
    artifact_root: Path = Path("./var/artifacts")
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    embedding_provider: str = "fake"
    embedding_model: str = "fake-hash-embedding-v1"
    embedding_dimension: int = Field(default=64, ge=1, le=4096)
    embedding_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    generation_provider: str = "fake"
    generation_model: str = "fake-generation-v1"
    generation_base_url: str = "https://api.openai.com/v1"
    generation_api_key: SecretStr | None = None
    generation_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    generation_input_price_per_million: float | None = Field(default=None, ge=0)
    generation_output_price_per_million: float | None = Field(default=None, ge=0)
    embedding_input_price_per_million: float | None = Field(default=None, ge=0)
    experiment_cost_limit: float | None = Field(default=None, ge=0)
    job_api_default_wait: bool = False
    gemini_api_key: SecretStr | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com"
    gemini_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_embedding_dimension: int = Field(default=768, ge=1, le=4096)
    gemini_embedding_batch_size: int = Field(default=100, ge=1, le=100)
    gemini_embedding_task_type: str = "SEMANTIC_SIMILARITY"
    cors_origins: str = "http://localhost:3000"

    @field_validator(
        "generation_input_price_per_million",
        "generation_output_price_per_million",
        "embedding_input_price_per_million",
        "experiment_cost_limit",
        mode="before",
    )
    @classmethod
    def empty_optional_price_is_unknown(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("artifact_root")
    @classmethod
    def reject_source_artifact_root(cls, value: Path) -> Path:
        # Resolve at settings construction time. Keeping a relative path here made
        # artifact placement depend on later ``chdir`` calls (notably test runners
        # and worker processes), which could redirect research artifacts.
        normalized = value.expanduser().resolve()
        parts = {part.casefold() for part in normalized.parts}
        if "backend" in parts and "app" in parts:
            raise ValueError("artifact_root must be outside executable application directories")
        return normalized

    @property
    def allowed_cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
