"""Single source of truth for recurring-income / bill cadence normalization.

A recurring income (or bill) stores a **per-payment** amount; this maps a
cadence to the number of those payments in a month so a per-payment amount
can be normalized to a monthly figure (multiply) and a monthly figure can be
split into a per-payment amount (divide). The two operations are exact
inverses, so a salary entered as monthly and divided here round-trips back to
the same monthly income in `_monthly_income`.

CR note: "quincenal" is **semi-monthly** (paid on the 15th and month-end → 2
payments/month, 24/year), NOT bi-weekly (26/year). The factor is 2, so a
quincena is exactly half the monthly salary.
"""
from __future__ import annotations

from decimal import Decimal

# Payments per month by cadence. Used in BOTH directions:
#   monthly_amount = per_payment * factor   (normalization, _monthly_income)
#   per_payment    = monthly_amount / factor (salary entry division)
PAYMENTS_PER_MONTH: dict[str, Decimal] = {
    "weekly": Decimal("52") / Decimal("12"),
    "biweekly": Decimal("2"),  # CR quincenal = semi-monthly (2/month)
    "monthly": Decimal("1"),
    "annual": Decimal("1") / Decimal("12"),
}


def per_payment_from_monthly(monthly: Decimal, frequency: str) -> Decimal:
    """Split a monthly amount into the per-payment amount for `frequency`.

    Unknown cadence → returned unchanged (treated as monthly). Quantized to
    cents so the stored row matches what the user is shown.
    """
    factor = PAYMENTS_PER_MONTH.get(frequency)
    if factor is None or factor == 0:
        return monthly
    return (Decimal(monthly) / factor).quantize(Decimal("0.01"))
