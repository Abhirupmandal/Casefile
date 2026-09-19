"""
Unit Tests: Configuration Foundation

Validates loading development and test configurations, environment variable
expansion (${VAR} and ${VAR:-default}), SecretStr protections, and fallback logic.
"""

import os
from unittest import mock

import pytest

from casefile.config import (
    AppConfig,
    _expand_env_vars,
    load_config,
)


@pytest.mark.unit
class TestConfigLoading:
    """Verify strongly typed configuration loading."""

    def test_load_development_config(self) -> None:
        cfg = load_config("development")
        assert cfg.environment == "development"
        assert cfg.database.name == "casefile_dev"
        assert cfg.database.port == 5432
        assert cfg.redis.port == 6379
        assert cfg.agents.supervisor.model == "claude-3-5-sonnet-20241022"
        assert cfg.agents.investigator.model == "gpt-4o"
        assert cfg.budget.max_total_tokens == 150_000
        assert cfg.checkpointing.enabled is True

    def test_load_test_config(self) -> None:
        cfg = load_config("test")
        assert cfg.environment == "test"
        assert cfg.database.name == "casefile_test"
        assert cfg.redis.db == 15
        assert cfg.observability.tracing.enabled is False
        assert cfg.human_approval.enabled is False

    def test_secret_str_masking(self) -> None:
        cfg = load_config("development")
        # Ensure passwords and keys are not exposed as plaintext in string representation
        repr_str = repr(cfg.database)
        assert "SecretStr" in repr_str
        assert "casefile_dev_password" not in repr_str

    def test_env_var_expansion(self) -> None:
        with mock.patch.dict(os.environ, {"TEST_HOST": "my-postgres-host"}):
            expanded = _expand_env_vars("postgresql://${TEST_HOST}:5432/db")
            assert expanded == "postgresql://my-postgres-host:5432/db"

    def test_env_var_default_fallback(self) -> None:
        expanded = _expand_env_vars("postgresql://${UNDEFINED_VAR:-fallback-host}:5432/db")
        assert expanded == "postgresql://fallback-host:5432/db"

    def test_fallback_when_file_not_found(self) -> None:
        cfg = load_config("nonexistent_env")
        assert cfg.environment == "nonexistent_env"
        assert isinstance(cfg, AppConfig)
