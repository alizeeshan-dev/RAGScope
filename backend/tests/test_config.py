from pathlib import Path

import pytest
from backend.app.core.config import Settings
from pydantic import ValidationError


def test_settings_have_safe_fake_provider_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.embedding_provider == "fake"
    assert settings.embedding_dimension == 64
    assert settings.artifact_root == (Path.cwd() / "var/artifacts").resolve()


def test_settings_reject_artifacts_inside_application_source() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, artifact_root="backend/app/uploads")


def test_settings_validate_limits() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_upload_bytes=0)


def test_empty_optional_pricing_values_remain_unknown() -> None:
    settings = Settings(
        _env_file=None,
        generation_input_price_per_million="",
        generation_output_price_per_million="",
    )
    assert settings.generation_input_price_per_million is None
    assert settings.generation_output_price_per_million is None
