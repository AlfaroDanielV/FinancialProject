import { api } from "./client";

/**
 * Native in-app alerts feed. Backend renders pending nudges to CR-voseo text
 * (GET /nudges/feed, read-only). Actions reuse the existing Phase 5d
 * transitions: act → POST /act, dismiss → POST /dismiss. "later" has no
 * endpoint — the app hides the card locally and it returns on the next fetch.
 */

export type NudgeVerb = "act" | "dismiss" | "later";

export interface NudgeFeedButton {
  label: string;
  verb: NudgeVerb | string;
}

export interface NudgeFeedItem {
  id: string;
  nudge_type: string;
  priority: string;
  text: string;
  created_at: string;
  buttons: NudgeFeedButton[];
}

interface NudgeFeedResponse {
  items: NudgeFeedItem[];
}

/**
 * Phase 8 B5 — earned-celebration nudge types. Positive milestones rendered
 * with a celebratory style in the Alertas feed (green award icon, no "No
 * mostrar más" guilt) instead of the problem-focused bell/alert-triangle.
 */
export const CELEBRATION_NUDGE_TYPES = new Set<string>([
  "goal_achieved",
  "debt_paid_off",
  "first_full_month",
  "under_budget_month",
]);

export function isCelebrationNudge(type: string): boolean {
  return CELEBRATION_NUDGE_TYPES.has(type);
}

export async function fetchNudgeFeed(): Promise<NudgeFeedItem[]> {
  const res = await api.get<NudgeFeedResponse>("/nudges/feed");
  return res.data.items;
}

export async function actOnNudge(id: string): Promise<void> {
  await api.post(`/nudges/${id}/act`);
}

export async function dismissNudge(id: string): Promise<void> {
  await api.post(`/nudges/${id}/dismiss`);
}
