import { api } from "./client";

export interface InsightItem {
  id: string;
  insight_type: string;
  group: string;
  description: string;
  confidence: number;
  source: string;
  user_locked: boolean;
  editable: boolean;
  valid_until: string;
  updated_at: string;
  content: Record<string, unknown>;
}

export interface InsightGroup {
  group: string;
  label: string;
  items: InsightItem[];
}

export interface InsightsListResponse {
  total: number;
  groups: InsightGroup[];
}

export interface InsightDeleteResponse {
  deleted: number;
}

export async function fetchInsights(): Promise<InsightsListResponse> {
  const res = await api.get<InsightsListResponse>("/users/me/insights");
  return res.data;
}

export async function deleteInsight(
  id: string
): Promise<InsightDeleteResponse> {
  const res = await api.delete<InsightDeleteResponse>(
    `/users/me/insights/${id}`
  );
  return res.data;
}

export async function deleteInsightGroup(
  group: string
): Promise<InsightDeleteResponse> {
  const res = await api.delete<InsightDeleteResponse>(
    `/users/me/insights/group/${group}`
  );
  return res.data;
}

export async function deleteAllInsights(): Promise<InsightDeleteResponse> {
  const res = await api.delete<InsightDeleteResponse>("/users/me/insights");
  return res.data;
}

export async function exportInsights(): Promise<Record<string, unknown>> {
  const res = await api.get<Record<string, unknown>>(
    "/users/me/insights/export"
  );
  return res.data;
}
