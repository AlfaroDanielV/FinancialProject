/**
 * Phase 6f B11 — Debt detail screen.
 *
 * Three sections (expandable on tap):
 *   1. Info card — name, type, lender, balance, rate, monthly payment, next payment.
 *   2. Amortización — schedule (first 24 months shown; "ver más" note if longer).
 *   3. Cancelación anticipada — lump-sum and/or extra-monthly calculator.
 * Bottom: archive action with confirm.
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
  archiveDebt,
  DEBT_TYPE_LABELS,
  fetchDebt,
  fetchDebtSchedule,
  fetchPayoffScenarios,
  type AmortizationRow,
  type PayoffScenariosResponse,
} from "../api/debts";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";
import type { MasStackParamList } from "../navigation/MasNavigator";

// ── helpers ───────────────────────────────────────────────────────────────────

function fmt(value: number, currency: string): string {
  const symbol = currency === "CRC" ? "₡" : currency;
  const abs = Math.abs(value);
  const formatted =
    currency === "CRC"
      ? abs.toLocaleString("es-CR", { maximumFractionDigits: 0 })
      : abs.toLocaleString("es-CR", { minimumFractionDigits: 2 });
  return `${symbol} ${formatted}`;
}

function fmtPct(rate: number): string {
  return `${(rate * 100).toFixed(2)}%`;
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("es-CR", { day: "numeric", month: "short", year: "numeric" });
}

function nextPaymentLabel(dueDay: number): string {
  const today = new Date();
  const candidate = new Date(today.getFullYear(), today.getMonth(), dueDay);
  if (candidate < today) candidate.setMonth(candidate.getMonth() + 1);
  return candidate.toLocaleDateString("es-CR", { day: "numeric", month: "long", year: "numeric" });
}

// ── expandable section ────────────────────────────────────────────────────────

function Section({
  title,
  defaultOpen = false,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <View style={sectionStyles.container}>
      <Pressable
        style={({ pressed }) => [
          sectionStyles.header,
          pressed && sectionStyles.headerPressed,
        ]}
        onPress={() => setOpen((v) => !v)}
      >
        <Text style={sectionStyles.title}>{title}</Text>
        <Feather
          name={open ? "chevron-up" : "chevron-down"}
          size={18}
          color={Colors.textMuted}
        />
      </Pressable>
      {open && <View style={sectionStyles.body}>{children}</View>}
    </View>
  );
}

const sectionStyles = StyleSheet.create({
  container: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    overflow: "hidden",
    ...CardShadow,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.md - 2,
  },
  headerPressed: {
    backgroundColor: Colors.bgElevated,
  },
  title: {
    fontSize: FontSize.md,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  body: {
    borderTopWidth: 1,
    borderTopColor: Colors.borderLight,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
  },
});

// ── amortization table ────────────────────────────────────────────────────────

const SCHEDULE_DISPLAY_LIMIT = 24;

function AmortizationTable({
  rows,
  totalMonths,
  currency,
}: {
  rows: AmortizationRow[];
  totalMonths: number;
  currency: string;
}) {
  const displayed = rows.slice(0, SCHEDULE_DISPLAY_LIMIT);
  return (
    <View style={amortStyles.container}>
      <View style={amortStyles.headerRow}>
        {["#", "Fecha", "Cuota", "Capital", "Interés", "Saldo"].map((h) => (
          <Text key={h} style={amortStyles.colHeader}>
            {h}
          </Text>
        ))}
      </View>
      {displayed.map((row) => (
        <View
          key={row.month_number}
          style={[
            amortStyles.dataRow,
            row.month_number % 2 === 0 && amortStyles.dataRowAlt,
          ]}
        >
          <Text style={amortStyles.cell}>{row.month_number}</Text>
          <Text style={amortStyles.cell}>{fmtDate(row.payment_date)}</Text>
          <Text style={amortStyles.cell}>{fmt(row.payment_amount, currency)}</Text>
          <Text style={amortStyles.cell}>{fmt(row.principal_portion, currency)}</Text>
          <Text style={[amortStyles.cell, { color: Colors.warning }]}>
            {fmt(row.interest_portion, currency)}
          </Text>
          <Text style={amortStyles.cell}>{fmt(row.remaining_balance, currency)}</Text>
        </View>
      ))}
      {totalMonths > SCHEDULE_DISPLAY_LIMIT && (
        <Text style={amortStyles.truncNote}>
          Se muestran los primeros {SCHEDULE_DISPLAY_LIMIT} meses de {totalMonths} en total.
        </Text>
      )}
    </View>
  );
}

const amortStyles = StyleSheet.create({
  container: {
    gap: 1,
  },
  headerRow: {
    flexDirection: "row",
    paddingVertical: Spacing.xs,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    marginBottom: Spacing.xs,
  },
  colHeader: {
    flex: 1,
    fontSize: FontSize.xs,
    fontWeight: "700",
    color: Colors.textMuted,
    textAlign: "right",
  },
  dataRow: {
    flexDirection: "row",
    paddingVertical: Spacing.xs,
  },
  dataRowAlt: {
    backgroundColor: Colors.bgElevated,
  },
  cell: {
    flex: 1,
    fontSize: 9,
    color: Colors.textPrimary,
    textAlign: "right",
  },
  truncNote: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    textAlign: "center",
    marginTop: Spacing.sm,
    fontStyle: "italic",
  },
});

// ── payoff calculator ─────────────────────────────────────────────────────────

function PayoffCalculator({ debtId, currency }: { debtId: string; currency: string }) {
  const [lumpSum, setLumpSum] = useState("");
  const [extraMonthly, setExtraMonthly] = useState("");
  const [result, setResult] = useState<PayoffScenariosResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function calculate() {
    const ls = parseFloat(lumpSum);
    const em = parseFloat(extraMonthly);
    if (!ls && !em) {
      setError("Ingresá un pago único o un monto mensual adicional.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await fetchPayoffScenarios(debtId, {
        ...(ls > 0 ? { lump_sum: ls } : {}),
        ...(em > 0 ? { extra_monthly: em } : {}),
      });
      setResult(data);
    } catch {
      setError("No se pudo calcular. Verificá los valores.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <View style={calcStyles.container}>
      <View style={calcStyles.field}>
        <Text style={calcStyles.label}>Pago único ({currency})</Text>
        <TextInput
          style={calcStyles.input}
          value={lumpSum}
          onChangeText={setLumpSum}
          keyboardType="numeric"
          placeholder="0"
          placeholderTextColor={Colors.textMuted}
        />
      </View>
      <View style={calcStyles.field}>
        <Text style={calcStyles.label}>Extra mensual ({currency})</Text>
        <TextInput
          style={calcStyles.input}
          value={extraMonthly}
          onChangeText={setExtraMonthly}
          keyboardType="numeric"
          placeholder="0"
          placeholderTextColor={Colors.textMuted}
        />
      </View>
      {error && <Text style={calcStyles.errorText}>{error}</Text>}
      <Pressable
        style={({ pressed }) => [
          calcStyles.btn,
          (loading || pressed) && calcStyles.btnDisabled,
        ]}
        onPress={calculate}
        disabled={loading}
      >
        {loading ? (
          <ActivityIndicator color={Colors.textOnDark} size="small" />
        ) : (
          <Text style={calcStyles.btnText}>Calcular ahorro</Text>
        )}
      </Pressable>

      {result && (
        <View style={calcStyles.results}>
          <ResultRow label="Plazo original" value={`${result.original.total_months} meses`} />
          <ResultRow
            label="Interés total original"
            value={fmt(result.original.total_interest, result.currency)}
            color={Colors.warning}
          />
          {result.lump_sum && (
            <>
              <View style={calcStyles.divider} />
              <Text style={calcStyles.scenarioTitle}>Con pago único</Text>
              <ResultRow label="Meses ahorrados" value={`${result.lump_sum.months_saved}`} color={Colors.income} />
              <ResultRow
                label="Interés ahorrado"
                value={fmt(result.lump_sum.interest_saved, result.currency)}
                color={Colors.income}
              />
              {result.lump_sum.prepayment_penalty_applies && (
                <ResultRow
                  label="Penalidad Ley 7472"
                  value={fmt(result.lump_sum.prepayment_penalty_amount, result.currency)}
                  color={Colors.overdue}
                />
              )}
            </>
          )}
          {result.extra_monthly && (
            <>
              <View style={calcStyles.divider} />
              <Text style={calcStyles.scenarioTitle}>Con cuota extra mensual</Text>
              <ResultRow label="Meses ahorrados" value={`${result.extra_monthly.months_saved}`} color={Colors.income} />
              <ResultRow
                label="Interés ahorrado"
                value={fmt(result.extra_monthly.interest_saved, result.currency)}
                color={Colors.income}
              />
            </>
          )}
          {result.variable_rate_notice && (
            <Text style={calcStyles.rateNotice}>{result.variable_rate_notice}</Text>
          )}
        </View>
      )}
    </View>
  );
}

function ResultRow({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <View style={calcStyles.resultRow}>
      <Text style={calcStyles.resultLabel}>{label}</Text>
      <Text style={[calcStyles.resultValue, color ? { color } : null]}>{value}</Text>
    </View>
  );
}

const calcStyles = StyleSheet.create({
  container: {
    gap: Spacing.sm,
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
  errorText: {
    fontSize: FontSize.sm,
    color: Colors.overdue,
  },
  btn: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.sm,
    alignItems: "center",
    justifyContent: "center",
    minHeight: 44,
  },
  btnDisabled: {
    opacity: 0.65,
  },
  btnText: {
    fontSize: FontSize.md,
    fontWeight: "700",
    color: Colors.textOnDark,
  },
  results: {
    marginTop: Spacing.sm,
    gap: Spacing.xs,
  },
  scenarioTitle: {
    fontSize: FontSize.sm,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  resultRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 3,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
  },
  resultLabel: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  resultValue: {
    fontSize: FontSize.sm,
    fontWeight: "600",
    color: Colors.textPrimary,
  },
  divider: {
    height: 1,
    backgroundColor: Colors.border,
    marginVertical: Spacing.xs,
  },
  rateNotice: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontStyle: "italic",
    marginTop: Spacing.xs,
  },
});

// ── main screen ───────────────────────────────────────────────────────────────

type Props = {
  navigation: NativeStackNavigationProp<MasStackParamList, "DebtDetail">;
  route: RouteProp<MasStackParamList, "DebtDetail">;
};

export function DebtDetailScreen({ navigation, route }: Props) {
  const { debtId } = route.params;
  const queryClient = useQueryClient();

  const debtQuery = useQuery({
    queryKey: ["debts", debtId],
    queryFn: () => fetchDebt(debtId),
  });

  const scheduleQuery = useQuery({
    queryKey: ["debts", debtId, "schedule"],
    queryFn: () => fetchDebtSchedule(debtId),
    enabled: false,
  });

  const debt = debtQuery.data;

  const archiveMutation = useMutation({
    mutationFn: () => archiveDebt(debtId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["debts"] });
      navigation.goBack();
    },
  });

  function confirmArchive() {
    if (!debt) return;
    Alert.alert(
      "Archivar deuda",
      `¿Archivar "${debt.name}"? Esta acción no puede deshacerse desde la app.`,
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

  if (debtQuery.isLoading || !debt) {
    return (
      <SafeAreaView style={styles.safe} edges={["bottom"]}>
        <View style={styles.center}>
          <ActivityIndicator color={Colors.accent} />
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      <ScrollView contentContainerStyle={styles.content}>
        {/* ── info card ── */}
        <View style={styles.card}>
          <Text style={styles.debtName}>{debt.name}</Text>
          <Text style={styles.debtType}>
            {DEBT_TYPE_LABELS[debt.debt_type] ?? debt.debt_type}
            {debt.lender ? ` · ${debt.lender}` : ""}
          </Text>
          <View style={styles.divider} />
          <InfoRow label="Saldo actual" value={fmt(debt.current_balance, debt.currency)} bold />
          <InfoRow label="Cuota mínima" value={`${fmt(debt.minimum_payment, debt.currency)}/mes`} />
          <InfoRow label="Tasa de interés" value={`${fmtPct(debt.interest_rate)} ${debt.rate_type === "variable" ? "(variable)" : "(fija)"}`} />
          {debt.term_months && (
            <InfoRow label="Plazo" value={`${debt.term_months} meses`} />
          )}
          <InfoRow label="Próximo pago" value={nextPaymentLabel(debt.payment_due_day)} />
          {debt.maturity_date && (
            <InfoRow label="Vencimiento" value={fmtDate(debt.maturity_date)} />
          )}
          {debt.notes ? (
            <InfoRow label="Notas" value={debt.notes} />
          ) : null}
          {!debt.is_active && (
            <Text style={styles.pausedBadge}>PAUSADA</Text>
          )}
        </View>

        {/* ── amortization schedule ── */}
        <Section
          title="Tabla de amortización"
          defaultOpen={false}
        >
          {scheduleQuery.data ? (
            <>
              <View style={styles.schedSummary}>
                <InfoRow
                  label="Total intereses"
                  value={fmt(scheduleQuery.data.total_interest, debt.currency)}
                  color={Colors.warning}
                />
                <InfoRow
                  label="Fecha de cancelación"
                  value={fmtDate(scheduleQuery.data.payoff_date)}
                />
              </View>
              <AmortizationTable
                rows={scheduleQuery.data.schedule}
                totalMonths={scheduleQuery.data.total_months}
                currency={debt.currency}
              />
            </>
          ) : scheduleQuery.isFetching ? (
            <ActivityIndicator color={Colors.accent} style={{ margin: Spacing.md }} />
          ) : (
            <Pressable
              style={({ pressed }) => [
                styles.loadBtn,
                pressed && styles.loadBtnPressed,
              ]}
              onPress={() => scheduleQuery.refetch()}
            >
              <Text style={styles.loadBtnText}>Cargar tabla de amortización</Text>
            </Pressable>
          )}
        </Section>

        {/* ── payoff calculator ── */}
        <Section title="Cancelación anticipada">
          <PayoffCalculator debtId={debtId} currency={debt.currency} />
        </Section>

        {/* ── archive action ── */}
        <View style={styles.actionsCard}>
          <Pressable
            style={({ pressed }) => [
              styles.archiveBtn,
              (archiveMutation.isPending || pressed) && styles.archiveBtnDisabled,
            ]}
            onPress={confirmArchive}
            disabled={archiveMutation.isPending}
          >
            {archiveMutation.isPending ? (
              <ActivityIndicator color={Colors.overdue} size="small" />
            ) : (
              <>
                <Feather name="trash-2" size={16} color={Colors.overdue} />
                <Text style={styles.archiveBtnText}>Archivar deuda</Text>
              </>
            )}
          </Pressable>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

function InfoRow({
  label,
  value,
  bold = false,
  color,
}: {
  label: string;
  value: string;
  bold?: boolean;
  color?: string;
}) {
  return (
    <View style={infoStyles.row}>
      <Text style={infoStyles.label}>{label}</Text>
      <Text
        style={[
          infoStyles.value,
          bold && infoStyles.valueBold,
          color ? { color } : null,
        ]}
      >
        {value}
      </Text>
    </View>
  );
}

const infoStyles = StyleSheet.create({
  row: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: Spacing.xs,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
  },
  label: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    flex: 1,
  },
  value: {
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
    fontWeight: "400",
    flexShrink: 1,
    textAlign: "right",
    paddingLeft: Spacing.sm,
  },
  valueBold: {
    fontWeight: "700",
    fontSize: FontSize.md,
  },
});

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
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
    gap: Spacing.xs,
    ...CardShadow,
  },
  debtName: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  debtType: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    marginBottom: Spacing.xs,
  },
  divider: {
    height: 1,
    backgroundColor: Colors.borderLight,
    marginVertical: Spacing.xs,
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
    marginTop: Spacing.xs,
  },
  schedSummary: {
    marginBottom: Spacing.sm,
  },
  loadBtn: {
    backgroundColor: Colors.accentBg,
    borderRadius: Radius.md,
    paddingVertical: Spacing.sm,
    alignItems: "center",
    borderWidth: 1,
    borderColor: Colors.accentSoft,
    minHeight: 44,
    justifyContent: "center",
  },
  loadBtnPressed: {
    opacity: 0.72,
  },
  loadBtnText: {
    fontSize: FontSize.sm,
    fontWeight: "600",
    color: Colors.accent,
  },
  actionsCard: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    ...CardShadow,
  },
  archiveBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    paddingVertical: Spacing.sm,
    paddingHorizontal: Spacing.sm,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.overdue + "60",
    backgroundColor: Colors.expense + "08",
    minHeight: 44,
    justifyContent: "center",
  },
  archiveBtnDisabled: {
    opacity: 0.6,
  },
  archiveBtnText: {
    fontSize: FontSize.sm,
    color: Colors.overdue,
    fontWeight: "600",
  },
});
