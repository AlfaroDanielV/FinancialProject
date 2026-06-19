"""Currency conversion for envelope spend (and future multi-currency needs).

Today the app is effectively single-currency (CRC) with occasional USD rows, so
the only pair we support is CRC ↔ USD via a **hardcoded reference rate**. This
keeps cross-currency envelope spend directionally correct: a US$30 expense
tagged to a CRC envelope must count as ₡15 000, not ₡30.

TODO(BCCR — tracked in CLAUDE.md tech-debt): replace `FALLBACK_USD_TO_CRC` with
the live rate from the Banco Central de Costa Rica "Indicadores Económicos" SOAP
web service (`GetIndicadoresEconomicos`; indicador 317 = tipo de cambio compra,
318 = venta). A small daily worker should persist the rate into the existing
`currency_rates` table (`base_currency`/`quote_currency`/`rate`/`as_of`,
migration 0017) and this module should read the latest row, falling back to the
constant only when the table is empty / stale. Until then the fixed rate is the
single source of truth.
"""
from __future__ import annotations

from decimal import Decimal

# Placeholder reference rate, ₡ per US$1. Swap for the BCCR-sourced daily rate
# (see module docstring). Intentionally a round number — it is NOT a market rate.
FALLBACK_USD_TO_CRC = Decimal("500")


def convert(amount: Decimal, from_currency: str | None, to_currency: str | None) -> Decimal:
    """Convert `amount` from `from_currency` into `to_currency`.

    Same-currency (and any unsupported pair) returns the amount unchanged — we
    only support CRC/USD today. The result is a `Decimal`; callers round/quantize
    as needed.
    """
    src = (from_currency or "CRC").upper()
    dst = (to_currency or "CRC").upper()
    if src == dst:
        return amount
    if src == "USD" and dst == "CRC":
        return amount * FALLBACK_USD_TO_CRC
    if src == "CRC" and dst == "USD":
        return amount / FALLBACK_USD_TO_CRC
    # Unsupported pair (no third currency exists in the app yet). Leave as-is.
    return amount
