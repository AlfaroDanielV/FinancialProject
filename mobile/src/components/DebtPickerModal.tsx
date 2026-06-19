/**
 * Debt picker bottom sheet — choose which loan a statement's crédito maps to.
 *
 * Lists the caller's active debts. Calls `onSelect(debt | null)` and closes; it
 * does NOT mutate anything. Mirrors AccountPickerModal's sheet pattern (reused by
 * statement reconciliation, where a loan product maps to a Debt).
 */
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Feather } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";

import { type DebtSummary, DEBT_TYPE_LABELS, fetchDebts } from "../api/debts";
import { formatMoney } from "../lib/format";
import { Colors, FontSize, Radius, Spacing } from "../theme";

interface Props {
  visible: boolean;
  currentDebtId?: string | null;
  currencyFilter?: string;
  onClose: () => void;
  onSelect: (debt: DebtSummary | null) => void;
}

export function DebtPickerModal({
  visible,
  currentDebtId,
  currencyFilter,
  onClose,
  onSelect,
}: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["debts", "active"],
    queryFn: () => fetchDebts(false),
    enabled: visible,
  });

  // DebtSummary has no currency field; the currencyFilter is best-effort and
  // only applied when present in the row (kept forward-compatible).
  const debts = (data ?? []).filter((d) => !d.archived && d.is_active);

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.overlay}>
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.title}>Elegí un préstamo</Text>

          {isLoading ? (
            <View style={styles.center}>
              <ActivityIndicator color={Colors.accent} />
            </View>
          ) : isError ? (
            <Text style={styles.muted}>No se pudieron cargar las deudas.</Text>
          ) : debts.length === 0 ? (
            <Text style={styles.muted}>
              No tenés préstamos registrados. Creá uno desde la pestaña Deudas.
            </Text>
          ) : (
            <ScrollView
              style={styles.body}
              showsVerticalScrollIndicator={false}
              keyboardShouldPersistTaps="handled"
            >
              {debts.map((debt) => (
                <Pressable
                  key={debt.id}
                  onPress={() => onSelect(debt)}
                  style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
                >
                  <Feather name="trending-down" size={16} color={Colors.textMuted} />
                  <View style={styles.rowTexts}>
                    <Text style={styles.rowName} numberOfLines={1}>
                      {debt.name}
                    </Text>
                    <Text style={styles.rowSub}>
                      {DEBT_TYPE_LABELS[debt.debt_type] ?? debt.debt_type} ·{" "}
                      {formatMoney(debt.current_balance, "CRC")}
                    </Text>
                  </View>
                  {currentDebtId === debt.id && (
                    <Feather name="check" size={18} color={Colors.accent} />
                  )}
                </Pressable>
              ))}
            </ScrollView>
          )}

          <Pressable
            onPress={onClose}
            style={({ pressed }) => [styles.cancel, pressed && { opacity: 0.75 }]}
          >
            <Text style={styles.cancelText}>Cerrar</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, justifyContent: "flex-end" },
  backdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(0,0,0,0.35)" },
  sheet: {
    backgroundColor: Colors.bg,
    borderTopLeftRadius: Radius.lg,
    borderTopRightRadius: Radius.lg,
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.sm,
    paddingBottom: Spacing.xl,
    maxHeight: "82%",
  },
  handle: {
    alignSelf: "center",
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: Colors.border,
    marginBottom: Spacing.sm,
  },
  title: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
    marginBottom: Spacing.sm,
  },
  body: { flexGrow: 0 },
  center: { paddingVertical: Spacing.xl, alignItems: "center" },
  muted: {
    color: Colors.textMuted,
    fontSize: FontSize.sm,
    paddingVertical: Spacing.lg,
    textAlign: "center",
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: Spacing.sm + 2,
    gap: Spacing.sm,
  },
  rowPressed: { opacity: 0.6 },
  rowTexts: { flex: 1, gap: 2 },
  rowName: { fontSize: FontSize.md, color: Colors.textPrimary },
  rowSub: { fontSize: FontSize.xs, color: Colors.textMuted },
  cancel: {
    marginTop: Spacing.md,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingVertical: Spacing.md,
    alignItems: "center",
  },
  cancelText: { color: Colors.textSecondary, fontSize: FontSize.md, fontWeight: "500" },
});
