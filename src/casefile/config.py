"""
CASEFILE Strongly-Typed Configuration Foundation

Loads and validates environment and YAML configurations using Pydantic v2.
Supports environment variable expansion (${VAR} or ${VAR:-default})
and clean separation of development and test environments.
"""

from __future__ import annotations

import os
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr

# Regex for matching environment variable patterns: ${VAR} or ${VAR:-default}
ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)(?::-(.*?))?\}")


def _expand_env_vars(raw: str) -> str:
    """Expand ${VAR} and ${VAR:-default} patterns using os.environ."""

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        default_val = match.group(2) if match.group(2) is not None else ""
        return os.environ.get(var_name, default_val)

    return ENV_VAR_PATTERN.sub(_replace, raw)


def _deep_expand(val: Any) -> Any:
    """Recursively expand environment variables in dicts, lists, and strings."""
    if isinstance(val, str):
        return _expand_env_vars(val)
    elif isinstance(val, dict):
        return {k: _deep_expand(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_deep_expand(item) for item in val]
    return val


class DatabaseConfig(BaseModel):
    """SQLite local persistence settings.

    The database location is a URL so file paths stay in configuration, not
    in source code. Override with the CASEFILE_DATABASE_URL environment
    variable; SQLite is the default. No server, host, port, or password.
    """

    url: str = Field(default="sqlite:///./casefile.db")

    @property
    def path(self) -> str:
        """Return the filesystem path (or ':memory:') for a sqlite:// URL."""
        prefix = "sqlite:///"
        if self.url == "sqlite:///:memory:":
            return ":memory:"
        if self.url.startswith(prefix):
            return self.url[len(prefix) :] or ":memory:"
        raise ValueError(f"Unsupported database URL scheme: {self.url!r}")


class RedisConfig(BaseModel):
    """Redis caching and rate limiting configuration."""

    host: str = Field(default="localhost")
    port: int = Field(default=6379, ge=1, le=65535)
    db: int = Field(default=0, ge=0)
    max_connections: int = Field(default=50, ge=1)
    socket_timeout: int = Field(default=5, ge=1)

    @property
    def url(self) -> str:
        """Construct Redis connection string."""
        return f"redis://{self.host}:{self.port}/{self.db}"


class OpenAIProviderConfig(BaseModel):
    """OpenAI provider credentials and endpoint settings."""

    api_key: SecretStr = Field(default=SecretStr(""))
    organization: SecretStr = Field(default=SecretStr(""))
    base_url: str = Field(default="https://api.openai.com/v1")


class AnthropicProviderConfig(BaseModel):
    """Anthropic provider credentials and endpoint settings."""

    api_key: SecretStr = Field(default=SecretStr(""))
    base_url: str = Field(default="https://api.anthropic.com/v1")


class LLMConfig(BaseModel):
    """LLM provider configurations."""

    openai: OpenAIProviderConfig = Field(default_factory=OpenAIProviderConfig)
    anthropic: AnthropicProviderConfig = Field(default_factory=AnthropicProviderConfig)


class AgentModelConfig(BaseModel):
    """Individual agent execution parameters."""

    provider: str = Field(default="anthropic")
    model: str = Field(default="claude-3-5-sonnet-20241022")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)


class AgentsConfig(BaseModel):
    """All agent specifications."""

    supervisor: AgentModelConfig = Field(
        default_factory=lambda: AgentModelConfig(
            provider="anthropic", model="claude-3-5-sonnet-20241022", temperature=0.0
        )
    )
    extractor: AgentModelConfig = Field(
        default_factory=lambda: AgentModelConfig(
            provider="anthropic", model="claude-3-5-sonnet-20241022", temperature=0.0
        )
    )
    investigator: AgentModelConfig = Field(
        default_factory=lambda: AgentModelConfig(provider="openai", model="gpt-4o", temperature=0.3)
    )
    reviewer: AgentModelConfig = Field(
        default_factory=lambda: AgentModelConfig(
            provider="anthropic", model="claude-3-5-sonnet-20241022", temperature=0.0
        )
    )


class BudgetConfig(BaseModel):
    """Default multi-dimensional budget limits."""

    max_input_tokens: int = Field(default=100_000, ge=1)
    max_output_tokens: int = Field(default=20_000, ge=1)
    max_total_tokens: int = Field(default=150_000, ge=1)
    max_cost_usd: Decimal = Field(default=Decimal("5.00"), gt=Decimal("0.00"))
    max_steps: int = Field(default=50, ge=1)
    max_execution_time_seconds: int = Field(default=1800, ge=1)
    max_rework_cycles: int = Field(default=3, ge=1)


class ToolConfig(BaseModel):
    """Settings for an individual external tool."""

    cache_ttl: int = Field(default=3600, ge=0)
    rate_limit: int = Field(default=100, ge=1)
    timeout: int = Field(default=5, ge=1)


class ToolsConfig(BaseModel):
    """Tool limits, timeouts, and cache TTL configuration."""

    policy_lookup: ToolConfig = Field(
        default_factory=lambda: ToolConfig(cache_ttl=3600, rate_limit=100, timeout=5)
    )
    claim_history_lookup: ToolConfig = Field(
        default_factory=lambda: ToolConfig(cache_ttl=3600, rate_limit=50, timeout=5)
    )
    repair_cost_lookup: ToolConfig = Field(
        default_factory=lambda: ToolConfig(cache_ttl=3600, rate_limit=100, timeout=5)
    )
    fraud_signal_lookup: ToolConfig = Field(
        default_factory=lambda: ToolConfig(cache_ttl=1800, rate_limit=50, timeout=5)
    )
    document_retrieval: ToolConfig = Field(
        default_factory=lambda: ToolConfig(cache_ttl=7200, rate_limit=20, timeout=10)
    )


class TracingConfig(BaseModel):
    """OpenTelemetry tracing exporter settings."""

    enabled: bool = Field(default=True)
    exporter: str = Field(default="jaeger")
    endpoint: str = Field(default="http://localhost:14268/api/traces")
    otlp_endpoint: str = Field(default="http://localhost:4318")
    sampling_rate: float = Field(default=1.0, ge=0.0, le=1.0)


class MetricsConfig(BaseModel):
    """OpenTelemetry metrics exporter settings."""

    enabled: bool = Field(default=True)
    exporter: str = Field(default="prometheus")
    endpoint: str = Field(default="http://localhost:9090")


class LoggingConfig(BaseModel):
    """Structured logging configuration."""

    level: str = Field(default="DEBUG")
    format: str = Field(default="json")
    output: str = Field(default="stdout")


class ObservabilityConfig(BaseModel):
    """Full telemetry and logging settings."""

    service_name: str = Field(default="casefile")
    environment: str = Field(default="development")
    tracing: TracingConfig = Field(default_factory=TracingConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


class CheckpointingConfig(BaseModel):
    """Checkpoint creation and retention policy."""

    enabled: bool = Field(default=True)
    checkpoint_after_agent: bool = Field(default=True)
    checkpoint_on_budget_threshold: bool = Field(default=True)
    prune_after_completion: bool = Field(default=False)


class HumanApprovalConfig(BaseModel):
    """Settings for the human-in-the-loop approval gate."""

    enabled: bool = Field(default=True)
    timeout_hours: int = Field(default=24, ge=1)
    notification_channels: list[str] = Field(default_factory=lambda: ["email", "slack"])


class ApiSecurityConfig(BaseModel):
    """Production API security and network parameters."""

    debug: bool = Field(default=False)
    allowed_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "casefile.internal"]
    )
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]
    )
    cors_allow_credentials: bool = Field(default=True)
    max_request_body_bytes: int = Field(default=2 * 1024 * 1024, ge=1024)
    max_description_length: int = Field(default=10_000, ge=100)
    auth_mode: str = Field(default="development")
    enable_security_headers: bool = Field(default=True)
    content_security_policy: str = Field(
        default=(
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' fonts.googleapis.com; "
            "font-src 'self' fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self';"
        )
    )
    strict_transport_security: bool = Field(default=False)


class AppConfig(BaseModel):
    """Root configuration model for CASEFILE."""

    environment: str = Field(default="development")
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    checkpointing: CheckpointingConfig = Field(default_factory=CheckpointingConfig)
    human_approval: HumanApprovalConfig = Field(default_factory=HumanApprovalConfig)
    security: ApiSecurityConfig = Field(default_factory=ApiSecurityConfig)

    def validate_production(self) -> None:
        """Enforce fail-closed checks on security-critical parameters in production."""
        if self.environment == "production":
            if self.security.debug:
                raise ValueError("Insecure configuration: debug cannot be True in production")
            if self.security.cors_allow_credentials and "*" in self.security.cors_allowed_origins:
                raise ValueError(
                    "Insecure configuration: wildcard CORS origin with credentials is not permitted in production"
                )
            if not self.security.cors_allowed_origins:
                raise ValueError(
                    "Insecure configuration: cors_allowed_origins must not be empty in production"
                )


def load_config(
    env: str | None = None,
    config_file: str | Path | None = None,
) -> AppConfig:
    """
    Load configuration from YAML file and apply environment variable expansions.
    Defaults to CASEFILE_ENV or 'development'.
    """
    selected_env: str = (
        env if env is not None else (os.environ.get("CASEFILE_ENV") or "development")
    )

    if config_file is not None:
        target_path = Path(config_file)
    else:
        # Check standard config locations
        candidates = [
            Path("config") / f"{selected_env}.yaml",
            Path(__file__).resolve().parent.parent.parent / "config" / f"{selected_env}.yaml",
        ]
        target_path = next((p for p in candidates if p.exists()), candidates[0])

    if not target_path.exists():
        # Fall back to default AppConfig if file not found
        cfg = AppConfig(environment=selected_env)
        cfg.validate_production()
        return cfg

    with open(target_path, encoding="utf-8") as f:
        raw_yaml = yaml.safe_load(f) or {}

    expanded = _deep_expand(raw_yaml)
    expanded["environment"] = selected_env
    cfg = AppConfig.model_validate(expanded)
    cfg.validate_production()
    return cfg


@lru_cache(maxsize=4)
def get_config(env: str | None = None) -> AppConfig:
    """Cached singleton accessor for application configuration."""
    return load_config(env=env)
