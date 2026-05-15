import { z } from "zod";

import { api } from "./client";

const CategoryKindSchema = z.enum(["income", "expense", "both"]);
export type CategoryKind = z.infer<typeof CategoryKindSchema>;

export const Category = z.object({
  id: z.string().uuid(),
  user_id: z.string().uuid(),
  name: z.string(),
  kind: CategoryKindSchema,
  color: z.string(),
  icon: z.string().nullable(),
  is_default: z.boolean(),
  archived: z.boolean(),
  transaction_count: z.number().int(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Category = z.infer<typeof Category>;

export const CategoryList = z.array(Category);

export async function fetchCategories(includeArchived = false) {
  const resp = await api.get("/categories", {
    params: includeArchived ? { include_archived: true } : undefined,
  });
  return CategoryList.parse(resp.data);
}

export interface CategoryCreatePayload {
  name: string;
  kind: CategoryKind;
  color: string;
  icon?: string;
}

export async function createCategory(payload: CategoryCreatePayload) {
  const resp = await api.post("/categories", payload);
  return Category.parse(resp.data);
}

export interface CategoryUpdatePayload {
  name?: string;
  kind?: CategoryKind;
  color?: string;
  icon?: string | null;
  archived?: boolean;
}

export async function updateCategory(id: string, payload: CategoryUpdatePayload) {
  const resp = await api.patch(`/categories/${id}`, payload);
  return Category.parse(resp.data);
}

export async function archiveCategory(id: string) {
  return updateCategory(id, { archived: true });
}

export async function restoreCategory(id: string) {
  return updateCategory(id, { archived: false });
}
