"""Costa-Rican-convention money formatting."""
from __future__ import annotations

from decimal import Decimal


def format_amount(amount: Decimal, currency: str) -> str:
    """₡5.000 for CRC (period thousands, no decimals).
    $30.00 for USD (comma thousands, two decimals).
    Anything else: amount + currency code.
    """
    if currency == "CRC":
        return "₡" + f"{int(abs(amount)):,}".replace(",", ".")
    if currency == "USD":
        return f"${abs(amount):,.2f}"
    return f"{abs(amount)} {currency}"
