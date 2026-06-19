import { api } from "./client";

export type GoalStatus =
  | "active"
  | "paused"
  | "achieved"
  | "abandoned"
  | "completed";

export interface GoalResponse {
  id: string;
  user_id: string;
  name: string;
  target_amount: number;
  target_currency: string;
  current_amount: number;
  target_date: string | null;
  monthly_contribution: number | null;
  priority: number;
  status: GoalStatus;
  linked_account_id: string | null;
  created_at: string;
}

export interface GoalContributionResponse {
  id: string;
  goal_id: string;
  transaction_id: string | null;
  source_account_id: string | null;
  /** Set once the contribution was returned by a goal cancel (Phase 7d). */
  refund_transaction_id: string | null;
  amount: number;
  occurred_at: string;
  created_at: string;
}

export interface GoalContributionResult {
  goal: GoalResponse;
  contribution: GoalContributionResponse;
}

export interface GoalForecastResponse {
  goal_id: string;
  target_amount: number;
  current_amount: number;
  remaining: number;
  avg_monthly_contribution: number;
  months_to_target: number | null;
  projected_completion_date: string | null;
  has_enough_data: boolean;
  lookback_months: number;
}

export interface GoalUpdate {
  name?: string;
  target_amount?: number;
  target_currency?: "CRC" | "USD";
  target_date?: string | null;
  priority?: number;
  monthly_contribution?: number | null;
  status?: GoalStatus;
}

export interface GoalCreate {
  name: string;
  target_amount: number;
  target_currency: "CRC" | "USD";
  target_date?: string | null;
  priority?: number;
  monthly_contribution?: number | null;
}

export interface GoalContributionCreate {
  amount: number;
  /** Phase 7d: every aporte names the account the money comes from. */
  account_id: string;
  occurred_at?: string;
}

// ── Phase 7d: cancel with refunds ────────────────────────────────────────────

export interface GoalRefundItem {
  account_id: string | null;
  account_name: string | null;
  account_archived: boolean;
  amount: number;
  contribution_count: number;
}

export interface GoalCancelPreview {
  goal_id: string;
  currency: string;
  refundable_total: number;
  unrefundable_total: number;
  refunds: GoalRefundItem[];
}

export interface GoalCancelResult {
  goal: GoalResponse;
  refunded_total: number;
  unrefundable_total: number;
  refunds: GoalRefundItem[];
}

export const STATUS_LABELS: Record<string, string> = {
  active: "Activa",
  paused: "Pausada",
  achieved: "Cumplida",
  abandoned: "Abandonada",
  completed: "Cumplida",
};

export const STATUS_COLORS: Record<string, string> = {
  active: "#3E7A57",
  paused: "#8A6A1F",
  achieved: "#2E7D54",
  abandoned: "#8B8B83",
  completed: "#2E7D54",
};

export async function fetchGoals(status?: string): Promise<GoalResponse[]> {
  const res = await api.get<GoalResponse[]>("/goals", {
    params: status ? { status } : undefined,
  });
  return res.data;
}

export async function fetchGoal(id: string): Promise<GoalResponse> {
  const res = await api.get<GoalResponse>(`/goals/${id}`);
  return res.data;
}

export async function fetchGoalContributions(
  id: string
): Promise<GoalContributionResponse[]> {
  const res = await api.get<GoalContributionResponse[]>(
    `/goals/${id}/contributions`
  );
  return res.data;
}

export async function fetchGoalForecast(
  id: string
): Promise<GoalForecastResponse> {
  const res = await api.get<GoalForecastResponse>(`/goals/${id}/forecast`);
  return res.data;
}

export async function updateGoal(
  id: string,
  payload: GoalUpdate
): Promise<GoalResponse> {
  const res = await api.patch<GoalResponse>(`/goals/${id}`, payload);
  return res.data;
}

export async function addGoalContribution(
  id: string,
  payload: GoalContributionCreate
): Promise<GoalContributionResult> {
  const res = await api.post<GoalContributionResult>(
    `/goals/${id}/contributions`,
    payload
  );
  return res.data;
}

export async function pauseGoal(id: string): Promise<GoalResponse> {
  return updateGoal(id, { status: "paused" });
}

export async function resumeGoal(id: string): Promise<GoalResponse> {
  return updateGoal(id, { status: "active" });
}

export async function markGoalAchieved(id: string): Promise<GoalResponse> {
  return updateGoal(id, { status: "completed" });
}

export async function createGoal(payload: GoalCreate): Promise<GoalResponse> {
  const res = await api.post<GoalResponse>("/goals", payload);
  return res.data;
}

/** Phase 7d — what a cancel would refund, per account (shown BEFORE confirming). */
export async function fetchGoalCancelPreview(
  id: string
): Promise<GoalCancelPreview> {
  const res = await api.get<GoalCancelPreview>(`/goals/${id}/cancel-preview`);
  return res.data;
}

/** Phase 7d — cancel the goal: the money goes back to the source accounts. */
export async function cancelGoal(id: string): Promise<GoalCancelResult> {
  const res = await api.post<GoalCancelResult>(`/goals/${id}/cancel`);
  return res.data;
}

/** Phase 7d — TRUE delete (409 while the goal still holds refundable money). */
export async function deleteGoal(id: string): Promise<void> {
  await api.delete(`/goals/${id}`);
}
