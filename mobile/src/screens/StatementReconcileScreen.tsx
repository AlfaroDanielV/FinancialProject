/**
 * Bank-statement reconciliation form (PDF → balance anchor).
 *
 * The user uploads a bank statement PDF; the backend reads every product + its
 * closing balance and suggests a matching account/debt. Each detected product
 * shows as a row with a target dropdown (pre-filled with the best guess) and an
 * on/off toggle. "Reconciliar (N)" anchors each enabled deposit/credit account
 * to its corte balance and sets each loan's current_balance — at the fecha de
 * corte, append-only (the same engine as "corregí mi saldo").
 *
 * Works for a BUNDLED statement (e.g. BAC = several accounts + a loan in one
 * PDF). "LLM extracts; rules decide": the deterministic reconcile endpoint is
 * the only write path.
 */
import { useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import * as DocumentPicker from "expo-document-picker";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  parseStatement,
  reconcileStatement,
  STATEMENT_KIND_LABELS,
  type StatementExtraction,
  type StatementKind,
  type StatementProduct,
  type StatementReconcileItem,
} from "../api/statements";
import { fetchAccounts, type AccountResponse } from "../api/accounts";
import { fetchDebts, type DebtSummary } from "../api/debts";
import { AccountPickerModal } from "../components/AccountPickerModal";
import { DebtPickerModal } from "../components/DebtPickerModal";
import { DateField } from "../components/fields/DateField";
import { formatMoney } from "../lib/format";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import type { AccountsStackParamList } from "../navigation/AccountsNavigator";

type Nav = NativeStackNavigationProp<AccountsStackParamList, "StatementReconcile">;

const LOW_CONFIDENCE = 0.65;

function todayIso(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

function reviewLabel(p: StatementProduct): string {
  if (p.review_reason === "conservation_mismatch")
    return "Revisar — el saldo no cuadra con los movimientos";
  if (p.review_reason === "no_target_role")
    return "Revisar — no encontré el saldo a aplicar";
  return "Revisar antes de aplicar";
}

interface RowState {
  enabled: boolean;
  targetId: string | null;
}

export function StatementReconcileScreen() {
  const nav = useNavigation<Nav>();
  const qc = useQueryClient();

  const [extraction, setExtraction] = useState<StatementExtraction | null>(null);
  const [corte, setCorte] = useState<string>("");
  const [rows, setRows] = useState<RowState[]>([]);
  const [pickerRow, setPickerRow] = useState<number | null>(null);
  const [lowConf, setLowConf] = useState(false);

  // Name lookups so a row can show its mapped target's name.
  const { data: accounts } = useQuery({
    queryKey: ["accounts", "active"],
    queryFn: () => fetchAccounts(false),
  });
  const { data: debts } = useQuery({
    queryKey: ["debts", "active"],
    queryFn: () => fetchDebts(false),
  });
  const accountsById = useMemo(() => {
    const m = new Map<string, AccountResponse>();
    (accounts ?? []).forEach((a) => m.set(a.id, a));
    return m;
  }, [accounts]);
  const debtsById = useMemo(() => {
    const m = new Map<string, DebtSummary>();
    (debts ?? []).forEach((d) => m.set(d.id, d));
    return m;
  }, [debts]);

  const parseMutation = useMutation({
    mutationFn: (uri: string) => parseStatement(uri),
    onSuccess: (data) => {
      setExtraction(data);
      setCorte(data.corte_date ?? todayIso());
      setLowConf(data.confidence < LOW_CONFIDENCE);
      setRows(
        data.products.map((p) => {
          const suggested =
            p.kind === "loan" ? p.suggested_debt_id : p.suggested_account_id;
          // Conservation-flagged rows default OFF — the user confirms them. A
          // row with no resolved balance can't anchor anything.
          return {
            enabled:
              Boolean(suggested) && !p.needs_review && p.closing_balance != null,
            targetId: suggested,
          };
        }),
      );
    },
    onError: () => {
      Alert.alert(
        "No pude leer el estado",
        "Probá con otro PDF o reconciliá el saldo a mano desde la cuenta.",
      );
    },
  });

  const reconcileMutation = useMutation({
    mutationFn: () => {
      const items: StatementReconcileItem[] = [];
      (extraction?.products ?? []).forEach((p, i) => {
        const row = rows[i];
        if (row?.enabled && row.targetId && p.closing_balance != null) {
          // Self-stamp identity onto the target only for a confident identity
          // match the user did NOT override — otherwise we'd poison the next
          // statement's deterministic resolution.
          const suggestedId =
            p.kind === "loan" ? p.suggested_debt_id : p.suggested_account_id;
          const stampable =
            (p.match_confidence ?? 0) >= 1 && row.targetId === suggestedId;
          items.push({
            kind: p.kind,
            target_id: row.targetId,
            closing_balance: String(p.closing_balance),
            currency: p.currency,
            iban: stampable ? p.iban : null,
            account_number: stampable ? p.account_number : null,
            last4: stampable ? p.account_last4 : null,
            conservation_ok: p.conservation_ok ?? null,
            needs_review: p.needs_review ?? false,
          });
        }
      });
      return reconcileStatement({ corte_date: corte, items });
    },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["debts"] });
      qc.invalidateQueries({ queryKey: ["debtOverview"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      Alert.alert(
        "Reconciliado",
        `Listo: ${res.results.length} ${
          res.results.length === 1 ? "saldo actualizado" : "saldos actualizados"
        } al ${corte}.`,
      );
      nav.goBack();
    },
    onError: () => {
      Alert.alert("Error", "No se pudo reconciliar. Revisá las cuentas elegidas.");
    },
  });

  const pickPdf = async () => {
    if (parseMutation.isPending) return;
    const result = await DocumentPicker.getDocumentAsync({
      type: "application/pdf",
      copyToCacheDirectory: true,
    });
    if (result.canceled || result.assets.length === 0) return;
    parseMutation.mutate(result.assets[0].uri);
  };

  const setRow = (i: number, patch: Partial<RowState>) =>
    setRows((prev) => prev.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const targetName = (kind: StatementKind, targetId: string | null): string => {
    if (!targetId) return "Elegí una cuenta";
    if (kind === "loan") return debtsById.get(targetId)?.name ?? "Préstamo";
    return accountsById.get(targetId)?.name ?? "Cuenta";
  };

  const enabledCount = rows.filter((r, i) => {
    return (
      r.enabled && r.targetId && extraction?.products[i]?.closing_balance != null
    );
  }).length;
  const canSubmit = enabledCount > 0 && Boolean(corte) && !reconcileMutation.isPending;

  const pickerProduct =
    pickerRow != null ? extraction?.products[pickerRow] : undefined;

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        {/* ── upload ───────────────────────────────────────────────────────── */}
        <View style={styles.pdfCard}>
          <Text style={styles.pdfTitle}>Subí tu estado de cuenta (PDF)</Text>
          <Text style={styles.pdfBody}>
            Leo el saldo al corte de cada cuenta y lo dejo igual al de tu banco.
          </Text>
          <Pressable
            onPress={pickPdf}
            disabled={parseMutation.isPending}
            style={({ pressed }) => [styles.pdfBtn, pressed && { opacity: 0.85 }]}
          >
            {parseMutation.isPending ? (
              <ActivityIndicator color={Colors.accent} />
            ) : (
              <>
                <Feather name="upload" size={16} color={Colors.accent} />
                <Text style={styles.pdfBtnLabel}>
                  {extraction ? "Subir otro estado" : "Elegir PDF"}
                </Text>
              </>
            )}
          </Pressable>
          {lowConf && (
            <Text style={styles.lowConf}>
              Leí el documento pero no estoy seguro de todos los montos. Revisalos
              antes de reconciliar.
            </Text>
          )}
        </View>

        {extraction && (
          <>
            <View style={styles.metaRow}>
              <Text style={styles.metaBank}>{extraction.bank ?? "Estado de cuenta"}</Text>
              <Text style={styles.metaCount}>
                {extraction.products.length}{" "}
                {extraction.products.length === 1 ? "producto" : "productos"}
              </Text>
            </View>

            <View style={styles.fieldGroup}>
              <Text style={styles.label}>Fecha de corte</Text>
              <DateField
                value={corte}
                onChange={setCorte}
                style={styles.input}
                maximumDate={new Date()}
              />
            </View>

            {extraction.products.map((p, i) => {
              const row = rows[i];
              const active = row?.enabled ?? false;
              return (
                <View key={i} style={[styles.row, active && styles.rowActive]}>
                  <View style={styles.rowHeader}>
                    <View style={styles.rowHeaderLeft}>
                      <View style={styles.kindPill}>
                        <Text style={styles.kindPillText}>
                          {STATEMENT_KIND_LABELS[p.kind]}
                        </Text>
                      </View>
                      <Text style={styles.rowTitle} numberOfLines={1}>
                        {p.label ?? STATEMENT_KIND_LABELS[p.kind]}
                        {p.account_last4 ? ` ·…${p.account_last4}` : ""}
                      </Text>
                    </View>
                    <Switch
                      value={active}
                      disabled={p.closing_balance == null}
                      onValueChange={(v) => setRow(i, { enabled: v })}
                      trackColor={{ false: Colors.border, true: Colors.accentSoft }}
                      thumbColor={active ? Colors.accent : Colors.bgCard}
                    />
                  </View>

                  {p.needs_review || p.conservation_ok === false ? (
                    <View style={styles.reviewBadge}>
                      <Feather name="alert-triangle" size={12} color={Colors.warning} />
                      <Text style={styles.reviewBadgeText}>{reviewLabel(p)}</Text>
                    </View>
                  ) : p.conservation_ok == null ? (
                    <Text style={styles.unverified}>
                      Sin verificar — confirmá el monto.
                    </Text>
                  ) : null}

                  {p.attributed_instruments && p.attributed_instruments.length > 0 ? (
                    <Text style={styles.instruments} numberOfLines={1}>
                      Tarjetas: {p.attributed_instruments.join(", ")}
                    </Text>
                  ) : null}

                  <Text style={styles.rowBalance}>
                    {p.closing_balance != null ? (
                      <>
                        {formatMoney(p.closing_balance, p.currency)}{" "}
                        <Text style={styles.rowBalanceCur}>{p.currency}</Text>
                      </>
                    ) : (
                      <Text style={styles.rowBalanceCur}>Sin saldo aplicable</Text>
                    )}
                  </Text>

                  <Pressable
                    onPress={() => setPickerRow(i)}
                    style={({ pressed }) => [
                      styles.targetBtn,
                      !row?.targetId && styles.targetBtnEmpty,
                      pressed && { opacity: 0.8 },
                    ]}
                  >
                    <Feather
                      name={p.kind === "loan" ? "trending-down" : "credit-card"}
                      size={14}
                      color={row?.targetId ? Colors.accent : Colors.textMuted}
                    />
                    <Text
                      style={[
                        styles.targetText,
                        !row?.targetId && styles.targetTextEmpty,
                      ]}
                      numberOfLines={1}
                    >
                      {targetName(p.kind, row?.targetId ?? null)}
                    </Text>
                    <Feather name="chevron-down" size={16} color={Colors.textMuted} />
                  </Pressable>
                </View>
              );
            })}

            <Pressable
              onPress={() => canSubmit && reconcileMutation.mutate()}
              disabled={!canSubmit}
              style={({ pressed }) => [
                styles.submitBtn,
                !canSubmit && styles.submitDisabled,
                pressed && canSubmit && { opacity: 0.85 },
              ]}
            >
              {reconcileMutation.isPending ? (
                <ActivityIndicator color={Colors.textOnDark} />
              ) : (
                <Text style={styles.submitLabel}>
                  Reconciliar{enabledCount > 0 ? ` (${enabledCount})` : ""}
                </Text>
              )}
            </Pressable>
          </>
        )}
      </ScrollView>

      {/* ── per-row picker ─────────────────────────────────────────────────── */}
      {pickerProduct && pickerProduct.kind === "loan" ? (
        <DebtPickerModal
          visible={pickerRow != null}
          currentDebtId={pickerRow != null ? rows[pickerRow]?.targetId : null}
          onClose={() => setPickerRow(null)}
          onSelect={(debt) => {
            if (pickerRow != null) setRow(pickerRow, { targetId: debt?.id ?? null });
            setPickerRow(null);
          }}
        />
      ) : (
        <AccountPickerModal
          visible={pickerRow != null}
          currentAccountId={pickerRow != null ? rows[pickerRow]?.targetId : null}
          currencyFilter={pickerProduct?.currency}
          allowClear
          onClose={() => setPickerRow(null)}
          onSelect={(acc) => {
            if (pickerRow != null) setRow(pickerRow, { targetId: acc?.id ?? null });
            setPickerRow(null);
          }}
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Colors.bg },
  scroll: { flex: 1 },
  content: { padding: Spacing.md, gap: Spacing.md, paddingBottom: Spacing.xl },

  fieldGroup: { gap: Spacing.xs },
  label: {
    fontSize: FontSize.sm,
    fontWeight: "600",
    color: Colors.textSecondary,
    letterSpacing: 0.3,
  },
  input: {
    backgroundColor: Colors.bgInput,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
  },

  pdfCard: {
    backgroundColor: Colors.accentBg,
    borderRadius: Radius.md,
    padding: Spacing.md,
    gap: Spacing.sm,
  },
  pdfTitle: { fontSize: FontSize.md, fontWeight: "600", color: Colors.textPrimary },
  pdfBody: { fontSize: FontSize.sm, color: Colors.textSecondary, lineHeight: 19 },
  pdfBtn: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.sm + 2,
    backgroundColor: Colors.bgCard,
  },
  pdfBtnLabel: { fontSize: FontSize.sm, fontWeight: "600", color: Colors.accent },
  lowConf: { fontSize: FontSize.xs, color: Colors.warning, lineHeight: 17 },

  metaRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  metaBank: { fontSize: FontSize.md, fontWeight: "700", color: Colors.textPrimary },
  metaCount: { fontSize: FontSize.sm, color: Colors.textMuted },

  row: {
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    padding: Spacing.md,
    gap: Spacing.sm,
  },
  rowActive: { borderColor: Colors.accentSoft },
  rowHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: Spacing.sm,
  },
  rowHeaderLeft: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
  },
  kindPill: {
    backgroundColor: Colors.bgElevated,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 2,
  },
  kindPillText: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    color: Colors.textSecondary,
  },
  rowTitle: { flex: 1, fontSize: FontSize.sm, color: Colors.textPrimary },
  reviewBadge: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.xs,
  },
  reviewBadgeText: { fontSize: FontSize.xs, color: Colors.warning, flex: 1 },
  unverified: { fontSize: FontSize.xs, color: Colors.textMuted },
  instruments: { fontSize: FontSize.xs, color: Colors.textMuted },
  rowBalance: { fontSize: FontSize.lg, fontWeight: "700", color: Colors.textPrimary },
  rowBalanceCur: { fontSize: FontSize.xs, color: Colors.textMuted, fontWeight: "500" },
  targetBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    backgroundColor: Colors.bgInput,
  },
  targetBtnEmpty: { borderColor: Colors.warning },
  targetText: { flex: 1, fontSize: FontSize.sm, color: Colors.textPrimary },
  targetTextEmpty: { color: Colors.textMuted },

  submitBtn: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.md,
    alignItems: "center",
    marginTop: Spacing.sm,
  },
  submitDisabled: { backgroundColor: Colors.border },
  submitLabel: { fontSize: FontSize.md, fontWeight: "600", color: Colors.textOnDark },
});
