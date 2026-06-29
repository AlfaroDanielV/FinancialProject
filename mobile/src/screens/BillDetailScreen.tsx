/**
 * Phase 6f B10 — Bill detail screen.
 *
 * Shows bill metadata, the current occurrence status, and a mark-paid form
 * when the occurrence is actionable (pending/overdue/partially_paid).
 * Also provides Pausar/Reanudar and Archivar actions.
 */
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import type { RouteProp } from "@react-navigation/native";

import {
  archiveBill,
  fetchRecurringBills,
  markBillPaid,
  newIdempotencyKey,
  pauseBill,
  resumeBill,
  ACTIONABLE_STATUSES,
  FREQUENCY_LABELS,
  type RecurringBillResponse,
  type BillOccurrenceResponse,
} from "../api/bills";
import { fetchAccounts, type AccountResponse } from "../api/accounts";
import { BillFormModal } from "../components/BillFormModal";
import { DateField } from "../components/fields/DateField";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";
import type { MasStackParamList } from "../navigation/MasNavigator";

// ── helpers ───────────────────────────────────────────────────────────────────

function fmtAmount(amount: number | null, currency: string): string {
  if (amount === null) return "Variable";
  const symbol = currency === "CRC" ? "₡" : currency;
  const abs = Math.abs(amount);
  const formatted =
    currency === "CRC"
      ? abs.toLocaleString("es-CR", { maximumFractionDigits: 0 })
      : abs.toLocaleString("es-CR", { minimumFractionDigits: 2 });
  return `${symbol} ${formatted}`;
}

function fmtDate(iso: string): string {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("es-CR", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

function todayIso(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

const STATUS_LABELS: Record<string, string> = {
  pending: "Pendiente",
  overdue: "Vencido",
  partially_paid: "Pago parcial",
  paid: "Pagado",
  skipped: "Omitido",
  cancelled: "Cancelado",
};

function statusColor(status: string): string {
  if (status === "paid") return Colors.income;
  if (status === "overdue") return Colors.overdue;
  if (status === "partially_paid") return Colors.warning;
  return Colors.textSecondary;
}

// ── mark-paid form ────────────────────────────────────────────────────────────

function MarkPaidForm({
  bill,
  occurrence,
  accounts,
  onSuccess,
}: {
  bill: RecurringBillResponse;
  occurrence: BillOccurrenceResponse;
  accounts: AccountResponse[];
  onSuccess: () => void;
}) {
  const queryClient = useQueryClient();

  const defaultAmount = occurrence.amount_expected ?? bill.amount_expected ?? 0;
  const [amount, setAmount] = useState(String(defaultAmount));
  // Flexible Payment Dates: default to TODAY (you can pick any date), not the
  // occurrence's due_date.
  const [paidAt, setPaidAt] = useState(todayIso());
  const [accountId, setAccountId] = useState<string>(bill.account_id ?? "");
  const [description, setDescription] = useState("");
  const [idempotencyKey] = useState(() => newIdempotencyKey());
  const [apiError, setApiError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const markPaidMutation = useMutation({
    mutationFn: () =>
      markBillPaid(bill.id, {
        amount_paid: parseFloat(amount) || undefined,
        paid_at: paidAt || undefined,
        account_id: accountId || undefined,
        description: description.trim() || undefined,
        idempotency_key: idempotencyKey,
      }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["bill-occurrences"] });
      queryClient.invalidateQueries({ queryKey: ["recurring-bills"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      const msg = data.warning ? `Pagado. ${data.warning}` : "¡Pago registrado!";
      setSuccessMsg(msg);
      setApiError(null);
      onSuccess();
    },
    onError: (err: unknown) => {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setApiError(typeof detail === "string" ? detail : "Error al registrar el pago.");
    },
  });

  if (successMsg) {
    return (
      <View style={formStyles.successBox}>
        <Feather name="check-circle" size={18} color={Colors.income} />
        <Text style={formStyles.successText}>{successMsg}</Text>
      </View>
    );
  }

  const isSubmitting = markPaidMutation.isPending;

  return (
    <View style={formStyles.container}>
      <Text style={formStyles.sectionTitle}>Registrar pago</Text>

      <View style={formStyles.field}>
        <Text style={formStyles.label}>Monto pagado ({bill.currency})</Text>
        <TextInput
          style={formStyles.input}
          value={amount}
          onChangeText={setAmount}
          keyboardType="numeric"
          placeholderTextColor={Colors.textMuted}
        />
      </View>

      <View style={formStyles.field}>
        <Text style={formStyles.label}>Fecha de pago</Text>
        <DateField value={paidAt} onChange={setPaidAt} style={formStyles.input} />
      </View>

      {accounts.length > 0 && (
        <View style={formStyles.field}>
          <Text style={formStyles.label}>Cuenta</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={formStyles.pills}>
            {[{ id: "", name: "Sin cuenta" }, ...accounts].map((acc) => (
              <Pressable
                key={acc.id}
                style={[
                  formStyles.pill,
                  accountId === acc.id && formStyles.pillActive,
                ]}
                onPress={() => setAccountId(acc.id)}
              >
                <Text
                  style={[
                    formStyles.pillText,
                    accountId === acc.id && formStyles.pillTextActive,
                  ]}
                >
                  {acc.name}
                </Text>
              </Pressable>
            ))}
          </ScrollView>
        </View>
      )}

      <View style={formStyles.field}>
        <Text style={formStyles.label}>Descripción (opcional)</Text>
        <TextInput
          style={formStyles.input}
          value={description}
          onChangeText={setDescription}
          placeholder="Nota sobre el pago"
          placeholderTextColor={Colors.textMuted}
        />
      </View>

      {apiError && <Text style={formStyles.errorText}>{apiError}</Text>}

      <Pressable
        style={({ pressed }) => [
          formStyles.submitBtn,
          (isSubmitting || pressed) && formStyles.submitBtnDisabled,
        ]}
        onPress={() => markPaidMutation.mutate()}
        disabled={isSubmitting}
      >
        {isSubmitting ? (
          <ActivityIndicator color={Colors.textOnDark} size="small" />
        ) : (
          <Text style={formStyles.submitBtnText}>Marcar como pagado</Text>
        )}
      </Pressable>
    </View>
  );
}

const formStyles = StyleSheet.create({
  container: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    gap: Spacing.sm,
    ...CardShadow,
  },
  sectionTitle: {
    fontSize: FontSize.md,
    fontWeight: "700",
    color: Colors.textPrimary,
    marginBottom: Spacing.xs,
  },
  field: {
    gap: Spacing.xs,
  },
  label: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontWeight: "500",
  },
  input: {
    backgroundColor: Colors.bgInput,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: Spacing.sm - 2,
    fontSize: FontSize.md,
    color: Colors.textPrimary,
  },
  pills: {
    flexDirection: "row",
  },
  pill: {
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: Spacing.xs,
    marginRight: Spacing.xs,
    backgroundColor: Colors.bg,
  },
  pillActive: {
    backgroundColor: Colors.accentBg,
    borderColor: Colors.accent,
  },
  pillText: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  pillTextActive: {
    color: Colors.accent,
    fontWeight: "600",
  },
  errorText: {
    fontSize: FontSize.sm,
    color: Colors.overdue,
  },
  successBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    padding: Spacing.md,
    backgroundColor: Colors.accentBg,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.accentSoft,
  },
  successText: {
    flex: 1,
    fontSize: FontSize.sm,
    color: Colors.income,
    fontWeight: "500",
  },
  submitBtn: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.sm,
    alignItems: "center",
    justifyContent: "center",
    marginTop: Spacing.xs,
    minHeight: 44,
  },
  submitBtnDisabled: {
    opacity: 0.65,
  },
  submitBtnText: {
    fontSize: FontSize.md,
    fontWeight: "700",
    color: Colors.textOnDark,
  },
});

// ── main screen ───────────────────────────────────────────────────────────────

type Props = {
  navigation: NativeStackNavigationProp<MasStackParamList, "BillDetail">;
  route: RouteProp<MasStackParamList, "BillDetail">;
};

export function BillDetailScreen({ navigation, route }: Props) {
  const { bill: initialBill, occurrence } = route.params;
  const queryClient = useQueryClient();
  const [editVisible, setEditVisible] = useState(false);

  const billsQuery = useQuery({
    queryKey: ["recurring-bills", "active"],
    queryFn: () => fetchRecurringBills(false),
    select: (data) => data.find((b) => b.id === initialBill.id) ?? initialBill,
  });
  const bill = billsQuery.data ?? initialBill;

  const accountsQuery = useQuery({
    queryKey: ["accounts", false],
    queryFn: () => fetchAccounts(false),
  });
  const accounts = accountsQuery.data ?? [];

  const isActionable = occurrence ? ACTIONABLE_STATUSES.has(occurrence.status) : false;

  const pauseMutation = useMutation({
    mutationFn: () => pauseBill(bill.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["recurring-bills"] });
      queryClient.invalidateQueries({ queryKey: ["bill-occurrences"] });
      navigation.goBack();
    },
  });

  const resumeMutation = useMutation({
    mutationFn: () => resumeBill(bill.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["recurring-bills"] });
      queryClient.invalidateQueries({ queryKey: ["bill-occurrences"] });
      navigation.goBack();
    },
  });

  const archiveMutation = useMutation({
    mutationFn: () => archiveBill(bill.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["recurring-bills"] });
      queryClient.invalidateQueries({ queryKey: ["bill-occurrences"] });
      navigation.goBack();
    },
  });

  function confirmArchive() {
    Alert.alert(
      "Archivar gasto fijo",
      `¿Archivar "${bill.name}"? Se cancelarán los pagos futuros pendientes.`,
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: "Archivar",
          style: "destructive",
          onPress: () => archiveMutation.mutate(),
        },
      ],
    );
  }

  const isBusy =
    pauseMutation.isPending || resumeMutation.isPending || archiveMutation.isPending;

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      <ScrollView contentContainerStyle={styles.content}>
        {/* ── bill header card ── */}
        <View style={styles.card}>
          <Text style={styles.billName}>{bill.name}</Text>
          {bill.provider ? (
            <Text style={styles.billProvider}>{bill.provider}</Text>
          ) : null}
          <View style={styles.metaRow}>
            <MetaChip
              icon="repeat"
              label={FREQUENCY_LABELS[bill.frequency] ?? bill.frequency}
            />
            {bill.category ? <MetaChip icon="tag" label={bill.category} /> : null}
          </View>
          <View style={styles.divider} />
          <View style={styles.amountRow}>
            <Text style={styles.amountLabel}>Monto esperado</Text>
            <Text style={styles.amountValue}>
              {fmtAmount(bill.amount_expected, bill.currency)}
            </Text>
          </View>
          {!bill.is_active && (
            <Text style={styles.pausedBadge}>PAUSADO</Text>
          )}
        </View>

        {/* ── occurrence status card ── */}
        {occurrence && (
          <View style={styles.card}>
            <Text style={styles.sectionTitle}>Próximo pago</Text>
            <View style={styles.occRow}>
              <Text style={styles.occLabel}>Vence</Text>
              <Text style={styles.occValue}>{fmtDate(occurrence.due_date)}</Text>
            </View>
            <View style={styles.occRow}>
              <Text style={styles.occLabel}>Estado</Text>
              <Text style={[styles.occValue, { color: statusColor(occurrence.status) }]}>
                {STATUS_LABELS[occurrence.status] ?? occurrence.status}
              </Text>
            </View>
            {occurrence.amount_expected !== null && (
              <View style={styles.occRow}>
                <Text style={styles.occLabel}>Monto</Text>
                <Text style={styles.occValue}>
                  {fmtAmount(occurrence.amount_expected, bill.currency)}
                </Text>
              </View>
            )}
          </View>
        )}

        {/* ── mark-paid form ── */}
        {isActionable && occurrence && (
          <MarkPaidForm
            bill={bill}
            occurrence={occurrence}
            accounts={accounts}
            onSuccess={() => navigation.goBack()}
          />
        )}

        {/* ── actions ── */}
        <View style={styles.actionsCard}>
          <Text style={styles.sectionTitle}>Acciones</Text>
          <ActionBtn
            label="Editar gasto fijo"
            icon="edit-2"
            onPress={() => setEditVisible(true)}
            disabled={isBusy}
          />
          {bill.is_active ? (
            <ActionBtn
              label="Pausar gasto fijo"
              icon="pause-circle"
              onPress={() => pauseMutation.mutate()}
              disabled={isBusy}
            />
          ) : (
            <ActionBtn
              label="Reanudar gasto fijo"
              icon="play-circle"
              onPress={() => resumeMutation.mutate()}
              disabled={isBusy}
            />
          )}
          <ActionBtn
            label="Archivar gasto fijo"
            icon="trash-2"
            onPress={confirmArchive}
            disabled={isBusy}
            destructive
          />
          {isBusy && (
            <ActivityIndicator
              color={Colors.accent}
              style={{ marginTop: Spacing.sm }}
            />
          )}
        </View>
      </ScrollView>

      <BillFormModal
        visible={editVisible}
        mode="edit"
        bill={bill}
        onClose={() => setEditVisible(false)}
        onSaved={() => {
          queryClient.invalidateQueries({ queryKey: ["recurring-bills"] });
          queryClient.invalidateQueries({ queryKey: ["bill-occurrences"] });
          queryClient.invalidateQueries({ queryKey: ["dashboard"] });
          setEditVisible(false);
        }}
      />
    </SafeAreaView>
  );
}

function MetaChip({ icon, label }: { icon: React.ComponentProps<typeof Feather>["name"]; label: string }) {
  return (
    <View style={chipStyles.chip}>
      <Feather name={icon} size={12} color={Colors.textSecondary} />
      <Text style={chipStyles.label}>{label}</Text>
    </View>
  );
}

function ActionBtn({
  label,
  icon,
  onPress,
  disabled,
  destructive = false,
}: {
  label: string;
  icon: React.ComponentProps<typeof Feather>["name"];
  onPress: () => void;
  disabled?: boolean;
  destructive?: boolean;
}) {
  return (
    <Pressable
      style={({ pressed }) => [
        actionStyles.btn,
        destructive && actionStyles.btnDestructive,
        (pressed || disabled) && actionStyles.btnPressed,
      ]}
      onPress={onPress}
      disabled={disabled}
    >
      <Feather
        name={icon}
        size={16}
        color={destructive ? Colors.overdue : Colors.textSecondary}
      />
      <Text
        style={[actionStyles.btnText, destructive && actionStyles.btnTextDestructive]}
      >
        {label}
      </Text>
    </Pressable>
  );
}

const chipStyles = StyleSheet.create({
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.xs,
    backgroundColor: Colors.bgElevated,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: Spacing.xs,
    borderWidth: 1,
    borderColor: Colors.borderLight,
  },
  label: {
    fontSize: FontSize.xs,
    color: Colors.textSecondary,
    fontWeight: "500",
  },
});

const actionStyles = StyleSheet.create({
  btn: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    paddingVertical: Spacing.sm,
    paddingHorizontal: Spacing.sm,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bg,
    minHeight: 44,
  },
  btnDestructive: {
    borderColor: Colors.overdue + "60",
    backgroundColor: Colors.expense + "08",
  },
  btnPressed: {
    opacity: 0.65,
  },
  btnText: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontWeight: "500",
  },
  btnTextDestructive: {
    color: Colors.overdue,
  },
});

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },
  content: {
    padding: Spacing.md,
    gap: Spacing.md,
  },
  card: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    gap: Spacing.sm,
    ...CardShadow,
  },
  billName: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  billProvider: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  metaRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: Spacing.xs,
  },
  divider: {
    height: 1,
    backgroundColor: Colors.borderLight,
    marginVertical: Spacing.xs,
  },
  amountRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  amountLabel: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  amountValue: {
    fontSize: FontSize.md,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  pausedBadge: {
    alignSelf: "flex-start",
    fontSize: FontSize.xs,
    fontWeight: "700",
    color: Colors.warning,
    backgroundColor: Colors.warningBg,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 2,
    letterSpacing: 0.5,
  },
  sectionTitle: {
    fontSize: FontSize.md,
    fontWeight: "700",
    color: Colors.textPrimary,
    marginBottom: Spacing.xs,
  },
  occRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: Spacing.xs,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
  },
  occLabel: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  occValue: {
    fontSize: FontSize.sm,
    fontWeight: "600",
    color: Colors.textPrimary,
  },
  actionsCard: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    gap: Spacing.sm,
    ...CardShadow,
  },
});
