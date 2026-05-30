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

export interface GoalContributionCreate {
  amount: number;
  occurred_at?: string;
}

export const STATUS_LABELS: Record<string, string> = {
  active: "Activa",
  paused: "Pausada",
  achieved: "Cumplida",
  abandoned: "Abandonada",
  completed: "Cumplida",
};

export const STATUS_COLORS: Record<string, string> = {
  active: "#4A6741",
  paused: "#7A6228",
  achieved: "#3D5C35",
  abandoned: "#9C8B74",
  completed: "#3D5C35",
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

export async function abandonGoal(id: string): Promise<GoalResponse> {
  const res = await api.delete<GoalResponse>(`/goals/${id}`);
  return res.data;
}
