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
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
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
  restoreRecurringIncome,
  resumeRecurringIncome,
  fetchRecurringIncomes,
  type RecurringIncomeResponse,
} from "../api/incomes";
import { IncomeFormModal } from "../components/IncomeFormModal";
import { SalaryCalculator } from "../components/SalaryCalculator";
import { DateField } from "../components/fields/DateField";
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
  onRestore: (id: string) => void;
  onEdit: (income: RecurringIncomeResponse) => void;
  pendingId: string | null;
}

function IncomeRow({
  income,
  onPause,
  onResume,
  onArchive,
  onRestore,
  onEdit,
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
      ) : income.archived ? (
        <View style={styles.actions}>
          <Pressable style={styles.actionBtn} onPress={() => onRestore(income.id)}>
            <Feather name="rotate-ccw" size={13} color={Colors.income} />
            <Text style={[styles.actionText, { color: Colors.income }]}>Restaurar</Text>
          </Pressable>
        </View>
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

          {/* Derived CR cycles (aguinaldo / salario escolar) are read-only —
              their amount comes from the base salary, so no manual edit. */}
          {!isDerived && (
            <Pressable style={styles.actionBtn} onPress={() => onEdit(income)}>
              <Feather name="edit-2" size={13} color={Colors.accent} />
              <Text style={[styles.actionText, { color: Colors.accent }]}>Editar</Text>
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
  const [formVisible, setFormVisible] = useState(false);
  const [formMode, setFormMode] = useState<"create" | "edit">("create");
  const [formIncome, setFormIncome] = useState<RecurringIncomeResponse | null>(null);
  const [calcVisible, setCalcVisible] = useState(false);
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

  // Hire-date prompt for CR-cycle derivation (aguinaldo/salario escolar are
  // prorated by the fecha de incorporación). Captured here, persisted on the
  // salary by the backend so a re-derive prefills it.
  const [deriveTarget, setDeriveTarget] = useState<
    { id: string; name: string } | null
  >(null);
  const [deriveHireDate, setDeriveHireDate] = useState("");

  const deriveMutation = useMutation({
    mutationFn: ({ salaryId, hireDate }: { salaryId: string; hireDate?: string | null }) =>
      deriveIncomeCycles(salaryId, hireDate),
    onSuccess: () => {
      setDeriveTarget(null);
      queryClient.invalidateQueries({ queryKey: ["recurring-incomes"] });
    },
    onError: () => {
      Alert.alert("Error", "No se pudieron crear los ciclos CR. Intenta de nuevo.");
    },
  });

  function openDerive(salary: RecurringIncomeResponse) {
    setDeriveHireDate(salary.hire_date ?? "");
    setDeriveTarget({ id: salary.id, name: salary.name });
  }

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

  async function handleRestore(id: string) {
    setPendingId(id);
    try {
      await restoreRecurringIncome(id);
      queryClient.invalidateQueries({ queryKey: ["recurring-incomes"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    } catch {
      Alert.alert("Error", "No se pudo restaurar el ingreso.");
    } finally {
      setPendingId(null);
    }
  }

  function openCreate() {
    setFormMode("create");
    setFormIncome(null);
    setFormVisible(true);
  }

  function openEdit(income: RecurringIncomeResponse) {
    setFormMode("edit");
    setFormIncome(income);
    setFormVisible(true);
  }

  function onFormSaved() {
    setFormVisible(false);
    queryClient.invalidateQueries({ queryKey: ["recurring-incomes"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
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
        <View style={styles.headerActions}>
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
          <Pressable
            style={({ pressed }) => [styles.newBtn, pressed && { opacity: 0.85 }]}
            onPress={openCreate}
          >
            <Feather name="plus" size={15} color={Colors.textOnDark} />
            <Text style={styles.newBtnText}>Nuevo</Text>
          </Pressable>
        </View>
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
            <>
              <Pressable
                style={({ pressed }) => [styles.calcBar, pressed && { opacity: 0.85 }]}
                onPress={() => setCalcVisible(true)}
              >
                <Feather name="percent" size={16} color={Colors.accent} />
                <Text style={styles.calcBarText}>Calculadora de salario neto</Text>
                <Feather name="chevron-right" size={16} color={Colors.textMuted} />
              </Pressable>
              {nudgeSalary && !showArchived ? (
                <NudgeBanner
                  salaryId={nudgeSalary.id}
                  salaryName={nudgeSalary.name}
                  onDerive={() => openDerive(nudgeSalary)}
                  isPending={deriveMutation.isPending}
                />
              ) : null}
            </>
          }
          ListEmptyComponent={
            <View style={styles.emptyCard}>
              <Feather name="trending-up" size={32} color={Colors.accentSoft} />
              <Text style={styles.emptyTitle}>Sin ingresos registrados</Text>
              <Text style={styles.emptySub}>
                Registrá tu salario por el chat o tocá "+ Nuevo". Si es salario,
                calculo el neto desde el bruto.
              </Text>
              <Pressable
                style={({ pressed }) => [styles.emptyCta, pressed && { opacity: 0.85 }]}
                onPress={openCreate}
              >
                <Feather name="plus" size={16} color={Colors.textOnDark} />
                <Text style={styles.emptyCtaText}>Nuevo ingreso</Text>
              </Pressable>
            </View>
          }
          renderItem={({ item }) => (
            <IncomeRow
              income={item}
              onPause={handlePause}
              onResume={handleResume}
              onArchive={handleArchive}
              onRestore={handleRestore}
              onEdit={openEdit}
              pendingId={pendingId}
            />
          )}
        />
      )}

      <IncomeFormModal
        visible={formVisible}
        mode={formMode}
        income={formIncome ?? undefined}
        onClose={() => setFormVisible(false)}
        onSaved={onFormSaved}
      />

      <Modal
        visible={calcVisible}
        animationType="slide"
        transparent
        onRequestClose={() => setCalcVisible(false)}
      >
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={styles.calcOverlay}
        >
          <Pressable style={styles.calcBackdrop} onPress={() => setCalcVisible(false)} />
          <View style={styles.calcSheet}>
            <View style={styles.calcHandle} />
            <View style={styles.calcSheetHeader}>
              <Text style={styles.calcSheetTitle}>Salario neto</Text>
              <Pressable onPress={() => setCalcVisible(false)} hitSlop={8}>
                <Feather name="x" size={20} color={Colors.textMuted} />
              </Pressable>
            </View>
            <ScrollView
              style={styles.calcSheetBody}
              keyboardShouldPersistTaps="handled"
              showsVerticalScrollIndicator={false}
            >
              <SalaryCalculator currency="CRC" />
            </ScrollView>
          </View>
        </KeyboardAvoidingView>
      </Modal>

      {/* ── CR-cycle hire-date prompt ── */}
      <Modal
        visible={deriveTarget != null}
        animationType="fade"
        transparent
        onRequestClose={() => setDeriveTarget(null)}
      >
        <Pressable style={styles.calcBackdrop} onPress={() => setDeriveTarget(null)} />
        <View style={styles.deriveWrap} pointerEvents="box-none">
          <View style={styles.deriveSheet}>
            <Text style={styles.deriveTitle}>Fecha de incorporación</Text>
            <Text style={styles.deriveSub}>
              ¿Cuándo empezaste en la empresa? El aguinaldo y el salario escolar
              se calculan proporcional al tiempo trabajado. Si ya tenés más de un
              año, dejalo en "Año completo".
            </Text>
            <DateField
              value={deriveHireDate}
              onChange={setDeriveHireDate}
              placeholder="Elegí la fecha"
              style={styles.deriveInput}
              maximumDate={new Date()}
            />
            <View style={styles.deriveActions}>
              <Pressable
                style={({ pressed }) => [styles.deriveGhost, pressed && { opacity: 0.7 }]}
                disabled={deriveMutation.isPending}
                onPress={() =>
                  deriveTarget &&
                  deriveMutation.mutate({ salaryId: deriveTarget.id, hireDate: null })
                }
              >
                <Text style={styles.deriveGhostText}>Año completo</Text>
              </Pressable>
              <Pressable
                style={({ pressed }) => [
                  styles.deriveBtn,
                  (deriveMutation.isPending || !deriveHireDate) && { opacity: 0.5 },
                  pressed && { opacity: 0.85 },
                ]}
                disabled={deriveMutation.isPending || !deriveHireDate}
                onPress={() =>
                  deriveTarget &&
                  deriveMutation.mutate({
                    salaryId: deriveTarget.id,
                    hireDate: deriveHireDate,
                  })
                }
              >
                {deriveMutation.isPending ? (
                  <ActivityIndicator size="small" color={Colors.bgCard} />
                ) : (
                  <Text style={styles.deriveBtnText}>Derivar</Text>
                )}
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
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
  // header actions + create
  headerActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
  },
  newBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 6,
  },
  newBtnText: {
    fontSize: FontSize.sm,
    color: Colors.textOnDark,
    fontWeight: "700",
  },
  // salary calculator entry bar
  calcBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.accentSoft,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    marginBottom: Spacing.sm,
    ...CardShadow,
  },
  calcBarText: {
    flex: 1,
    fontSize: FontSize.sm,
    fontWeight: "600",
    color: Colors.textPrimary,
  },
  // empty CTA
  emptyCta: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    marginTop: Spacing.sm,
  },
  emptyCtaText: {
    fontSize: FontSize.md,
    color: Colors.textOnDark,
    fontWeight: "700",
  },
  // standalone calculator modal
  calcOverlay: { flex: 1, justifyContent: "flex-end" },
  calcBackdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(0,0,0,0.35)" },
  deriveWrap: {
    flex: 1,
    justifyContent: "center",
    paddingHorizontal: Spacing.lg,
  },
  deriveSheet: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    padding: Spacing.lg,
    gap: Spacing.sm,
  },
  deriveTitle: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  deriveSub: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    lineHeight: 19,
  },
  deriveInput: {
    backgroundColor: Colors.bg,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    marginTop: Spacing.xs,
  },
  deriveActions: {
    flexDirection: "row",
    gap: Spacing.sm,
    marginTop: Spacing.sm,
  },
  deriveGhost: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingVertical: Spacing.sm + 2,
  },
  deriveGhostText: { color: Colors.textSecondary, fontSize: FontSize.sm, fontWeight: "600" },
  deriveBtn: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.sm + 2,
  },
  deriveBtnText: { color: Colors.bgCard, fontSize: FontSize.sm, fontWeight: "700" },
  calcSheet: {
    backgroundColor: Colors.bg,
    borderTopLeftRadius: Radius.lg,
    borderTopRightRadius: Radius.lg,
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.sm,
    paddingBottom: Spacing.xl,
    maxHeight: "90%",
  },
  calcHandle: {
    alignSelf: "center",
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: Colors.border,
    marginBottom: Spacing.sm,
  },
  calcSheetHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: Spacing.md,
  },
  calcSheetTitle: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  calcSheetBody: { paddingBottom: Spacing.md },
});
