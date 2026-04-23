"""Shared string enums used by Phase 4 models, routes, and services.

We persist the string value (not a Postgres ENUM type) to match the rest of
the codebase. Validation lives at the Pydantic schema layer.
"""
from enum import Enum


class BillFrequency(str, Enum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    BIMONTHLY = "bimonthly"
    QUARTERLY = "quarterly"
    SEMIANNUAL = "semiannual"
    ANNUAL = "annual"
    CUSTOM = "custom"


class BillCategory(str, Enum):
    UTILITY_ELECTRICITY = "utility_electricity"
    UTILITY_WATER = "utility_water"
    INTERNET = "internet"
    MOBILE = "mobile"
    STREAMING = "streaming"
    SOFTWARE_SUBSCRIPTION = "software_subscription"
    LOAN_PAYMENT = "loan_payment"
    INSURANCE = "insurance"
    CREDIT_CARD = "credit_card"
    RENT = "rent"
    HOMEOWNERS_FEE = "homeowners_fee"
    TAX = "tax"
    OTHER = "other"


class BillOccurrenceStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    PARTIALLY_PAID = "partially_paid"
    SKIPPED = "skipped"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


class CustomEventType(str, Enum):
    TAX_DEADLINE = "tax_deadline"
    GOAL_MILESTONE = "goal_milestone"
    INCOME_EXPECTED = "income_expected"
    REMINDER = "reminder"
    OTHER = "other"


class NotificationScope(str, Enum):
    BILL = "bill"
    EVENT = "event"
    CATEGORY_DEFAULT = "category_default"
    GLOBAL_DEFAULT = "global_default"


class NotificationChannel(str, Enum):
    IN_APP = "in_app"
    TELEGRAM = "telegram"
    EMAIL = "email"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    ACKNOWLEDGED = "acknowledged"
    DISMISSED = "dismissed"
    FAILED = "failed"


class NudgeType(str, Enum):
    MISSING_INCOME = "missing_income"
    STALE_PENDING_CONFIRMATION = "stale_pending_confirmation"
    UPCOMING_BILL = "upcoming_bill"


class NudgePriority(str, Enum):
    NORMAL = "normal"
    HIGH = "high"


class NudgeStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    DISMISSED = "dismissed"
    ACTED_ON = "acted_on"
    EXPIRED = "expired"
    SUPPRESSED = "suppressed"


class PendingConfirmationResolution(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EDITED = "edited"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"
