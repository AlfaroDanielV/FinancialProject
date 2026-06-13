/**
 * Mirror of api/services/income_frequency.py — payments-per-month per cadence.
 *
 * A recurring income stores a PER-PAYMENT amount; the backend normalizes to a
 * monthly figure by multiplying by this factor. A salary is entered as a
 * MONTHLY figure, so the form divides by the factor before sending so the
 * round-trip preserves the monthly salary (a quincena = monthly / 2).
 *
 * CR: "quincenal" is semi-monthly (2 payments/month), NOT bi-weekly (26/yr).
 * Keep in sync with the Python constant.
 */
import type { IncomeFrequency } from "../api/incomes";

export const PAYMENTS_PER_MONTH: Record<IncomeFrequency, number> = {
  weekly: 52 / 12,
  biweekly: 2, // CR quincenal = semi-monthly
  monthly: 1,
  annual: 1 / 12,
};

/** Split a monthly amount into the per-payment amount for `frequency`. */
export function perPaymentFromMonthly(
  monthly: number,
  frequency: IncomeFrequency,
): number {
  const factor = PAYMENTS_PER_MONTH[frequency] ?? 1;
  return monthly / factor;
}

/** Reconstruct the monthly amount from a stored per-payment amount. */
export function monthlyFromPerPayment(
  perPayment: number,
  frequency: IncomeFrequency,
): number {
  const factor = PAYMENTS_PER_MONTH[frequency] ?? 1;
  return perPayment * factor;
}
