import { api } from "./client";
import {
  AmortizationSchedule,
  Debt,
  DebtOverviewSchema,
  DebtSummaryList,
  PayoffScenariosResponse,
} from "../schemas/entities";

export async function fetchDebts(includeArchived = false) {
  const resp = await api.get("/debts", {
    params: includeArchived ? { include_archived: true } : undefined,
  });
  return DebtSummaryList.parse(resp.data);
}

export async function fetchDebtOverview() {
  const resp = await api.get("/debts/overview");
  return DebtOverviewSchema.parse(resp.data);
}

export async function fetchDebt(id: string) {
  const resp = await api.get(`/debts/${id}`);
  return Debt.parse(resp.data);
}

export async function fetchDebtSchedule(id: string) {
  const resp = await api.get(`/debts/${id}/schedule`);
  return AmortizationSchedule.parse(resp.data);
}

export interface PayoffScenarioParams {
  lump_sum?: number;
  extra_monthly?: number;
}

export async function fetchPayoffScenarios(id: string, params: PayoffScenarioParams) {
  const query: Record<string, number> = {};
  if (params.lump_sum && params.lump_sum > 0) query.lump_sum = params.lump_sum;
  if (params.extra_monthly && params.extra_monthly > 0)
    query.extra_monthly = params.extra_monthly;
  const resp = await api.get(`/debts/${id}/payoff-scenarios`, { params: query });
  return PayoffScenariosResponse.parse(resp.data);
}

export interface DebtUpdatePayload {
  name?: string;
  payment_due_day?: number;
  account_id?: string | null;
  notes?: string;
  is_active?: boolean;
  archived?: boolean;
}

export async function updateDebt(id: string, payload: DebtUpdatePayload) {
  const resp = await api.patch(`/debts/${id}`, payload);
  return Debt.parse(resp.data);
}

export async function pauseDebt(id: string) {
  return updateDebt(id, { is_active: false });
}

export async function resumeDebt(id: string) {
  return updateDebt(id, { is_active: true });
}

export async function archiveDebt(id: string) {
  const resp = await api.delete(`/debts/${id}`);
  return Debt.parse(resp.data);
}

export async function restoreDebt(id: string) {
  return updateDebt(id, { is_active: true, archived: false });
}
