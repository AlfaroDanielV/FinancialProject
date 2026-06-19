import { api } from "./client";

export type CategoryKind = "income" | "expense" | "both";

export interface CategoryResponse {
  id: string;
  user_id: string;
  name: string;
  kind: CategoryKind;
  color: string;
  icon: string | null;
  is_default: boolean;
  archived: boolean;
  transaction_count: number;
  created_at: string;
  updated_at: string;
}

export interface CategoryCreate {
  name: string;
  kind: CategoryKind;
  color: string;
  icon?: string;
}

export interface CategoryUpdate {
  name?: string;
  kind?: CategoryKind;
  color?: string;
  icon?: string | null;
  archived?: boolean;
}

export const KIND_LABELS: Record<CategoryKind, string> = {
  income: "Ingreso",
  expense: "Egreso",
  both: "Ambos",
};

export const PRESET_COLORS = [
  "#B3402E", // brick red
  "#2E7D54", // green
  "#3E7A57", // sage
  "#A8763B", // amber
  "#33708A", // steel teal
  "#6E5A93", // muted violet
];

export async function fetchCategories(
  includeArchived = false
): Promise<CategoryResponse[]> {
  const res = await api.get<CategoryResponse[]>("/categories", {
    params: { include_archived: includeArchived },
  });
  return res.data;
}

export async function createCategory(
  payload: CategoryCreate
): Promise<CategoryResponse> {
  const res = await api.post<CategoryResponse>("/categories", payload);
  return res.data;
}

export async function updateCategory(
  id: string,
  payload: CategoryUpdate
): Promise<CategoryResponse> {
  const res = await api.patch<CategoryResponse>(`/categories/${id}`, payload);
  return res.data;
}

export async function archiveCategory(id: string): Promise<CategoryResponse> {
  return updateCategory(id, { archived: true });
}

export async function restoreCategory(id: string): Promise<CategoryResponse> {
  return updateCategory(id, { archived: false });
}
