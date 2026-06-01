/**
 * Phase 6f debt slice (D3) — lifted verbatim from web/src/lib/amortization.ts.
 *
 * Pure TypeScript (no web deps), used by DebtCreateScreen for the live cuota
 * preview and a default for minimum_payment. Backend
 * api/services/amortization.py remains the source of truth; this mirrors it for
 * UI previews only.
 */
const MAX_MONTHS = 600;

export interface PaymentPreviewInput {
  balance: number;
  annualRate: number;
  termMonths: number;
  includesInsurance: boolean;
  insuranceMonthly: number;
}

export function computeFrenchPayment(
  principal: number,
  monthlyRate: number,
  months: number,
) {
  if (principal <= 0 || months <= 0) return null;
  if (monthlyRate === 0) return principal / months;
  const factor = (1 + monthlyRate) ** months;
  return (principal * monthlyRate * factor) / (factor - 1);
}

export function computeMonthlyPaymentPreview(input: PaymentPreviewInput) {
  const monthlyRate = input.annualRate / 12;
  const basePayment = computeFrenchPayment(
    input.balance,
    monthlyRate,
    input.termMonths,
  );
  if (basePayment === null) return null;
  const insurance = input.includesInsurance ? input.insuranceMonthly : 0;
  return basePayment + insurance;
}

export function paymentCanAmortize(
  balance: number,
  annualRate: number,
  monthlyPayment: number,
  insuranceMonthly: number,
) {
  if (balance <= 0 || monthlyPayment <= 0) return false;
  const monthlyRate = annualRate / 12;
  return monthlyRate === 0 || monthlyPayment - insuranceMonthly > balance * monthlyRate;
}

// ── Phase 6e B7: client-side payoff scenario engine ─────────────────────────
// These mirror api/services/amortization.py. Backend is the source of truth;
// these are for UI previews. Cross-validation lives in the SPA's manual smoke.

export interface PayoffScenarioInput {
  balance: number;
  annualRate: number;
  monthlyPayment: number;
  includesInsurance: boolean;
  insuranceMonthly: number;
  paymentsMade: number;
  prepaymentPenaltyPct: number;
}

export interface PayoffScenarioOutput {
  monthsSaved: number;
  interestSaved: number;
  totalSaved: number;
  newTotalMonths: number;
  prepaymentPenaltyApplies: boolean;
  prepaymentPenaltyAmount: number;
}

function _runOff(
  balance: number,
  monthlyRate: number,
  payment: number,
  insurance: number,
  extraMonthly: number,
  lumpSumAmount: number,
  lumpSumMonth: number,
): { months: number; interest: number } {
  let remaining = balance;
  let totalInterest = 0;
  let months = 0;
  while (remaining > 0 && months < MAX_MONTHS) {
    months += 1;
    const interest = monthlyRate > 0 ? remaining * monthlyRate : 0;
    let principal = payment - insurance - interest;
    if (months === lumpSumMonth && lumpSumAmount > 0) {
      principal += lumpSumAmount;
    }
    principal += extraMonthly;
    if (principal <= 0) {
      // Payment doesn't cover interest — diverges. Bail.
      return { months: MAX_MONTHS, interest: totalInterest };
    }
    if (principal >= remaining) {
      totalInterest += interest;
      remaining = 0;
      break;
    }
    remaining -= principal;
    totalInterest += interest;
  }
  return { months, interest: totalInterest };
}

export function calculatePrepaymentPenalty(
  paymentsMade: number,
  prepaymentPenaltyPct: number,
  prepaymentAmount: number,
): { amount: number; applies: boolean } {
  if (paymentsMade >= 2) return { amount: 0, applies: false };
  const rate = prepaymentPenaltyPct || 0;
  const amount = Math.round(prepaymentAmount * rate * 100) / 100;
  return { amount, applies: rate > 0 };
}

export function earlyPayoffLumpSum(
  input: PayoffScenarioInput,
  lumpSumAmount: number,
  lumpSumMonth = 1,
): PayoffScenarioOutput {
  const monthlyRate = input.annualRate / 12;
  const insurance = input.includesInsurance ? input.insuranceMonthly : 0;
  const baseline = _runOff(
    input.balance,
    monthlyRate,
    input.monthlyPayment,
    insurance,
    0,
    0,
    0,
  );
  const proposed = _runOff(
    input.balance,
    monthlyRate,
    input.monthlyPayment,
    insurance,
    0,
    lumpSumAmount,
    lumpSumMonth,
  );
  const penalty = calculatePrepaymentPenalty(
    input.paymentsMade,
    input.prepaymentPenaltyPct,
    lumpSumAmount,
  );
  const interestSaved = Math.round((baseline.interest - proposed.interest) * 100) / 100;
  return {
    monthsSaved: baseline.months - proposed.months,
    interestSaved,
    totalSaved: Math.round((interestSaved - penalty.amount) * 100) / 100,
    newTotalMonths: proposed.months,
    prepaymentPenaltyApplies: penalty.applies,
    prepaymentPenaltyAmount: penalty.amount,
  };
}

export function earlyPayoffExtraMonthly(
  input: PayoffScenarioInput,
  extraMonthly: number,
): PayoffScenarioOutput {
  const monthlyRate = input.annualRate / 12;
  const insurance = input.includesInsurance ? input.insuranceMonthly : 0;
  const baseline = _runOff(
    input.balance,
    monthlyRate,
    input.monthlyPayment,
    insurance,
    0,
    0,
    0,
  );
  const proposed = _runOff(
    input.balance,
    monthlyRate,
    input.monthlyPayment,
    insurance,
    extraMonthly,
    0,
    0,
  );
  const penalty = calculatePrepaymentPenalty(
    input.paymentsMade,
    input.prepaymentPenaltyPct,
    input.balance,
  );
  const interestSaved = Math.round((baseline.interest - proposed.interest) * 100) / 100;
  return {
    monthsSaved: baseline.months - proposed.months,
    interestSaved,
    totalSaved: Math.round((interestSaved - penalty.amount) * 100) / 100,
    newTotalMonths: proposed.months,
    prepaymentPenaltyApplies: penalty.applies,
    prepaymentPenaltyAmount: penalty.amount,
  };
}

export { MAX_MONTHS };
