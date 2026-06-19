/**
 * Account picker bottom sheet — choose which account a movement belongs to.
 *
 * Lists the caller's active accounts (all currencies — reassigning to a
 * different-currency account converts the amount server-side). Calls
 * `onSelect(account | null)` and closes; it does NOT mutate anything itself, so
 * it's reused by the transaction edit modal. A "Sin cuenta" row clears the
 * assignment (returns null).
 *
 * Mirrors CategoryPickerModal's sheet pattern.
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

import {
  type AccountResponse,
  ACCOUNT_TYPE_LABELS,
  fetchAccounts,
} from "../api/accounts";
import { Colors, FontSize, Radius, Spacing } from "../theme";

interface Props {
  visible: boolean;
  currentAccountId?: string | null;
  /** Show a "Sin cuenta" row that returns null (clears). Default true. */
  allowClear?: boolean;
  /** When set, only accounts of this currency are listed (used by the Gmail
   * review, where the confirm path rejects cross-currency instead of
   * converting). Default: no filter. */
  currencyFilter?: string;
  onClose: () => void;
  onSelect: (account: AccountResponse | null) => void;
}

function currencyLabel(currency: string): string {
  if (currency === "CRC") return "₡";
  if (currency === "USD") return "$";
  return currency;
}

export function AccountPickerModal({
  visible,
  currentAccountId,
  allowClear = true,
  currencyFilter,
  onClose,
  onSelect,
}: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["accounts", "active"],
    queryFn: () => fetchAccounts(false),
    enabled: visible,
  });

  const accounts = (data ?? []).filter(
    (a) => !a.archived && (!currencyFilter || a.currency === currencyFilter),
  );

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.overlay}>
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.title}>Elegí una cuenta</Text>

          {isLoading ? (
            <View style={styles.center}>
              <ActivityIndicator color={Colors.accent} />
            </View>
          ) : isError ? (
            <Text style={styles.muted}>No se pudieron cargar las cuentas.</Text>
          ) : accounts.length === 0 ? (
            <Text style={styles.muted}>
              No tenés cuentas todavía. Creá una en la pestaña Cuentas.
            </Text>
          ) : (
            <ScrollView
              style={styles.body}
              showsVerticalScrollIndicator={false}
              keyboardShouldPersistTaps="handled"
            >
              {allowClear && (
                <Pressable
                  onPress={() => onSelect(null)}
                  style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
                >
                  <Feather name="slash" size={16} color={Colors.textMuted} />
                  <Text style={[styles.rowName, styles.rowNameFlex]}>Sin cuenta</Text>
                  {currentAccountId == null && (
                    <Feather name="check" size={18} color={Colors.accent} />
                  )}
                </Pressable>
              )}

              {accounts.map((acc) => (
                <Pressable
                  key={acc.id}
                  onPress={() => onSelect(acc)}
                  style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
                >
                  <Feather name="credit-card" size={16} color={Colors.textMuted} />
                  <View style={styles.rowTexts}>
                    <Text style={styles.rowName} numberOfLines={1}>
                      {acc.name}
                    </Text>
                    <Text style={styles.rowSub}>
                      {currencyLabel(acc.currency)} ·{" "}
                      {ACCOUNT_TYPE_LABELS[acc.account_type] ?? acc.account_type}
                    </Text>
                  </View>
                  {currentAccountId === acc.id && (
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
  rowNameFlex: { flex: 1 },
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
