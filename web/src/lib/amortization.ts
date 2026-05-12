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

export { MAX_MONTHS };
