/**
 * Gmail shadow review — the "bulk upload" form.
 *
 * Lists pending Gmail-found transactions. Each row defaults to KEEP; tap to
 * flip to DISCARD, or edit it (local draft — shadow rows can't be PATCHed, so
 * edits are sent inside confirm). "Aplicar" confirms the KEEP rows (with
 * edits) and discards the DISCARD rows in one pass.
 */
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Modal,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useFocusEffect } from "@react-navigation/native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  confirmShadow,
  discardShadow,
  fetchShadow,
  type ShadowConfirmItem,
} from "../api/gmail";
import { fetchAccounts, type AccountResponse } from "../api/accounts";
import type { TransactionResponse } from "../api/transactions";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import { AccountPickerModal } from "../components/AccountPickerModal";
import { CategoryPickerModal } from "../components/CategoryPickerModal";

type Decision = "keep" | "discard";
type Draft = {
  amountMagnitude: string;
  merchant: string;
  category: string;
  categoryId: string | null;
  date: string;
};

function fmtAmount(amount: number, currency: string): string {
  const abs = Math.abs(amount);
  if (currency === "USD") return `$${abs.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
  return `₡${Math.round(abs).toLocaleString("es-CR")}`;
}

export function GmailReviewScreen() {
  const qc = useQueryClient();
  const shadowQ = useQuery({ queryKey: ["gmailShadow"], queryFn: fetchShadow });
  const rows = shadowQ.data ?? [];

  // Account names for the per-row guess display (shared cache with the picker).
  const accountsQ = useQuery({
    queryKey: ["accounts", "active"],
    queryFn: () => fetchAccounts(false),
  });
  const accountById = new Map<string, AccountResponse>(
    (accountsQ.data ?? []).map((a) => [a.id, a]),
  );

  // Refetch every time the screen gains focus — so returning here after a scan
  // (or after fixing the Gmail connection) shows fresh results instead of a
  // stale cache.
  useFocusEffect(
    useCallback(() => {
      qc.invalidateQueries({ queryKey: ["gmailShadow"] });
    }, [qc]),
  );

  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [drafts, setDrafts] = useState<Record<string, Partial<ShadowConfirmItem>>>({});
  const [editing, setEditing] = useState<TransactionResponse | null>(null);
  const [accountPickerFor, setAccountPickerFor] = useState<TransactionResponse | null>(null);

  const decisionFor = (id: string): Decision => decisions[id] ?? "keep";

  // The scan pre-fills r.account_id with its best-effort guess; a draft override
  // wins. The picker runs with allowClear off, so a draft account_id is real.
  const effectiveAccountId = (r: TransactionResponse): string | null =>
    drafts[r.id]?.account_id ?? r.account_id ?? null;
  const guessedCount = rows.filter((r) => r.account_id).length;
  const keepIds = rows.filter((r) => decisionFor(r.id) === "keep").map((r) => r.id);
  const discardIds = rows.filter((r) => decisionFor(r.id) === "discard").map((r) => r.id);

  const applyMutation = useMutation({
    mutationFn: async () => {
      if (keepIds.length) {
        const items: ShadowConfirmItem[] = keepIds.map((id) => ({
          id,
          ...drafts[id],
        }));
        await confirmShadow(items);
      }
      if (discardIds.length) await discardShadow(discardIds);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["gmailShadow"] });
      qc.invalidateQueries({ queryKey: ["transactions"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      setDecisions({});
      setDrafts({});
      Alert.alert(
        "Listo",
        `Confirmadas: ${keepIds.length} · Descartadas: ${discardIds.length}`,
      );
    },
    onError: () => Alert.alert("Error", "No se pudo aplicar la revisión."),
  });

  const toggle = (id: string) =>
    setDecisions((prev) => ({
      ...prev,
      [id]: (prev[id] ?? "keep") === "keep" ? "discard" : "keep",
    }));

  const draftView = (r: TransactionResponse) => {
    const d = drafts[r.id] ?? {};
    return {
      merchant: d.merchant ?? r.merchant ?? "",
      category: d.category ?? r.category ?? "",
      amount: d.amount ?? r.amount,
    };
  };

  const canApply = rows.length > 0 && !applyMutation.isPending;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <View style={styles.headerRow}>
          <Text style={styles.headerTitle}>Revisar correos</Text>
          <Pressable onPress={() => shadowQ.refetch()} hitSlop={8}>
            <Feather name="refresh-cw" size={16} color={Colors.textMuted} />
          </Pressable>
        </View>
        <Text style={styles.headerSub}>
          {shadowQ.isLoading
            ? "Cargando…"
            : shadowQ.isError
              ? "No pude cargar los resultados."
              : rows.length === 0
                ? "Nada pendiente por revisar."
                : `${rows.length} encontrado(s) · Confirmar ${keepIds.length} · Descartar ${discardIds.length}`}
        </Text>
        {guessedCount > 0 && (
          <Text style={styles.guessNote}>
            Adiviné la cuenta de {guessedCount}{" "}
            {guessedCount === 1 ? "movimiento" : "movimientos"} — revisá que estén
            bien antes de confirmar.
          </Text>
        )}
      </View>

      <FlatList
        data={rows}
        keyExtractor={(r) => r.id}
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            refreshing={shadowQ.isFetching && !shadowQ.isLoading}
            onRefresh={() => shadowQ.refetch()}
            tintColor={Colors.accent}
          />
        }
        renderItem={({ item }) => {
          const dec = decisionFor(item.id);
          const v = draftView(item);
          const edited = Boolean(drafts[item.id]);
          const effId = effectiveAccountId(item);
          const acct = effId ? accountById.get(effId) : undefined;
          const acctOverridden = drafts[item.id]?.account_id != null;
          const acctGuessed = !acctOverridden && Boolean(item.account_id);
          return (
            <View style={[styles.row, dec === "discard" && styles.rowDiscard]}>
              <Pressable onPress={() => toggle(item.id)} hitSlop={6} style={styles.check}>
                <Feather
                  name={dec === "keep" ? "check-circle" : "circle"}
                  size={22}
                  color={dec === "keep" ? Colors.income : Colors.textMuted}
                />
              </Pressable>
              <View style={styles.rowBody}>
                <Text style={[styles.rowAmount, dec === "discard" && styles.struck]}>
                  {fmtAmount(v.amount, item.currency)}
                </Text>
                <Text style={styles.rowMeta} numberOfLines={1}>
                  {v.merchant || "—"}
                  {v.category ? ` · ${v.category}` : ""}
                  {edited ? "  ·  editado" : ""}
                </Text>
                <Text style={styles.rowDate}>{item.transaction_date}</Text>
                <Pressable
                  onPress={() => setAccountPickerFor(item)}
                  hitSlop={4}
                  style={styles.acctChip}
                >
                  <Feather
                    name="credit-card"
                    size={12}
                    color={acct ? Colors.textSecondary : Colors.warning}
                  />
                  <Text
                    style={[styles.acctText, !acct && styles.acctMissing]}
                    numberOfLines={1}
                  >
                    {acct ? acct.name : "Sin cuenta — elegí"}
                  </Text>
                  {acctGuessed && acct && <Text style={styles.acctTag}>adivinada</Text>}
                </Pressable>
              </View>
              <Pressable onPress={() => setEditing(item)} hitSlop={6}>
                <Feather name="edit-2" size={16} color={Colors.accent} />
              </Pressable>
            </View>
          );
        }}
        ListEmptyComponent={
          shadowQ.isLoading ? (
            <ActivityIndicator style={{ marginTop: Spacing.xl }} color={Colors.accent} />
          ) : shadowQ.isError ? (
            <View style={styles.emptyBox}>
              <Feather name="alert-triangle" size={28} color={Colors.warning} />
              <Text style={styles.empty}>
                No pude cargar los resultados. Deslizá para reintentar.
              </Text>
              <Pressable
                onPress={() => shadowQ.refetch()}
                style={({ pressed }) => [styles.retryBtn, pressed && { opacity: 0.85 }]}
              >
                <Text style={styles.retryLabel}>Reintentar</Text>
              </Pressable>
            </View>
          ) : (
            <View style={styles.emptyBox}>
              <Feather name="inbox" size={28} color={Colors.border} />
              <Text style={styles.empty}>
                No hay nada por revisar todavía. Conectá Gmail y tocá «Escanear
                ahora». Si recién escaneaste y no aparece nada, revisá los
                remitentes o reconectá Gmail.
              </Text>
            </View>
          )
        }
      />

      {rows.length > 0 && (
        <View style={styles.bottomBar}>
          <Pressable
            onPress={() => applyMutation.mutate()}
            disabled={!canApply}
            style={({ pressed }) => [
              styles.applyBtn,
              !canApply && styles.btnDisabled,
              pressed && canApply && { opacity: 0.85 },
            ]}
          >
            {applyMutation.isPending ? (
              <ActivityIndicator color={Colors.textOnDark} />
            ) : (
              <Text style={styles.applyLabel}>
                Aplicar ({keepIds.length} confirmar · {discardIds.length} descartar)
              </Text>
            )}
          </Pressable>
        </View>
      )}

      <EditModal
        txn={editing}
        initial={editing ? drafts[editing.id] : undefined}
        onClose={() => setEditing(null)}
        onSave={(id, patch) => {
          setDrafts((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
          setEditing(null);
        }}
      />

      <AccountPickerModal
        visible={accountPickerFor !== null}
        currentAccountId={
          accountPickerFor ? effectiveAccountId(accountPickerFor) : null
        }
        currencyFilter={accountPickerFor?.currency}
        allowClear={false}
        onClose={() => setAccountPickerFor(null)}
        onSelect={(acc) => {
          if (acc && accountPickerFor) {
            const rowId = accountPickerFor.id;
            setDrafts((prev) => ({
              ...prev,
              [rowId]: { ...prev[rowId], account_id: acc.id },
            }));
          }
          setAccountPickerFor(null);
        }}
      />
    </SafeAreaView>
  );
}

function EditModal({
  txn,
  initial,
  onClose,
  onSave,
}: {
  txn: TransactionResponse | null;
  initial: Partial<ShadowConfirmItem> | undefined;
  onClose: () => void;
  onSave: (id: string, patch: Partial<ShadowConfirmItem>) => void;
}) {
  const sign = txn && txn.amount < 0 ? -1 : 1;
  const [draft, setDraft] = useState<Draft>({
    amountMagnitude: "",
    merchant: "",
    category: "",
    categoryId: null,
    date: "",
  });
  const [categoryPickerVisible, setCategoryPickerVisible] = useState(false);

  // Re-seed the local draft whenever a different row opens.
  const seedKey = txn?.id ?? "none";
  useEffect(() => {
    if (!txn) return;
    const amt = initial?.amount ?? txn.amount;
    setDraft({
      amountMagnitude: String(Math.abs(amt)),
      merchant: initial?.merchant ?? txn.merchant ?? "",
      category: initial?.category ?? txn.category ?? "",
      categoryId: initial?.category_id ?? txn.category_id ?? null,
      date: initial?.transaction_date ?? txn.transaction_date,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedKey]);

  if (!txn) return null;

  const save = () => {
    const mag = parseFloat(draft.amountMagnitude.replace(",", "."));
    const patch: Partial<ShadowConfirmItem> = {
      merchant: draft.merchant.trim() || undefined,
      // Set both name + FK so the confirmed row matches the Categorías screen.
      category: draft.category.trim() || undefined,
      category_id: draft.categoryId ?? undefined,
      transaction_date: draft.date.trim() || undefined,
    };
    if (!isNaN(mag) && mag > 0) patch.amount = sign * mag;
    onSave(txn.id, patch);
  };

  return (
    <Modal visible transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.modalBackdrop}>
        <View style={styles.modalCard}>
          <Text style={styles.modalTitle}>Editar movimiento</Text>

          <Text style={styles.label}>Monto ({sign < 0 ? "gasto" : "ingreso"})</Text>
          <TextInput
            style={styles.input}
            value={draft.amountMagnitude}
            onChangeText={(t) => setDraft((d) => ({ ...d, amountMagnitude: t }))}
            keyboardType="decimal-pad"
          />
          <Text style={styles.label}>Comercio</Text>
          <TextInput
            style={styles.input}
            value={draft.merchant}
            onChangeText={(t) => setDraft((d) => ({ ...d, merchant: t }))}
          />
          <Text style={styles.label}>Categoría</Text>
          <Pressable
            onPress={() => setCategoryPickerVisible(true)}
            style={({ pressed }) => [styles.input, styles.selectRow, pressed && { opacity: 0.7 }]}
          >
            <Text style={[styles.selectText, !draft.category.trim() && styles.selectPlaceholder]}>
              {draft.category.trim() || "Elegí una categoría"}
            </Text>
            <Feather name="chevron-right" size={18} color={Colors.textMuted} />
          </Pressable>
          <Text style={styles.label}>Fecha (YYYY-MM-DD)</Text>
          <TextInput
            style={styles.input}
            value={draft.date}
            onChangeText={(t) => setDraft((d) => ({ ...d, date: t }))}
          />

          <View style={styles.modalActions}>
            <Pressable onPress={onClose} style={({ pressed }) => [styles.modalBtn, pressed && { opacity: 0.7 }]}>
              <Text style={styles.modalCancel}>Cancelar</Text>
            </Pressable>
            <Pressable onPress={save} style={({ pressed }) => [styles.modalBtn, styles.modalSave, pressed && { opacity: 0.85 }]}>
              <Text style={styles.modalSaveLabel}>Guardar</Text>
            </Pressable>
          </View>
        </View>
      </View>

      <CategoryPickerModal
        visible={categoryPickerVisible}
        kind={sign < 0 ? "expense" : "income"}
        currentCategoryId={draft.categoryId}
        allowClear={false}
        onClose={() => setCategoryPickerVisible(false)}
        onSelect={(cat) => {
          if (cat) setDraft((d) => ({ ...d, category: cat.name, categoryId: cat.id }));
          setCategoryPickerVisible(false);
        }}
      />
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Colors.bg },
  header: {
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.md,
    paddingBottom: Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    backgroundColor: Colors.bgCard,
  },
  headerRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  headerTitle: { fontSize: FontSize.lg, fontWeight: "700", color: Colors.textPrimary },
  headerSub: { fontSize: FontSize.sm, color: Colors.textMuted, marginTop: 2 },
  content: { padding: Spacing.md, gap: Spacing.sm, paddingBottom: Spacing.xl },
  row: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md - 2,
    gap: Spacing.sm,
  },
  rowDiscard: { opacity: 0.5 },
  check: { paddingRight: Spacing.xs },
  rowBody: { flex: 1, gap: 1 },
  rowAmount: { fontSize: FontSize.md, fontWeight: "700", color: Colors.textPrimary },
  struck: { textDecorationLine: "line-through" },
  rowMeta: { fontSize: FontSize.sm, color: Colors.textSecondary },
  rowDate: { fontSize: FontSize.xs, color: Colors.textMuted },
  guessNote: {
    fontSize: FontSize.xs,
    color: Colors.textSecondary,
    marginTop: Spacing.xs,
    lineHeight: 16,
  },
  acctChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    marginTop: 2,
  },
  acctText: { fontSize: FontSize.xs, color: Colors.textSecondary, flexShrink: 1 },
  acctMissing: { color: Colors.warning },
  acctTag: { fontSize: 10, color: Colors.textMuted, fontStyle: "italic" },
  emptyBox: {
    alignItems: "center",
    gap: Spacing.sm,
    marginTop: Spacing.xl,
    paddingHorizontal: Spacing.md,
  },
  empty: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    textAlign: "center",
    lineHeight: 20,
  },
  retryBtn: {
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.lg,
    paddingVertical: Spacing.sm,
  },
  retryLabel: { color: Colors.accent, fontWeight: "600", fontSize: FontSize.sm },
  bottomBar: {
    padding: Spacing.md,
    borderTopWidth: 1,
    borderTopColor: Colors.borderLight,
    backgroundColor: Colors.bgCard,
  },
  applyBtn: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.md - 2,
    alignItems: "center",
  },
  btnDisabled: { backgroundColor: Colors.border },
  applyLabel: { fontSize: FontSize.md, fontWeight: "600", color: Colors.textOnDark },

  // modal
  modalBackdrop: { flex: 1, backgroundColor: Colors.overlay, justifyContent: "flex-end" },
  modalCard: {
    backgroundColor: Colors.bg,
    borderTopLeftRadius: Radius.lg,
    borderTopRightRadius: Radius.lg,
    padding: Spacing.md,
    gap: Spacing.xs,
  },
  modalTitle: { fontSize: FontSize.md, fontWeight: "700", color: Colors.textPrimary, marginBottom: Spacing.xs },
  label: { fontSize: FontSize.sm, fontWeight: "600", color: Colors.textSecondary, marginTop: Spacing.xs },
  input: {
    backgroundColor: Colors.bgInput,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    fontSize: FontSize.md,
    color: Colors.textPrimary,
  },
  selectRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  selectText: { fontSize: FontSize.md, color: Colors.textPrimary },
  selectPlaceholder: { color: Colors.textMuted },
  modalActions: { flexDirection: "row", gap: Spacing.sm, marginTop: Spacing.md },
  modalBtn: { flex: 1, borderRadius: Radius.md, paddingVertical: Spacing.sm + 2, alignItems: "center" },
  modalCancel: { fontSize: FontSize.md, color: Colors.textSecondary, fontWeight: "600" },
  modalSave: { backgroundColor: Colors.accent },
  modalSaveLabel: { fontSize: FontSize.md, color: Colors.textOnDark, fontWeight: "600" },
});
