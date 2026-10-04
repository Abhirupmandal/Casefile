"""
Unit Tests: Configuration Foundation

Validates loading development and test configurations, environment variable
expansion (${VAR} and ${VAR:-default}), SQLite database URL handling, and
fallback logic.
"""

import os
from unittest import mock

import pytest

from casefile.config import (
    AppConfig,
    _expand_env_vars,
    load_config,
)


def _load_without_db_env(env: str) -> AppConfig:
    """Load config with database env overrides removed for deterministic asserts."""
    with mock.patch.dict(os.environ):
        os.environ.pop("CASEFILE_DATABASE_URL", None)
        return load_config(env)


@pytest.mark.unit
class TestConfigLoading:
    """Verify strongly typed configuration loading."""

    def test_load_development_config(self) -> None:
        cfg = _load_without_db_env("development")
        assert cfg.environment == "development"
        assert cfg.database.url == "sqlite:///./casefile.db"
        assert cfg.database.path == "./casefile.db"
        assert cfg.redis.port == 6379
        assert cfg.agents.supervisor.model == "claude-3-5-sonnet-20241022"
        assert cfg.agents.investigator.model == "gpt-4o"
        assert cfg.budget.max_total_tokens == 150_000
        assert cfg.checkpointing.enabled is True

    def test_load_test_config(self) -> None:
        cfg = _load_without_db_env("test")
        assert cfg.environment == "test"
        assert cfg.database.url == "sqlite:///./casefile_test.db"
        assert cfg.redis.db == 15
        assert cfg.observability.tracing.enabled is False
        assert cfg.human_approval.enabled is False

    def test_database_url_env_override(self) -> None:
        with mock.patch.dict(os.environ, {"CASEFILE_DATABASE_URL": "sqlite:////tmp/override.db"}):
            cfg = load_config("development")
            assert cfg.database.url == "sqlite:////tmp/override.db"
            assert cfg.database.path == "/tmp/override.db"

    def test_database_url_memory(self) -> None:
        with mock.patch.dict(os.environ, {"CASEFILE_DATABASE_URL": "sqlite:///:memory:"}):
            cfg = load_config("test")
            assert cfg.database.url == "sqlite:///:memory:"
            assert cfg.database.path == ":memory:"

    def test_env_var_expansion(self) -> None:
        with mock.patch.dict(os.environ, {"TEST_HOST": "my-sqlite-host"}):
            expanded = _expand_env_vars("sqlite://${TEST_HOST}/casefile.db")
            assert expanded == "sqlite://my-sqlite-host/casefile.db"

    def test_env_var_default_fallback(self) -> None:
        expanded = _expand_env_vars("sqlite://${UNDEFINED_VAR:-fallback-host}/casefile.db")
        assert expanded == "sqlite://fallback-host/casefile.db"

    def test_fallback_when_file_not_found(self) -> None:
        cfg = load_config("nonexistent_env")
        assert cfg.environment == "nonexistent_env"
        assert isinstance(cfg, AppConfig)
