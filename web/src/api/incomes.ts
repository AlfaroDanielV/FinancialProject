import { z } from "zod";

import { api } from "./client";
import { RecurringIncome, RecurringIncomeList } from "../schemas/entities";

export async function fetchRecurringIncomes(includeArchived = false) {
  const resp = await api.get("/recurring-incomes", {
    params: includeArchived ? { include_archived: true } : undefined,
  });
  return RecurringIncomeList.parse(resp.data);
}

export interface RecurringIncomeUpdatePayload {
  name?: string;
  amount?: string;
  frequency?: "weekly" | "biweekly" | "monthly" | "annual";
  next_payment_date?: string;
  notes?: string;
  is_active?: boolean;
  archived?: boolean;
}

export async function updateRecurringIncome(
  id: string,
  payload: RecurringIncomeUpdatePayload,
) {
  const resp = await api.patch(`/recurring-incomes/${id}`, payload);
  return RecurringIncome.parse(resp.data);
}

export async function pauseRecurringIncome(id: string) {
  return updateRecurringIncome(id, { is_active: false });
}

export async function resumeRecurringIncome(id: string) {
  return updateRecurringIncome(id, { is_active: true });
}

export async function archiveRecurringIncome(id: string) {
  const resp = await api.delete(`/recurring-incomes/${id}`);
  return RecurringIncome.parse(resp.data);
}

export async function restoreRecurringIncome(id: string) {
  return updateRecurringIncome(id, { archived: false, is_active: true });
}

export const CRCycleDeriveResponseSchema = z.object({
  aguinaldo: RecurringIncome,
  salario_escolar: RecurringIncome,
  created_aguinaldo: z.boolean(),
  created_salario_escolar: z.boolean(),
});
export type CRCycleDeriveResponse = z.infer<typeof CRCycleDeriveResponseSchema>;

export async function deriveCRCycles(salaryId: string) {
  const resp = await api.post(`/recurring-incomes/${salaryId}/derive-cycles`);
  return CRCycleDeriveResponseSchema.parse(resp.data);
}
