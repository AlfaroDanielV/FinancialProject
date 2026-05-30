/**
 * Phase 6f B13 — Goal detail screen.
 *
 * Layout:
 *   - Progress header (name, progress bar, current/target, status)
 *   - Forecast section (at this pace, X months to completion)
 *   - Contributions section (history + add-contribution form)
 *   - Actions bar (Pausar/Reanudar, Marcar cumplida, Abandonar)
 *
 * Status transitions:
 *   active   → Pausar | Marcar cumplida | Abandonar
 *   paused   → Reanudar | Abandonar
 *   achieved → (read-only)
 *   abandoned → (read-only)
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
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import {
  abandonGoal,
  addGoalContribution,
  fetchGoal,
  fetchGoalContributions,
  fetchGoalForecast,
  markGoalAchieved,
  pauseGoal,
  resumeGoal,
  STATUS_COLORS,
  STATUS_LABELS,
  type GoalResponse,
} from "../api/goals";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";
import type { MasStackParamList } from "../navigation/MasNavigator";

type Props = NativeStackScreenProps<MasStackParamList, "GoalDetail">;

// ── helpers ───────────────────────────────────────────────────────────────────

function fmtAmount(amount: number, currency: string): string {
  const symbol = currency === "CRC" ? "₡" : currency;
  const formatted =
    currency === "CRC"
      ? amount.toLocaleString("es-CR", { maximumFractionDigits: 0 })
      : amount.toLocaleString("es-CR", { minimumFractionDigits: 2 });
  return `${symbol} ${formatted}`;
}

function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleDateString("es-CR", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function progressPct(goal: GoalResponse): number {
  const target = Number(goal.target_amount);
  if (target <= 0) return 0;
  return Math.min((Number(goal.current_amount) / target) * 100, 100);
}

function isTerminal(status: string): boolean {
  return status === "achieved" || status === "abandoned";
}

// ── sub-components ────────────────────────────────────────────────────────────

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.infoRow}>
      <Text style={styles.infoLabel}>{label}</Text>
      <Text style={styles.infoValue}>{value}</Text>
    </View>
  );
}

function Section({
  title,
  children,
  defaultOpen = true,
}: {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <View style={styles.section}>
      <Pressable
        style={styles.sectionHeader}
        onPress={() => setOpen((v) => !v)}
      >
        <Text style={styles.sectionTitle}>{title}</Text>
        <Feather
          name={open ? "chevron-up" : "chevron-down"}
          size={16}
          color={Colors.textMuted}
        />
      </Pressable>
      {open ? <View style={styles.sectionBody}>{children}</View> : null}
    </View>
  );
}

// ── add contribution form ─────────────────────────────────────────────────────

function AddContributionForm({
  goalId,
  currency,
  onDone,
}: {
  goalId: string;
  currency: string;
  onDone: () => void;
}) {
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () =>
      addGoalContribution(goalId, { amount: parseFloat(amount) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["goal", goalId] });
      queryClient.invalidateQueries({
        queryKey: ["goal-contributions", goalId],
      });
      queryClient.invalidateQueries({ queryKey: ["goal-forecast", goalId] });
      queryClient.invalidateQueries({ queryKey: ["goals"] });
      setAmount("");
      setError("");
      onDone();
    },
    onError: () => setError("No se pudo registrar el aporte."),
  });

  function submit() {
    const parsed = parseFloat(amount);
    if (isNaN(parsed) || parsed <= 0) {
      setError("Ingresa un monto válido mayor a 0.");
      return;
    }
    setError("");
    mutation.mutate();
  }

  return (
    <View style={styles.addForm}>
      <Text style={styles.addFormTitle}>Registrar aporte</Text>
      <View style={styles.addFormRow}>
        <Text style={styles.addFormSymbol}>
          {currency === "CRC" ? "₡" : currency}
        </Text>
        <TextInput
          style={styles.addFormInput}
          value={amount}
          onChangeText={setAmount}
          keyboardType="numeric"
          placeholder="0"
          placeholderTextColor={Colors.textMuted}
          returnKeyType="done"
          onSubmitEditing={submit}
        />
        <Pressable
          style={({ pressed }) => [
            styles.addFormBtn,
            pressed && { opacity: 0.7 },
            mutation.isPending && { opacity: 0.6 },
          ]}
          onPress={submit}
          disabled={mutation.isPending}
        >
          {mutation.isPending ? (
            <ActivityIndicator size="small" color={Colors.textOnDark} />
          ) : (
            <Text style={styles.addFormBtnText}>Aportar</Text>
          )}
        </Pressable>
      </View>
      {error ? <Text style={styles.addFormError}>{error}</Text> : null}
    </View>
  );
}

// ── main screen ───────────────────────────────────────────────────────────────

export function GoalDetailScreen({ route, navigation }: Props) {
  const { goalId } = route.params;
  const queryClient = useQueryClient();
  const [showAddForm, setShowAddForm] = useState(false);

  const goalQuery = useQuery({
    queryKey: ["goal", goalId],
    queryFn: () => fetchGoal(goalId),
  });

  const contributionsQuery = useQuery({
    queryKey: ["goal-contributions", goalId],
    queryFn: () => fetchGoalContributions(goalId),
  });

  const forecastQuery = useQuery({
    queryKey: ["goal-forecast", goalId],
    queryFn: () => fetchGoalForecast(goalId),
  });

  const statusMutation = useMutation({
    onSuccess: (updated) => {
      queryClient.setQueryData(["goal", goalId], updated);
      queryClient.invalidateQueries({ queryKey: ["goals"] });
    },
    onError: () => Alert.alert("Error", "No se pudo actualizar el estado."),
    mutationFn: (_fn: () => Promise<GoalResponse>) => _fn(),
  });

  function runStatus(fn: () => Promise<GoalResponse>) {
    statusMutation.mutate(fn);
  }

  function confirmAbandon() {
    Alert.alert(
      "Abandonar meta",
      "Esta acción es permanente. La meta quedará como abandonada.",
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: "Abandonar",
          style: "destructive",
          onPress: () => runStatus(() => abandonGoal(goalId)),
        },
      ]
    );
  }

  if (goalQuery.isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={Colors.accent} />
      </View>
    );
  }

  if (!goalQuery.data) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>Meta no encontrada.</Text>
      </View>
    );
  }

  const goal = goalQuery.data;
  const pct = progressPct(goal);
  const statusColor = STATUS_COLORS[goal.status] ?? Colors.textMuted;
  const contributions = contributionsQuery.data ?? [];
  const forecast = forecastQuery.data;
  const terminal = isTerminal(goal.status);

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      <ScrollView contentContainerStyle={styles.scroll}>

        {/* ── progress header ── */}
        <View style={styles.heroCard}>
          <View style={styles.heroTop}>
            <Text style={styles.heroName}>{goal.name}</Text>
            <View style={[styles.statusBadge, { borderColor: statusColor }]}>
              <Text style={[styles.statusText, { color: statusColor }]}>
                {STATUS_LABELS[goal.status] ?? goal.status}
              </Text>
            </View>
          </View>

          <View style={styles.heroProgress}>
            <View style={styles.progressTrack}>
              <View
                style={[
                  styles.progressFill,
                  {
                    width: `${pct}%` as any,
                    backgroundColor:
                      goal.status === "achieved" ? Colors.income : Colors.accent,
                  },
                ]}
              />
            </View>
            <Text style={styles.heroPct}>{pct.toFixed(1)}%</Text>
          </View>

          <View style={styles.heroAmounts}>
            <View>
              <Text style={styles.heroLabel}>Ahorrado</Text>
              <Text style={styles.heroCurrent}>
                {fmtAmount(Number(goal.current_amount), goal.target_currency)}
              </Text>
            </View>
            <View style={styles.heroSep} />
            <View style={{ alignItems: "flex-end" }}>
              <Text style={styles.heroLabel}>Meta</Text>
              <Text style={styles.heroTarget}>
                {fmtAmount(Number(goal.target_amount), goal.target_currency)}
              </Text>
            </View>
          </View>
        </View>

        {/* ── info section ── */}
        <Section title="Información" defaultOpen>
          {goal.target_date ? (
            <InfoRow
              label="Fecha objetivo"
              value={new Date(
                goal.target_date + "T12:00:00"
              ).toLocaleDateString("es-CR", {
                day: "numeric",
                month: "long",
                year: "numeric",
              })}
            />
          ) : null}
          {goal.monthly_contribution ? (
            <InfoRow
              label="Aporte mensual"
              value={fmtAmount(
                Number(goal.monthly_contribution),
                goal.target_currency
              )}
            />
          ) : null}
          <InfoRow
            label="Prioridad"
            value={`${goal.priority} / 5`}
          />
          <InfoRow
            label="Moneda"
            value={goal.target_currency}
          />
        </Section>

        {/* ── forecast section ── */}
        {forecast && !terminal ? (
          <Section title="Pronóstico" defaultOpen>
            {forecast.has_enough_data ? (
              <>
                <InfoRow
                  label="Aporte promedio / mes"
                  value={fmtAmount(
                    Number(forecast.avg_monthly_contribution),
                    goal.target_currency
                  )}
                />
                {forecast.months_to_target !== null ? (
                  <InfoRow
                    label="Meses para completar"
                    value={String(forecast.months_to_target)}
                  />
                ) : null}
                {forecast.projected_completion_date ? (
                  <InfoRow
                    label="Fecha proyectada"
                    value={new Date(
                      forecast.projected_completion_date + "T12:00:00"
                    ).toLocaleDateString("es-CR", {
                      month: "long",
                      year: "numeric",
                    })}
                  />
                ) : null}
              </>
            ) : (
              <Text style={styles.noDataText}>
                Registra aportes durante al menos un mes para ver el pronóstico.
              </Text>
            )}
          </Section>
        ) : null}

        {/* ── contributions section ── */}
        <Section title={`Aportes (${contributions.length})`} defaultOpen={false}>
          {!terminal ? (
            showAddForm ? (
              <AddContributionForm
                goalId={goalId}
                currency={goal.target_currency}
                onDone={() => setShowAddForm(false)}
              />
            ) : (
              <Pressable
                style={({ pressed }) => [
                  styles.addBtn,
                  pressed && { opacity: 0.7 },
                ]}
                onPress={() => setShowAddForm(true)}
              >
                <Feather name="plus" size={14} color={Colors.accent} />
                <Text style={styles.addBtnText}>Registrar aporte</Text>
              </Pressable>
            )
          ) : null}

          {contributions.length === 0 ? (
            <Text style={styles.noDataText}>Sin aportes registrados.</Text>
          ) : (
            contributions.slice(0, 20).map((c) => (
              <View key={c.id} style={styles.contribRow}>
                <Text style={styles.contribDate}>
                  {fmtDateTime(c.occurred_at)}
                </Text>
                <Text style={styles.contribAmount}>
                  +{fmtAmount(Number(c.amount), goal.target_currency)}
                </Text>
              </View>
            ))
          )}
          {contributions.length > 20 ? (
            <Text style={styles.noDataText}>
              Mostrando los últimos 20 de {contributions.length} aportes.
            </Text>
          ) : null}
        </Section>

      </ScrollView>

      {/* ── actions bar ── */}
      {!terminal && (
        <View style={styles.actionsBar}>
          {statusMutation.isPending ? (
            <ActivityIndicator color={Colors.accent} />
          ) : (
            <>
              {goal.status === "active" ? (
                <Pressable
                  style={styles.actionBtn}
                  onPress={() => runStatus(() => pauseGoal(goalId))}
                >
                  <Feather name="pause" size={14} color={Colors.warning} />
                  <Text style={[styles.actionText, { color: Colors.warning }]}>
                    Pausar
                  </Text>
                </Pressable>
              ) : (
                <Pressable
                  style={styles.actionBtn}
                  onPress={() => runStatus(() => resumeGoal(goalId))}
                >
                  <Feather name="play" size={14} color={Colors.income} />
                  <Text style={[styles.actionText, { color: Colors.income }]}>
                    Reanudar
                  </Text>
                </Pressable>
              )}

              {goal.status === "active" ? (
                <Pressable
                  style={[styles.actionBtn, styles.actionPrimary]}
                  onPress={() => runStatus(() => markGoalAchieved(goalId))}
                >
                  <Feather name="check-circle" size={14} color={Colors.textOnDark} />
                  <Text style={[styles.actionText, { color: Colors.textOnDark }]}>
                    Cumplida
                  </Text>
                </Pressable>
              ) : null}

              <Pressable style={styles.actionBtn} onPress={confirmAbandon}>
                <Feather name="x-circle" size={14} color={Colors.textMuted} />
                <Text style={[styles.actionText, { color: Colors.textMuted }]}>
                  Abandonar
                </Text>
              </Pressable>
            </>
          )}
        </View>
      )}
    </SafeAreaView>
  );
}

// ── styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Colors.bg },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  errorText: { color: Colors.textSecondary, fontSize: FontSize.md },
  scroll: { padding: Spacing.md, gap: Spacing.md, paddingBottom: 80 },

  // hero card
  heroCard: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    gap: Spacing.sm,
    ...CardShadow,
  },
  heroTop: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: Spacing.sm,
  },
  heroName: {
    flex: 1,
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  statusBadge: {
    borderWidth: 1,
    borderRadius: Radius.sm,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  statusText: { fontSize: FontSize.xs, fontWeight: "700" },
  heroProgress: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
  },
  progressTrack: {
    flex: 1,
    height: 8,
    backgroundColor: Colors.bgElevated,
    borderRadius: 4,
    overflow: "hidden",
  },
  progressFill: { height: "100%", borderRadius: 4, minWidth: 4 },
  heroPct: {
    fontSize: FontSize.sm,
    fontWeight: "700",
    color: Colors.textSecondary,
    width: 44,
    textAlign: "right",
  },
  heroAmounts: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  heroSep: { flex: 1 },
  heroLabel: { fontSize: FontSize.xs, color: Colors.textMuted, marginBottom: 2 },
  heroCurrent: { fontSize: FontSize.md, fontWeight: "700", color: Colors.income },
  heroTarget: { fontSize: FontSize.md, fontWeight: "600", color: Colors.textSecondary },

  // section
  section: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    overflow: "hidden",
    ...CardShadow,
  },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
  },
  sectionTitle: {
    fontSize: FontSize.sm,
    fontWeight: "700",
    color: Colors.textPrimary,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  sectionBody: { padding: Spacing.md, gap: Spacing.xs },

  // info row
  infoRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingVertical: 4,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
  },
  infoLabel: { fontSize: FontSize.sm, color: Colors.textSecondary },
  infoValue: { fontSize: FontSize.sm, fontWeight: "600", color: Colors.textPrimary },

  // no data
  noDataText: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    textAlign: "center",
    paddingVertical: Spacing.sm,
  },

  // contributions
  contribRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    paddingVertical: 5,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
  },
  contribDate: { fontSize: FontSize.sm, color: Colors.textSecondary },
  contribAmount: { fontSize: FontSize.sm, fontWeight: "700", color: Colors.income },

  // add contribution
  addBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingVertical: Spacing.sm,
    paddingHorizontal: Spacing.sm,
    borderRadius: Radius.sm,
    backgroundColor: Colors.accentBg,
    borderWidth: 1,
    borderColor: Colors.accentSoft,
    alignSelf: "flex-start",
    marginBottom: Spacing.sm,
  },
  addBtnText: { fontSize: FontSize.sm, fontWeight: "600", color: Colors.accent },
  addForm: {
    gap: Spacing.sm,
    marginBottom: Spacing.sm,
  },
  addFormTitle: {
    fontSize: FontSize.sm,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  addFormRow: { flexDirection: "row", alignItems: "center", gap: Spacing.sm },
  addFormSymbol: {
    fontSize: FontSize.md,
    fontWeight: "700",
    color: Colors.textSecondary,
  },
  addFormInput: {
    flex: 1,
    backgroundColor: Colors.bgInput,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 7,
    fontSize: FontSize.md,
    color: Colors.textPrimary,
  },
  addFormBtn: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.md,
    paddingVertical: 8,
    minWidth: 80,
    alignItems: "center",
  },
  addFormBtnText: {
    fontSize: FontSize.sm,
    fontWeight: "700",
    color: Colors.textOnDark,
  },
  addFormError: { fontSize: FontSize.xs, color: Colors.expense },

  // actions bar
  actionsBar: {
    position: "absolute",
    bottom: 0,
    left: 0,
    right: 0,
    flexDirection: "row",
    backgroundColor: Colors.bgCard,
    borderTopWidth: 1,
    borderTopColor: Colors.border,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    gap: Spacing.sm,
    alignItems: "center",
    justifyContent: "center",
  },
  actionBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    paddingHorizontal: Spacing.md,
    paddingVertical: 8,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bgElevated,
  },
  actionPrimary: {
    backgroundColor: Colors.accent,
    borderColor: Colors.accent,
  },
  actionText: {
    fontSize: FontSize.sm,
    fontWeight: "600",
  },
});
