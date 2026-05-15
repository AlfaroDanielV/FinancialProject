import { api } from "./client";
import {
  GoalContribution,
  GoalContributionList,
  GoalDetail,
  GoalDetailList,
  GoalForecast,
} from "../schemas/entities";

export async function fetchGoals(status?: string) {
  const resp = await api.get("/goals", {
    params: status ? { status } : undefined,
  });
  return GoalDetailList.parse(resp.data);
}

export async function fetchGoal(id: string) {
  const resp = await api.get(`/goals/${id}`);
  return GoalDetail.parse(resp.data);
}

export async function fetchGoalContributions(id: string) {
  const resp = await api.get(`/goals/${id}/contributions`);
  return GoalContributionList.parse(resp.data);
}

export async function fetchGoalForecast(id: string) {
  const resp = await api.get(`/goals/${id}/forecast`);
  return GoalForecast.parse(resp.data);
}

export interface GoalCreatePayload {
  name: string;
  target_amount: string;
  target_currency: "CRC" | "USD";
  target_date?: string;
  linked_account_id?: string;
  priority?: number;
  monthly_contribution?: string;
}

export async function createGoal(payload: GoalCreatePayload) {
  const resp = await api.post("/goals", payload);
  return GoalDetail.parse(resp.data);
}

export interface GoalUpdatePayload {
  name?: string;
  target_amount?: string;
  target_date?: string | null;
  monthly_contribution?: string | null;
  priority?: number;
  status?: "active" | "achieved" | "abandoned" | "paused";
  linked_account_id?: string | null;
}

export async function updateGoal(id: string, payload: GoalUpdatePayload) {
  const resp = await api.patch(`/goals/${id}`, payload);
  return GoalDetail.parse(resp.data);
}

export async function abandonGoal(id: string) {
  return updateGoal(id, { status: "abandoned" });
}

export async function pauseGoal(id: string) {
  return updateGoal(id, { status: "paused" });
}

export async function resumeGoal(id: string) {
  return updateGoal(id, { status: "active" });
}

export async function markGoalAchieved(id: string) {
  return updateGoal(id, { status: "achieved" });
}

export interface GoalContributionPayload {
  amount: string;
  occurred_at?: string;
  transaction_id?: string;
}

export async function addGoalContribution(
  goalId: string,
  payload: GoalContributionPayload,
) {
  const resp = await api.post(`/goals/${goalId}/contributions`, payload);
  return resp.data as { goal: GoalDetail; contribution: GoalContribution };
}
