import { z } from "zod";

import { api } from "./client";

export const InsightItem = z.object({
  id: z.string().uuid(),
  insight_type: z.string(),
  group: z.string(),
  description: z.string(),
  confidence: z.string(),
  source: z.string(),
  user_locked: z.boolean(),
  editable: z.boolean(),
  valid_until: z.string(),
  updated_at: z.string(),
  content: z.record(z.string(), z.unknown()),
});
export type InsightItem = z.infer<typeof InsightItem>;

export const InsightGroup = z.object({
  group: z.string(),
  label: z.string(),
  items: z.array(InsightItem),
});
export type InsightGroup = z.infer<typeof InsightGroup>;

export const InsightsListResponse = z.object({
  total: z.number().int(),
  groups: z.array(InsightGroup),
});
export type InsightsListResponse = z.infer<typeof InsightsListResponse>;

export async function fetchAllInsights(includeExpired = false) {
  const resp = await api.get("/users/me/insights", {
    params: includeExpired ? { include_expired: true } : undefined,
  });
  return InsightsListResponse.parse(resp.data);
}

export async function updateInsightContent(
  id: string,
  content: Record<string, unknown>,
) {
  const resp = await api.patch(`/users/me/insights/${id}`, { content });
  return InsightItem.parse(resp.data);
}

export async function deleteInsight(id: string) {
  const resp = await api.delete(`/users/me/insights/${id}`);
  return resp.data as { deleted: number };
}

export type InsightGroupKey =
  | "patrones"
  | "metas"
  | "conozco"
  | "banderas";

export async function deleteInsightsByGroup(group: InsightGroupKey) {
  const resp = await api.delete(`/users/me/insights/group/${group}`);
  return resp.data as { deleted: number };
}

export async function deleteAllInsights() {
  const resp = await api.delete("/users/me/insights");
  return resp.data as { deleted: number };
}

export async function downloadInsightsExport() {
  const resp = await api.get("/users/me/insights/export", {
    responseType: "blob",
  });
  const blob = resp.data as Blob;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `memoria-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
