import { api } from "./client";

export type IncomeType =
  | "salary"
  | "aguinaldo"
  | "salario_escolar"
  | "freelance"
  | "other";

export type IncomeFrequency = "weekly" | "biweekly" | "monthly" | "annual";

export interface RecurringIncomeResponse {
  id: string;
  user_id: string;
  name: string;
  income_type: IncomeType;
  amount: number | null;
  // Gross (pre-deduction) salary; `amount` carries the net take-home. Set only
  // for salaries captured via the calculator.
  gross_monthly: number | null;
  currency: string;
  frequency: IncomeFrequency;
  next_payment_date: string;
  base_salary_link_id: string | null;
  is_active: boolean;
  archived: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface RecurringIncomeCreate {
  name: string;
  income_type: IncomeType;
  amount: number;
  gross_monthly?: number;
  currency: "CRC" | "USD";
  frequency: IncomeFrequency;
  next_payment_date: string;
  notes?: string | null;
}

export interface RecurringIncomeUpdate {
  name?: string;
  amount?: number;
  gross_monthly?: number | null;
  frequency?: IncomeFrequency;
  next_payment_date?: string;
  is_active?: boolean;
  archived?: boolean;
  notes?: string | null;
}

export interface DeriveCyclesResponse {
  aguinaldo: RecurringIncomeResponse;
  salario_escolar: RecurringIncomeResponse;
  created_aguinaldo: boolean;
  created_salario_escolar: boolean;
}

export const INCOME_TYPE_LABELS: Record<IncomeType, string> = {
  salary: "Salario",
  aguinaldo: "Aguinaldo",
  salario_escolar: "Salario escolar",
  freelance: "Freelance",
  other: "Otro",
};

export const FREQUENCY_LABELS: Record<IncomeFrequency, string> = {
  weekly: "Semanal",
  biweekly: "Quincenal",
  monthly: "Mensual",
  annual: "Anual",
};

export const DERIVED_INCOME_TYPES = new Set<IncomeType>([
  "aguinaldo",
  "salario_escolar",
]);

export async function fetchRecurringIncomes(
  includeArchived = false
): Promise<RecurringIncomeResponse[]> {
  const res = await api.get<RecurringIncomeResponse[]>(
    "/recurring-incomes",
    { params: { include_archived: includeArchived } }
  );
  return res.data;
}

export async function fetchRecurringIncome(
  id: string
): Promise<RecurringIncomeResponse> {
  const res = await api.get<RecurringIncomeResponse>(
    `/recurring-incomes/${id}`
  );
  return res.data;
}

export async function createRecurringIncome(
  payload: RecurringIncomeCreate
): Promise<RecurringIncomeResponse> {
  const res = await api.post<RecurringIncomeResponse>(
    "/recurring-incomes",
    payload
  );
  return res.data;
}

export async function updateRecurringIncome(
  id: string,
  payload: RecurringIncomeUpdate
): Promise<RecurringIncomeResponse> {
  const res = await api.patch<RecurringIncomeResponse>(
    `/recurring-incomes/${id}`,
    payload
  );
  return res.data;
}

export async function pauseRecurringIncome(
  id: string
): Promise<RecurringIncomeResponse> {
  return updateRecurringIncome(id, { is_active: false });
}

export async function resumeRecurringIncome(
  id: string
): Promise<RecurringIncomeResponse> {
  return updateRecurringIncome(id, { is_active: true });
}

export async function archiveRecurringIncome(
  id: string
): Promise<RecurringIncomeResponse> {
  const res = await api.delete<RecurringIncomeResponse>(
    `/recurring-incomes/${id}`
  );
  return res.data;
}

export async function restoreRecurringIncome(
  id: string
): Promise<RecurringIncomeResponse> {
  return updateRecurringIncome(id, { archived: false, is_active: true });
}

export async function deriveIncomeCycles(
  salaryId: string
): Promise<DeriveCyclesResponse> {
  const res = await api.post<DeriveCyclesResponse>(
    `/recurring-incomes/${salaryId}/derive-cycles`
  );
  return res.data;
}
