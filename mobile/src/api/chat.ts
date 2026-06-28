import { api, LLM_UPLOAD_TIMEOUT_MS } from "./client";

export interface ChatButton {
  label: string;
  callback_data: string;
}

export interface ChatUrlButton {
  label: string;
  url: string;
}

/**
 * Phase 6f debt slice (D1/D3) — light-extraction handoff fields the chat does
 * NOT commit. `prefill` mirrors the backend `_dispatch_create_debt` payload:
 * amounts are strings (parsed in the form), `interest_rate_pct` is a percent
 * (the form converts to a 0–1 fraction), `term_months` is a number, and any
 * field may be null (the form completes whatever is missing).
 */
export interface DebtPrefill {
  name: string | null;
  original_amount: string | null;
  current_balance: string | null;
  interest_rate_pct: string | null;
  term_months: number | null;
  lender: string | null;
  currency: string;
}

/**
 * Phase 7b — credit-card creation handoff (`screen: "card_create"`). Light
 * extraction like debt: the native card form gathers the terms (or reads
 * them from the statement PDF). `credit_limit` is a string magnitude.
 */
export interface CardPrefill {
  name: string | null;
  issuer: string | null;
  credit_limit: string | null;
  currency: string;
}

/**
 * Envelope budgeting (Sobres) — emitted after an EXPENSE commits
 * (`screen: "assign_envelope"`). The chat offers an in-chat "Asignar a un
 * sobre" affordance for the just-created transaction; no navigation.
 */
export interface AssignEnvelopePrefill {
  transaction_id: string;
  amount: string;
  currency: string;
  merchant: string | null;
}

/**
 * Duplicate detection — emitted after an EXPENSE commits that looks like a
 * duplicate (`screen: "duplicate_warning"`). The chat renders an in-bubble
 * "posible duplicado" card with Eliminar (→ act the nudge = hard delete) and
 * Conservar (→ dismiss the nudge = keep). `nudge_id` may be null if the nudge
 * couldn't be raised (rare); the card then only offers Eliminar via the txn.
 */
export interface DuplicateWarningPrefill {
  transaction_id: string;
  nudge_id: string | null;
  amount: string;
  currency: string;
  merchant: string | null;
  matched_merchant: string | null;
  matched_date: string;
}

/**
 * Movimientos sin cuenta — emitted after the agent lists unassigned movements
 * (`screen: "assign_account"`). The chat hands off to the Movimientos tab with
 * the "Sin cuenta" filter applied so the user can assign each row to an account.
 */
export interface ReassignAccountPrefill {
  filter: string;
}

/**
 * Reclassify gasto ↔ ingreso — emitted after an INCOME commits
 * (`screen: "reclassify"`). The chat offers an in-chat "Era un gasto" chip for
 * the just-created row. The EXPENSE path reuses its `assign_envelope` hint to
 * offer the inverse "Era un ingreso" chip, so both share this shape.
 */
export interface ReclassifyPrefill {
  transaction_id: string;
  amount: string;
  currency: string;
  merchant: string | null;
}

/**
 * `/menu` marker (`screen: "menu"`) — no prefill payload; tells the chat to keep
 * the menu chips repeatable. The chips themselves arrive in `buttons`.
 */
export type MenuPrefill = Record<string, never>;

export interface ChatOpenScreen {
  screen: string;
  prefill:
    | DebtPrefill
    | CardPrefill
    | AssignEnvelopePrefill
    | DuplicateWarningPrefill
    | ReassignAccountPrefill
    | ReclassifyPrefill
    | MenuPrefill;
}

export interface ChatMessageResponse {
  reply_text: string;
  buttons: ChatButton[];
  url_buttons: ChatUrlButton[];
  open_screen: ChatOpenScreen | null;
}

export async function postChatMessage(text: string): Promise<ChatMessageResponse> {
  const { data } = await api.post<ChatMessageResponse>("/chat/message", { text });
  return data;
}

/**
 * Start a new conversation — clears durable server-side conversational state
 * (pending write, clarification, account-creation flow, memory-edit flow, and
 * the LLM query history). The visible message list is cleared client-side.
 */
export async function resetChat(): Promise<void> {
  await api.post("/chat/reset");
}

export async function postChatImage(
  uri: string,
  mediaType: string,
): Promise<ChatMessageResponse> {
  const form = new FormData();
  const filename = uri.split("/").pop() ?? "receipt.jpg";
  // React Native FormData accepts the object shape {uri, type, name}
  form.append("file", { uri, type: mediaType, name: filename } as unknown as Blob);
  const { data } = await api.post<ChatMessageResponse>("/chat/image", form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: LLM_UPLOAD_TIMEOUT_MS,
  });
  return data;
}
