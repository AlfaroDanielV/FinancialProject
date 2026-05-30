import { api } from "./client";

export interface DebtSummary {
  id: string;
  name: string;
  debt_type: string;
  current_balance: number;
  interest_rate: number;
  minimum_payment: number;
  payment_due_day: number;
  is_active: boolean;
  archived: boolean;
}

export interface DebtResponse {
  id: string;
  user_id: string;
  account_id: string | null;
  name: string;
  debt_type: string;
  lender: string | null;
  original_amount: number;
  current_balance: number;
  interest_rate: number;
  minimum_payment: number;
  payment_due_day: number;
  term_months: number | null;
  start_date: string | null;
  maturity_date: string | null;
  currency: string;
  notes: string | null;
  rate_type: string;
  rate_reference: string | null;
  prepayment_penalty_pct: number;
  payments_made: number;
  includes_insurance: boolean;
  insurance_monthly: number | null;
  is_active: boolean;
  archived: boolean;
  created_at: string;
  updated_at: string;
}

export interface DebtOverview {
  total_debt: number;
  total_minimum_monthly: number;
  debts_by_type: Record<string, number>;
  upcoming_payments: Array<{
    debt_id: string;
    name: string;
    debt_type: string;
    minimum_payment: number;
    payment_due_day: number;
    current_balance: number;
  }>;
}

export interface AmortizationRow {
  month_number: number;
  payment_date: string;
  payment_amount: number;
  principal_portion: number;
  interest_portion: number;
  insurance_portion: number;
  extra_payment: number;
  remaining_balance: number;
  cumulative_interest: number;
  cumulative_principal: number;
}

export interface AmortizationSchedule {
  debt_id: string;
  debt_name: string;
  current_balance: number;
  interest_rate: number;
  monthly_payment: number;
  total_months: number;
  total_interest: number;
  total_principal: number;
  total_payments: number;
  payoff_date: string | null;
  variable_rate_notice: string | null;
  schedule: AmortizationRow[];
}

export interface PayoffScenarioResult {
  months_saved: number;
  interest_saved: number;
  total_saved: number;
  new_payoff_date: string | null;
  new_total_months: number;
  new_total_interest: number;
  prepayment_penalty_applies: boolean;
  prepayment_penalty_amount: number;
}

export interface ScheduleSummary {
  monthly_payment: number;
  total_months: number;
  total_interest: number;
  total_paid: number;
  payoff_date: string | null;
}

export interface PayoffScenariosResponse {
  debt_id: string;
  currency: string;
  payments_made: number;
  prepayment_penalty_pct: number;
  original: ScheduleSummary;
  lump_sum: PayoffScenarioResult | null;
  extra_monthly: PayoffScenarioResult | null;
  variable_rate_notice: string | null;
}

export interface DebtUpdate {
  name?: string;
  payment_due_day?: number;
  account_id?: string;
  notes?: string;
  is_active?: boolean;
  archived?: boolean;
}

export const DEBT_TYPE_LABELS: Record<string, string> = {
  mortgage: "Hipoteca",
  credit_card: "Tarjeta de crédito",
  credit_card_balance: "Tarjeta de crédito",
  auto_loan: "Préstamo de auto",
  personal_loan: "Préstamo personal",
  student_loan: "Préstamo estudiantil",
  other: "Otro",
};

export async function fetchDebts(includeArchived = false): Promise<DebtSummary[]> {
  const { data } = await api.get<DebtSummary[]>("/debts", {
    params: includeArchived ? { include_archived: true } : {},
  });
  return data;
}

export async function fetchDebt(id: string): Promise<DebtResponse> {
  const { data } = await api.get<DebtResponse>(`/debts/${id}`);
  return data;
}

export async function fetchDebtOverview(): Promise<DebtOverview> {
  const { data } = await api.get<DebtOverview>("/debts/overview");
  return data;
}

export async function fetchDebtSchedule(id: string): Promise<AmortizationSchedule> {
  const { data } = await api.get<AmortizationSchedule>(`/debts/${id}/schedule`);
  return data;
}

export async function fetchPayoffScenarios(
  id: string,
  params: { lump_sum?: number; extra_monthly?: number },
): Promise<PayoffScenariosResponse> {
  const { data } = await api.get<PayoffScenariosResponse>(
    `/debts/${id}/payoff-scenarios`,
    { params },
  );
  return data;
}

export async function updateDebt(id: string, payload: DebtUpdate): Promise<DebtResponse> {
  const { data } = await api.patch<DebtResponse>(`/debts/${id}`, payload);
  return data;
}

export async function archiveDebt(id: string): Promise<DebtResponse> {
  const { data } = await api.delete<DebtResponse>(`/debts/${id}`);
  return data;
}
