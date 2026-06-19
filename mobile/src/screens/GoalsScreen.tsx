/**
 * Phase 6f B13 — Goals list screen.
 *
 * Status filter pills: Todas / Activas / Pausadas / Cumplidas / Abandonadas.
 * Each row shows a proportional progress bar and current/target amounts.
 * Tap to navigate to GoalDetailScreen.
 */
import { useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";

import {
  fetchGoals,
  STATUS_COLORS,
  STATUS_LABELS,
  type GoalResponse,
} from "../api/goals";
import { GoalFormModal } from "../components/GoalFormModal";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";
import type { MasStackParamList } from "../navigation/MasNavigator";

// ── helpers ───────────────────────────────────────────────────────────────────

function fmtAmount(amount: number, currency: string): string {
  const symbol = currency === "CRC" ? "₡" : currency;
  const formatted =
    currency === "CRC"
      ? amount.toLocaleString("es-CR", { maximumFractionDigits: 0 })
      : amount.toLocaleString("es-CR", { minimumFractionDigits: 2 });
  return `${symbol} ${formatted}`;
}

function progressPct(goal: GoalResponse): number {
  const target = Number(goal.target_amount);
  if (target <= 0) return 0;
  return Math.min((Number(goal.current_amount) / target) * 100, 100);
}

// ── status filter pills ───────────────────────────────────────────────────────

const FILTER_OPTIONS = [
  { label: "Todas", value: "" },
  { label: "Activas", value: "active" },
  { label: "Pausadas", value: "paused" },
  { label: "Cumplidas", value: "completed" },
  { label: "Abandonadas", value: "abandoned" },
];

// ── goal row ─────────────────────────────────────────────────────────────────

function GoalRow({
  goal,
  onPress,
}: {
  goal: GoalResponse;
  onPress: () => void;
}) {
  const pct = progressPct(goal);
  const statusColor =
    STATUS_COLORS[goal.status] ?? Colors.textMuted;

  return (
    <Pressable
      style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
      onPress={onPress}
    >
      <View style={styles.rowHeader}>
        <Text style={styles.rowName} numberOfLines={1}>
          {goal.name}
        </Text>
        <View style={[styles.statusBadge, { borderColor: statusColor }]}>
          <Text style={[styles.statusText, { color: statusColor }]}>
            {STATUS_LABELS[goal.status] ?? goal.status}
          </Text>
        </View>
      </View>

      {/* progress bar */}
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

      <View style={styles.rowFooter}>
        <Text style={styles.rowCurrent}>
          {fmtAmount(Number(goal.current_amount), goal.target_currency)}
        </Text>
        <Text style={styles.rowPct}>{pct.toFixed(0)}%</Text>
        <Text style={styles.rowTarget}>
          {fmtAmount(Number(goal.target_amount), goal.target_currency)}
        </Text>
      </View>

      {goal.target_date ? (
        <Text style={styles.rowDate}>
          Meta:{" "}
          {new Date(goal.target_date + "T12:00:00").toLocaleDateString(
            "es-CR",
            { day: "numeric", month: "short", year: "numeric" }
          )}
        </Text>
      ) : null}

      <Feather
        name="chevron-right"
        size={16}
        color={Colors.textMuted}
        style={styles.rowChevron}
      />
    </Pressable>
  );
}

// ── main screen ───────────────────────────────────────────────────────────────

type Props = {
  navigation: NativeStackNavigationProp<MasStackParamList, "GoalsList">;
};

export function GoalsScreen({ navigation }: Props) {
  const [statusFilter, setStatusFilter] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [createVisible, setCreateVisible] = useState(false);

  const goalsQuery = useQuery({
    queryKey: ["goals", statusFilter],
    queryFn: () => fetchGoals(statusFilter || undefined),
  });

  const goals = goalsQuery.data ?? [];
  const isLoading = goalsQuery.isLoading && !refreshing;

  async function onRefresh() {
    setRefreshing(true);
    await goalsQuery.refetch();
    setRefreshing(false);
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={styles.headerTitle}>Metas financieras</Text>
          <Text style={styles.headerSub}>Ahorros y seguimiento</Text>
        </View>
        <Pressable
          onPress={() => setCreateVisible(true)}
          style={({ pressed }) => [styles.addBtn, pressed && { opacity: 0.8 }]}
        >
          <Feather name="plus" size={18} color={Colors.textOnDark} />
        </Pressable>
      </View>

      {/* filter pills */}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.pills}
        style={styles.pillsRow}
      >
        {FILTER_OPTIONS.map((opt) => {
          const active = statusFilter === opt.value;
          return (
            <Pressable
              key={opt.value}
              style={[styles.pill, active && styles.pillActive]}
              onPress={() => setStatusFilter(opt.value)}
            >
              <Text
                style={[styles.pillText, active && styles.pillTextActive]}
              >
                {opt.label}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={Colors.accent} />
        </View>
      ) : (
        <FlatList
          data={goals}
          keyExtractor={(g) => g.id}
          contentContainerStyle={
            goals.length === 0 ? styles.emptyContainer : styles.listContent
          }
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              tintColor={Colors.accent}
            />
          }
          ListEmptyComponent={
            <View style={styles.emptyCard}>
              <Feather name="target" size={32} color={Colors.accentSoft} />
              <Text style={styles.emptyTitle}>Sin metas registradas</Text>
              <Text style={styles.emptySub}>
                Creá tu primera meta con el botón + o contámela en el chat.
              </Text>
            </View>
          }
          renderItem={({ item }) => (
            <GoalRow
              goal={item}
              onPress={() =>
                navigation.navigate("GoalDetail", { goalId: item.id })
              }
            />
          )}
        />
      )}

      <GoalFormModal
        visible={createVisible}
        onClose={() => setCreateVisible(false)}
        onSaved={(saved) => {
          setCreateVisible(false);
          void goalsQuery.refetch();
          navigation.navigate("GoalDetail", { goalId: saved.id });
        }}
      />
    </SafeAreaView>
  );
}

// ── styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Colors.bg },
  addBtn: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: Colors.accent,
    justifyContent: "center",
    alignItems: "center",
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
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
  pillsRow: {
    backgroundColor: Colors.bgCard,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    maxHeight: 48,
  },
  pills: {
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    gap: Spacing.sm,
    flexDirection: "row",
  },
  pill: {
    paddingHorizontal: Spacing.md,
    paddingVertical: 5,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bgCard,
  },
  pillActive: {
    backgroundColor: Colors.accentBg,
    borderColor: Colors.accent,
  },
  pillText: {
    fontSize: FontSize.sm,
    fontWeight: "500",
    color: Colors.textSecondary,
  },
  pillTextActive: {
    color: Colors.accent,
    fontWeight: "700",
  },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  listContent: { padding: Spacing.md, gap: Spacing.sm },
  emptyContainer: { flex: 1, justifyContent: "center", padding: Spacing.md },
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
  row: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    gap: Spacing.xs,
    ...CardShadow,
  },
  rowPressed: { opacity: 0.78 },
  rowHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: Spacing.sm,
  },
  rowName: {
    flex: 1,
    fontSize: FontSize.md,
    fontWeight: "600",
    color: Colors.textPrimary,
  },
  statusBadge: {
    borderWidth: 1,
    borderRadius: Radius.sm,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  statusText: {
    fontSize: FontSize.xs,
    fontWeight: "700",
  },
  progressTrack: {
    height: 6,
    backgroundColor: Colors.bgElevated,
    borderRadius: 3,
    overflow: "hidden",
    marginVertical: Spacing.xs,
  },
  progressFill: {
    height: "100%",
    borderRadius: 3,
    minWidth: 4,
  },
  rowFooter: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  rowCurrent: {
    fontSize: FontSize.sm,
    fontWeight: "600",
    color: Colors.income,
  },
  rowPct: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },
  rowTarget: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
  },
  rowDate: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    marginTop: 2,
  },
  rowChevron: {
    position: "absolute",
    right: Spacing.md,
    bottom: Spacing.md,
  },
});
