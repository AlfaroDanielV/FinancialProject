/**
 * Shared money formatter for new (envelope) components. Existing screens keep
 * their local `formatMoney` copies; this is the de-duplicated version going
 * forward. CRC rounds to whole colones (es-CR grouping); USD shows up to 2 dp.
 */
export function formatMoney(amount: number, currency: string): string {
  if (currency === "USD") {
    return `$${amount.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
  }
  return `₡${Math.round(amount).toLocaleString("es-CR")}`;
}
