/**
 * Phase 6f B12 — Recurring Incomes list screen.
 *
 * Shows all recurring incomes (excluding archived by default).
 * CR-cycle nudge banner: when there is at least one active CRC salary but
 * neither aguinaldo nor salario_escolar rows are present, prompts the user
 * to derive them via `POST /{salary_id}/derive-cycles` (one-tap, idempotent).
 *
 * Inline actions per row: Pausar / Reanudar / Archivar.
 * Derived income amounts are read-only (cannot be edited — backend forbids
 * sending currency or base_salary_link_id).
 */
import { useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  archiveRecurringIncome,
  deriveIncomeCycles,
  DERIVED_INCOME_TYPES,
  FREQUENCY_LABELS,
  INCOME_TYPE_LABELS,
  pauseRecurringIncome,
  resumeRecurringIncome,
  fetchRecurringIncomes,
  type RecurringIncomeResponse,
} from "../api/incomes";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";

// ── helpers ───────────────────────────────────────────────────────────────────

function fmtAmount(amount: number | null, currency: string): string {
  if (amount === null) return "—";
  const symbol = currency === "CRC" ? "₡" : currency;
  const formatted =
    currency === "CRC"
      ? amount.toLocaleString("es-CR", { maximumFractionDigits: 0 })
      : amount.toLocaleString("es-CR", { minimumFractionDigits: 2 });
  return `${symbol} ${formatted}`;
}

function fmtDate(iso: string): string {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("es-CR", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function activeSalaries(incomes: RecurringIncomeResponse[]) {
  return incomes.filter(
    (i) => i.income_type === "salary" && i.is_active && !i.archived && i.currency === "CRC"
  );
}

function hasDerivedForSalary(
  incomes: RecurringIncomeResponse[],
  salaryId: string
) {
  return incomes.some(
    (i) =>
      DERIVED_INCOME_TYPES.has(i.income_type as any) &&
      i.base_salary_link_id === salaryId
  );
}

// ── CR-cycle nudge banner ─────────────────────────────────────────────────────

interface NudgeBannerProps {
  salaryId: string;
  salaryName: string;
  onDerive: (salaryId: string) => void;
  isPending: boolean;
}

function NudgeBanner({
  salaryId,
  salaryName,
  onDerive,
  isPending,
}: NudgeBannerProps) {
  return (
    <View style={styles.nudge}>
      <Feather name="info" size={16} color={Colors.accent} style={styles.nudgeIcon} />
      <View style={styles.nudgeText}>
        <Text style={styles.nudgeTitle}>Ciclos CR faltantes</Text>
        <Text style={styles.nudgeSub}>
          Tu salario <Text style={styles.nudgeBold}>{salaryName}</Text> no tiene
          aguinaldo ni salario escolar registrados.
        </Text>
      </View>
      <Pressable
        style={({ pressed }) => [styles.nudgeBtn, pressed && { opacity: 0.7 }]}
        onPress={() => onDerive(salaryId)}
        disabled={isPending}
      >
        {isPending ? (
          <ActivityIndicator size="small" color={Colors.bgCard} />
        ) : (
          <Text style={styles.nudgeBtnText}>Derivar</Text>
        )}
      </Pressable>
    </View>
  );
}

// ── income row ────────────────────────────────────────────────────────────────

interface IncomeRowProps {
  income: RecurringIncomeResponse;
  onPause: (id: string) => void;
  onResume: (id: string) => void;
  onArchive: (id: string) => void;
  pendingId: string | null;
}

function IncomeRow({
  income,
  onPause,
  onResume,
  onArchive,
  pendingId,
}: IncomeRowProps) {
  const isLoading = pendingId === income.id;
  const isPaused = !income.is_active && !income.archived;
  const isDerived = DERIVED_INCOME_TYPES.has(income.income_type as any);

  return (
    <View
      style={[
        styles.row,
        isPaused && styles.rowPaused,
      ]}
    >
      <View style={styles.rowTop}>
        <View style={styles.rowLeft}>
          <Text style={styles.rowName} numberOfLines={1}>
            {income.name}
          </Text>
          <Text style={styles.rowType}>
            {INCOME_TYPE_LABELS[income.income_type as keyof typeof INCOME_TYPE_LABELS] ??
              income.income_type}
            {isPaused ? "  •  Pausado" : ""}
            {isDerived ? "  •  CR" : ""}
          </Text>
        </View>
        <View style={styles.rowRight}>
          <Text style={styles.rowAmount}>
            {fmtAmount(income.amount, income.currency)}
          </Text>
          <Text style={styles.rowFreq}>
            {FREQUENCY_LABELS[income.frequency as keyof typeof FREQUENCY_LABELS] ??
              income.frequency}
          </Text>
        </View>
      </View>

      <Text style={styles.rowDate}>
        Próximo pago: {fmtDate(income.next_payment_date)}
      </Text>

      {isLoading ? (
        <ActivityIndicator
          size="small"
          color={Colors.accent}
          style={styles.rowLoader}
        />
      ) : (
        <View style={styles.actions}>
          {isPaused ? (
            <Pressable
              style={styles.actionBtn}
              onPress={() => onResume(income.id)}
            >
              <Feather name="play" size={13} color={Colors.income} />
              <Text style={[styles.actionText, { color: Colors.income }]}>
                Reanudar
              </Text>
            </Pressable>
          ) : (
            <Pressable
              style={styles.actionBtn}
              onPress={() => onPause(income.id)}
            >
              <Feather name="pause" size={13} color={Colors.warning} />
              <Text style={[styles.actionText, { color: Colors.warning }]}>
                Pausar
              </Text>
            </Pressable>
          )}

          <Pressable
            style={styles.actionBtn}
            onPress={() => onArchive(income.id)}
          >
            <Feather name="archive" size={13} color={Colors.textMuted} />
            <Text style={[styles.actionText, { color: Colors.textMuted }]}>
              Archivar
            </Text>
          </Pressable>
        </View>
      )}
    </View>
  );
}

// ── main screen ───────────────────────────────────────────────────────────────

export function IncomesScreen() {
  const [showArchived, setShowArchived] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const incomesQuery = useQuery({
    queryKey: ["recurring-incomes", showArchived],
    queryFn: () => fetchRecurringIncomes(showArchived),
  });

  const incomes = incomesQuery.data ?? [];

  const nudgeSalary = useMemo(() => {
    const salaries = activeSalaries(incomes);
    return salaries.find((s) => !hasDerivedForSalary(incomes, s.id)) ?? null;
  }, [incomes]);

  const deriveMutation = useMutation({
    mutationFn: deriveIncomeCycles,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["recurring-incomes"] });
    },
    onError: () => {
      Alert.alert("Error", "No se pudieron crear los ciclos CR. Intenta de nuevo.");
    },
  });

  async function handlePause(id: string) {
    setPendingId(id);
    try {
      await pauseRecurringIncome(id);
      queryClient.invalidateQueries({ queryKey: ["recurring-incomes"] });
    } catch {
      Alert.alert("Error", "No se pudo pausar el ingreso.");
    } finally {
      setPendingId(null);
    }
  }

  async function handleResume(id: string) {
    setPendingId(id);
    try {
      await resumeRecurringIncome(id);
      queryClient.invalidateQueries({ queryKey: ["recurring-incomes"] });
    } catch {
      Alert.alert("Error", "No se pudo reanudar el ingreso.");
    } finally {
      setPendingId(null);
    }
  }

  function handleArchive(id: string) {
    Alert.alert(
      "Archivar ingreso",
      "El ingreso quedará archivado y no aparecerá en los totales activos.",
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: "Archivar",
          style: "destructive",
          onPress: async () => {
            setPendingId(id);
            try {
              await archiveRecurringIncome(id);
              queryClient.invalidateQueries({ queryKey: ["recurring-incomes"] });
              queryClient.invalidateQueries({ queryKey: ["dashboard"] });
            } catch {
              Alert.alert("Error", "No se pudo archivar el ingreso.");
            } finally {
              setPendingId(null);
            }
          },
        },
      ]
    );
  }

  async function onRefresh() {
    setRefreshing(true);
    await incomesQuery.refetch();
    setRefreshing(false);
  }

  const isLoading = incomesQuery.isLoading && !refreshing;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <View>
          <Text style={styles.headerTitle}>Ingresos recurrentes</Text>
          <Text style={styles.headerSub}>Salarios y ciclos CR</Text>
        </View>
        <Pressable
          style={({ pressed }) => [
            styles.archivedToggle,
            showArchived && styles.archivedToggleActive,
            pressed && { opacity: 0.7 },
          ]}
          onPress={() => setShowArchived((v) => !v)}
        >
          <Feather
            name="archive"
            size={14}
            color={showArchived ? Colors.accent : Colors.textMuted}
          />
          <Text
            style={[
              styles.archivedToggleText,
              showArchived && { color: Colors.accent },
            ]}
          >
            Archivados
          </Text>
        </Pressable>
      </View>

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={Colors.accent} />
        </View>
      ) : (
        <FlatList
          data={incomes}
          keyExtractor={(i) => i.id}
          contentContainerStyle={
            incomes.length === 0 ? styles.emptyContainer : styles.listContent
          }
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              tintColor={Colors.accent}
            />
          }
          ListHeaderComponent={
            nudgeSalary && !showArchived ? (
              <NudgeBanner
                salaryId={nudgeSalary.id}
                salaryName={nudgeSalary.name}
                onDerive={(id) => deriveMutation.mutate(id)}
                isPending={deriveMutation.isPending}
              />
            ) : null
          }
          ListEmptyComponent={
            <View style={styles.emptyCard}>
              <Feather name="trending-up" size={32} color={Colors.accentSoft} />
              <Text style={styles.emptyTitle}>Sin ingresos registrados</Text>
              <Text style={styles.emptySub}>
                Registra tus ingresos desde el chat o la sección de incorporación.
              </Text>
            </View>
          }
          renderItem={({ item }) => (
            <IncomeRow
              income={item}
              onPause={handlePause}
              onResume={handleResume}
              onArchive={handleArchive}
              pendingId={pendingId}
            />
          )}
        />
      )}
    </SafeAreaView>
  );
}

// ── styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.md,
    paddingBottom: Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    backgroundColor: Colors.bgCard,
  },
  headerTitle: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  headerSub: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    marginTop: 2,
  },
  archivedToggle: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 6,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  archivedToggleActive: {
    borderColor: Colors.accent,
    backgroundColor: Colors.accentBg,
  },
  archivedToggleText: {
    fontSize: FontSize.xs,
    fontWeight: "600",
    color: Colors.textMuted,
  },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  listContent: {
    padding: Spacing.md,
    gap: Spacing.sm,
  },
  emptyContainer: {
    flex: 1,
    justifyContent: "center",
    padding: Spacing.md,
  },
  emptyCard: {
    alignItems: "center",
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.xl,
    gap: Spacing.sm,
    ...CardShadow,
  },
  emptyTitle: {
    fontSize: FontSize.md,
    fontWeight: "600",
    color: Colors.textPrimary,
    marginTop: Spacing.sm,
  },
  emptySub: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    textAlign: "center",
    lineHeight: 20,
  },
  // nudge banner
  nudge: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: Colors.accentBg,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.accentSoft,
    padding: Spacing.md,
    gap: Spacing.sm,
    marginBottom: Spacing.sm,
    ...CardShadow,
  },
  nudgeIcon: {
    marginTop: 1,
  },
  nudgeText: {
    flex: 1,
    gap: 2,
  },
  nudgeTitle: {
    fontSize: FontSize.sm,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  nudgeSub: {
    fontSize: FontSize.xs,
    color: Colors.textSecondary,
    lineHeight: 16,
  },
  nudgeBold: {
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  nudgeBtn: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 6,
    minWidth: 64,
    alignItems: "center",
  },
  nudgeBtnText: {
    fontSize: FontSize.sm,
    fontWeight: "700",
    color: Colors.bgCard,
  },
  // income row
  row: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.border,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    gap: Spacing.xs,
    ...CardShadow,
  },
  rowPaused: {
    borderColor: Colors.warning,
    backgroundColor: Colors.warningBg,
  },
  rowTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
  },
  rowLeft: {
    flex: 1,
    gap: 2,
  },
  rowName: {
    fontSize: FontSize.md,
    fontWeight: "600",
    color: Colors.textPrimary,
  },
  rowType: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },
  rowRight: {
    alignItems: "flex-end",
    gap: 2,
  },
  rowAmount: {
    fontSize: FontSize.md,
    fontWeight: "700",
    color: Colors.income,
  },
  rowFreq: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },
  rowDate: {
    fontSize: FontSize.xs,
    color: Colors.textSecondary,
  },
  rowLoader: {
    marginTop: Spacing.xs,
  },
  actions: {
    flexDirection: "row",
    gap: Spacing.sm,
    marginTop: Spacing.xs,
  },
  actionBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingVertical: 4,
    paddingHorizontal: Spacing.sm,
    borderRadius: Radius.sm,
    backgroundColor: Colors.bgElevated,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  actionText: {
    fontSize: FontSize.xs,
    fontWeight: "600",
  },
});
