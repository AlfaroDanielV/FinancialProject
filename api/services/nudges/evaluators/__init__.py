"""Nudge evaluators.

Each evaluator produces NudgeCandidate objects from a deterministic SQL
read. They DO NOT insert, commit, or decide delivery — that's the
orchestrator's and the delivery worker's job.

Registry at the bottom lets the orchestrator iterate without explicit
wiring. New evaluator → import + append.
"""
from .base import BaseNudgeEvaluator, NudgeCandidate
from .missing_income import MissingIncomeEvaluator
from .stale_pending import StalePendingEvaluator
from .upcoming_bill import UpcomingBillEvaluator


ALL_EVALUATORS: list[BaseNudgeEvaluator] = [
    MissingIncomeEvaluator(),
    StalePendingEvaluator(),
    UpcomingBillEvaluator(),
]

__all__ = [
    "BaseNudgeEvaluator",
    "NudgeCandidate",
    "MissingIncomeEvaluator",
    "StalePendingEvaluator",
    "UpcomingBillEvaluator",
    "ALL_EVALUATORS",
]
