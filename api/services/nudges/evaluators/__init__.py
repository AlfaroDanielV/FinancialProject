"""Nudge evaluators.

Each evaluator produces NudgeCandidate objects from a deterministic SQL
read. They DO NOT insert, commit, or decide delivery — that's the
orchestrator's and the delivery worker's job.

Registry at the bottom lets the orchestrator iterate without explicit
wiring. New evaluator → import + append.
"""
from .base import BaseNudgeEvaluator, NudgeCandidate
from .duplicate_transaction import DuplicateTransactionEvaluator
from .envelope_near_limit import EnvelopeNearLimitEvaluator
from .missing_income import MissingIncomeEvaluator
from .over_commitment import OverCommitmentEvaluator
from .shadow_review_pending import ShadowReviewPendingEvaluator
from .stale_pending import StalePendingEvaluator
from .upcoming_bill import UpcomingBillEvaluator


ALL_EVALUATORS: list[BaseNudgeEvaluator] = [
    MissingIncomeEvaluator(),
    StalePendingEvaluator(),
    UpcomingBillEvaluator(),
    OverCommitmentEvaluator(),
    DuplicateTransactionEvaluator(),
    EnvelopeNearLimitEvaluator(),
    ShadowReviewPendingEvaluator(),
]

__all__ = [
    "BaseNudgeEvaluator",
    "NudgeCandidate",
    "DuplicateTransactionEvaluator",
    "EnvelopeNearLimitEvaluator",
    "MissingIncomeEvaluator",
    "ShadowReviewPendingEvaluator",
    "StalePendingEvaluator",
    "UpcomingBillEvaluator",
    "OverCommitmentEvaluator",
    "ALL_EVALUATORS",
]
