"""Nudge evaluators.

Each evaluator produces NudgeCandidate objects from a deterministic SQL
read. They DO NOT insert, commit, or decide delivery — that's the
orchestrator's and the delivery worker's job.

Registry at the bottom lets the orchestrator iterate without explicit
wiring. New evaluator → import + append.
"""
from .base import BaseNudgeEvaluator, NudgeCandidate
from .debt_paid_off import DebtPaidOffEvaluator
from .duplicate_transaction import DuplicateTransactionEvaluator
from .envelope_near_limit import EnvelopeNearLimitEvaluator
from .first_full_month import FirstFullMonthEvaluator
from .goal_achieved import GoalAchievedEvaluator
from .missing_income import MissingIncomeEvaluator
from .over_commitment import OverCommitmentEvaluator
from .shadow_review_pending import ShadowReviewPendingEvaluator
from .stale_pending import StalePendingEvaluator
from .under_budget_month import UnderBudgetMonthEvaluator
from .upcoming_bill import UpcomingBillEvaluator


ALL_EVALUATORS: list[BaseNudgeEvaluator] = [
    MissingIncomeEvaluator(),
    StalePendingEvaluator(),
    UpcomingBillEvaluator(),
    OverCommitmentEvaluator(),
    DuplicateTransactionEvaluator(),
    EnvelopeNearLimitEvaluator(),
    ShadowReviewPendingEvaluator(),
    # Phase 8 B5 — earned-celebration layer (positive milestones).
    GoalAchievedEvaluator(),
    DebtPaidOffEvaluator(),
    FirstFullMonthEvaluator(),
    UnderBudgetMonthEvaluator(),
]

__all__ = [
    "BaseNudgeEvaluator",
    "NudgeCandidate",
    "DebtPaidOffEvaluator",
    "DuplicateTransactionEvaluator",
    "EnvelopeNearLimitEvaluator",
    "FirstFullMonthEvaluator",
    "GoalAchievedEvaluator",
    "MissingIncomeEvaluator",
    "ShadowReviewPendingEvaluator",
    "StalePendingEvaluator",
    "UnderBudgetMonthEvaluator",
    "UpcomingBillEvaluator",
    "OverCommitmentEvaluator",
    "ALL_EVALUATORS",
]
