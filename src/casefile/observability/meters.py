"""
Casefile meters: the exact §14 metric set with enforced low cardinality.

Every record() call updates an internal in-memory store (deterministic,
test-assertable) and forwards to OTel instruments when configured.
Forbidden label keys (claim/execution/approval IDs, user IDs) raise
immediately so misuse fails loudly in tests instead of leaking into
production cardinality.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

FORBIDDEN_LABEL_KEYS = frozenset(
    {
        "claim_id",
        "casefile.claim_id",
        "execution_id",
        "casefile.execution_id",
        "approval_id",
        "casefile.approval_id",
        "user_id",
        "approver_id",
        "checkpoint_id",
        "casefile.checkpoint_id",
    }
)

METRIC_NAMES = (
    "casefile.workflow.runs",
    "casefile.workflow.duration",
    "casefile.workflow.failures",
    "casefile.agent.executions",
    "casefile.agent.duration",
    "casefile.agent.failures",
    "casefile.tool.invocations",
    "casefile.tool.duration",
    "casefile.tool.failures",
    "casefile.workflow.rework_cycles",
    "casefile.workflow.retries",
    "casefile.budget.cost",
    "casefile.budget.tokens",
    "casefile.budget.exhaustions",
    "casefile.approval.wait_duration",
    "casefile.approval.decisions",
)

_COUNTERS = frozenset(
    {
        "casefile.workflow.runs",
        "casefile.workflow.failures",
        "casefile.agent.executions",
        "casefile.agent.failures",
        "casefile.tool.invocations",
        "casefile.tool.failures",
        "casefile.workflow.rework_cycles",
        "casefile.workflow.retries",
        "casefile.budget.exhaustions",
        "casefile.approval.decisions",
    }
)


def check_labels(labels: dict[str, str]) -> dict[str, str]:
    """Reject forbidden high-cardinality label keys."""
    for key in labels:
        if key in FORBIDDEN_LABEL_KEYS:
            raise ValueError(f"Metric label {key!r} is forbidden (high cardinality)")
    return dict(labels)


class CasefileMeters:
    """Deterministic metrics: in-memory store + optional OTel forwarding."""

    def __init__(self, meter: Any | None = None) -> None:
        self._meter = meter
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._instruments: dict[str, Any] = {}
        if meter is not None:
            self._build_instruments(meter)

    def _build_instruments(self, meter: Any) -> None:
        try:
            for name in METRIC_NAMES:
                if name in _COUNTERS:
                    self._instruments[name] = meter.create_counter(name)
                elif name.endswith("duration") or name.endswith("wait_duration"):
                    self._instruments[name] = meter.create_histogram(name, unit="ms")
                else:
                    self._instruments[name] = meter.create_histogram(name)
        except Exception:
            self._instruments = {}

    def _key(self, name: str, labels: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
        return (name, tuple(sorted(check_labels(labels).items())))

    def record_counter(
        self, name: str, value: float = 1.0, labels: dict[str, str] | None = None
    ) -> None:
        """Record a counter measurement (unknown names rejected)."""
        if name not in _COUNTERS:
            raise ValueError(f"Unknown counter metric {name!r}")
        key = self._key(name, labels or {})
        self._counters[key] = self._counters.get(key, 0.0) + value
        instrument = self._instruments.get(name)
        if instrument is not None:
            with suppress(Exception):
                instrument.add(value, dict(key[1]))

    def record_histogram(
        self, name: str, value: float, labels: dict[str, str] | None = None
    ) -> None:
        """Record a histogram measurement (unknown names rejected)."""
        if name not in METRIC_NAMES or name in _COUNTERS:
            raise ValueError(f"Unknown histogram metric {name!r}")
        key = self._key(name, labels or {})
        self._counters[key] = self._counters.get(key, 0.0) + value
        instrument = self._instruments.get(name)
        if instrument is not None:
            with suppress(Exception):
                instrument.record(value, dict(key[1]))

    def total(self, name: str, labels: dict[str, str] | None = None) -> float:
        """In-memory total for one metric/label set (tests, debugging)."""
        if labels is None:
            return sum(amount for (metric, _), amount in self._counters.items() if metric == name)
        return self._counters.get(self._key(name, labels), 0.0)

    def snapshot(self) -> dict[str, float]:
        """Flattened name+labels totals for assertions."""
        out: dict[str, float] = {}
        for (name, label_tuple), amount in self._counters.items():
            suffix = ",".join(f"{key}={value}" for key, value in label_tuple)
            out[f"{name}{{{suffix}}}" if suffix else name] = amount
        return out
