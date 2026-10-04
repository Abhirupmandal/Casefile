"""
CASEFILE budget enforcement package (Phase 8).

Immutable envelopes, durable usage, atomic reservations, priced cost
accounting, typed termination, and monotonic terminal latches. Agents and
orchestrators consult this package; untrusted content can never alter it.
"""

from casefile.budget.engine import (
    BudgetEngine,
    BudgetExhaustedError,
    Reservation,
    ReserveKind,
    should_retry,
)
from casefile.budget.envelope import BUDGET_ENVELOPE_VERSION, BudgetEnvelope
from casefile.budget.pricing import (
    TEST_PRICING_VERSION,
    CostCalculator,
    CostRecord,
    ModelPricing,
    ModelUsage,
    PricingTable,
    calculate_cost,
    zero_pricing,
)
from casefile.budget.termination import (
    BudgetTermination,
    TerminationDecision,
    TerminationPolicy,
    to_workflow_state,
)
from casefile.budget.usage import BudgetUsage

__all__ = [
    "BudgetEngine",
    "BudgetExhaustedError",
    "Reservation",
    "ReserveKind",
    "should_retry",
    "BUDGET_ENVELOPE_VERSION",
    "BudgetEnvelope",
    "TEST_PRICING_VERSION",
    "CostCalculator",
    "CostRecord",
    "ModelPricing",
    "ModelUsage",
    "PricingTable",
    "calculate_cost",
    "zero_pricing",
    "BudgetTermination",
    "TerminationDecision",
    "TerminationPolicy",
    "to_workflow_state",
    "BudgetUsage",
]
