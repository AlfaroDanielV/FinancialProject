"""missing_income evaluator.

Fires when a user is actively logging spending but we have no record of
any income in the recent past. Triggers a nudge once per calendar month.

Condition (deterministic SQL):
    - The user has >= MISSING_INCOME_MIN_TXN_COUNT transactions with
      transaction_date >= now - MISSING_INCOME_TXN_WINDOW_DAYS.
    - The user has ZERO transactions with amount > 0 and
      transaction_date >= now - MISSING_INCOME_LOOKBACK_DAYS.

"Income" criterion: transactions.amount > 0.

    Evidence for this criterion (not a category, not a type column):
    - api/models/transaction.py:25 explicit comment
      "# negative = expense, positive = income"
    - api/services/transactions.py::sum_in_window filters income/expense
      purely by amount sign.
    - api/services/telegram_dispatcher.py applies the sign at commit time
      based on intent; income rows are stored with amount > 0.

Dedup key: missing_income:{user_id}:{YYYY-MM}. One nudge per user per
calendar month. If the user dismisses it, the silence machinery handles
recurrence.

Priority: always 'normal'. A missing-income gap is not an emergency.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..policy import (
    MISSING_INCOME_LOOKBACK_DAYS,
    MISSING_INCOME_MIN_TXN_COUNT,
    MISSING_INCOME_TXN_WINDOW_DAYS,
)
from .base import NudgeCandidate


_BASE_SQL = """
    SELECT u.id AS user_id,
           (SELECT COUNT(*) FROM transactions t
             WHERE t.user_id = u.id
               AND t.transaction_date >= :activity_from) AS txn_count
    FROM users u
    WHERE u.status = 'active'
      {user_filter}
      AND (SELECT COUNT(*) FROM transactions t
            WHERE t.user_id = u.id
              AND t.transaction_date >= :activity_from) >= :min_count
      AND NOT EXISTS (
            SELECT 1 FROM transactions t2
             WHERE t2.user_id = u.id
               AND t2.amount > 0
               AND t2.transaction_date >= :income_from
      )
"""


class MissingIncomeEvaluator:
    nudge_type = "missing_income"

    async def evaluate(
        self,
        session: AsyncSession,
        now: datetime,
        *,
        user_id: Optional[uuid.UUID] = None,
    ) -> list[NudgeCandidate]:
        today = now.date()
        activity_from = today - timedelta(days=MISSING_INCOME_TXN_WINDOW_DAYS - 1)
        income_from = today - timedelta(days=MISSING_INCOME_LOOKBACK_DAYS - 1)

        user_filter = "AND u.id = :target_user" if user_id is not None else ""
        params: dict[str, Any] = {
            "activity_from": activity_from,
            "income_from": income_from,
            "min_count": MISSING_INCOME_MIN_TXN_COUNT,
        }
        if user_id is not None:
            params["target_user"] = user_id

        result = await session.execute(
            text(_BASE_SQL.format(user_filter=user_filter)), params
        )

        month_tag = f"{now.year:04d}-{now.month:02d}"
        candidates: list[NudgeCandidate] = []
        for row in result.mappings().all():
            user_id: uuid.UUID = row["user_id"]
            payload: dict[str, Any] = {
                "txn_count_last_7d": int(row["txn_count"]),
                "window_days": MISSING_INCOME_TXN_WINDOW_DAYS,
                "lookback_days": MISSING_INCOME_LOOKBACK_DAYS,
                "month_tag": month_tag,
            }
            candidates.append(
                NudgeCandidate(
                    user_id=user_id,
                    dedup_key=f"missing_income:{user_id}:{month_tag}",
                    payload=payload,
                    priority="normal",
                )
            )
        return candidates
