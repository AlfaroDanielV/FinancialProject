/**
 * Phase 6f B14 — Memoria e privacidad screen.
 *
 * Shows the user's insight memory grouped by category (metas / conozco /
 * patrones / banderas). Supports:
 *   - Per-row delete
 *   - Per-group delete
 *   - Delete all (two-step confirmation)
 *   - Export as JSON (opens iOS share sheet)
 *
 * The destructive DELETE routes require the bearer JWT which the native
 * client already sends on every request via the interceptor.
 */
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  deleteAllInsights,
  deleteInsight,
  deleteInsightGroup,
  exportInsights,
  fetchInsights,
  type InsightGroup,
  type InsightItem,
} from "../api/memoria";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";

// ── insight row ───────────────────────────────────────────────────────────────

function InsightRow({
  item,
  onDelete,
  isPending,
}: {
  item: InsightItem;
  onDelete: (id: string) => void;
  isPending: boolean;
}) {
  return (
    <View style={styles.insightRow}>
      <View style={styles.insightContent}>
        <Text style={styles.insightType}>{item.insight_type}</Text>
        <Text style={styles.insightDesc} numberOfLines={3}>
          {item.description || JSON.stringify(item.content)}
        </Text>
        <View style={styles.insightMeta}>
          <Text style={styles.insightSource}>{item.source}</Text>
          {item.user_locked ? (
            <Feather name="lock" size={10} color={Colors.textMuted} />
          ) : null}
          <Text style={styles.insightConf}>
            {(Number(item.confidence) * 100).toFixed(0)}%
          </Text>
        </View>
      </View>

      {isPending ? (
        <ActivityIndicator size="small" color={Colors.textMuted} />
      ) : (
        <Pressable
          style={({ pressed }) => [
            styles.deleteBtn,
            pressed && { opacity: 0.6 },
          ]}
          onPress={() => onDelete(item.id)}
        >
          <Feather name="trash-2" size={14} color={Colors.expense} />
        </Pressable>
      )}
    </View>
  );
}

// ── group section ─────────────────────────────────────────────────────────────

function GroupSection({
  group,
  onDeleteItem,
  onDeleteGroup,
  pendingId,
  groupPending,
}: {
  group: InsightGroup;
  onDeleteItem: (id: string) => void;
  onDeleteGroup: (key: string) => void;
  pendingId: string | null;
  groupPending: string | null;
}) {
  const [open, setOpen] = useState(true);

  return (
    <View style={styles.section}>
      <Pressable
        style={styles.sectionHeader}
        onPress={() => setOpen((v) => !v)}
      >
        <Text style={styles.sectionLabel}>{group.label}</Text>
        <View style={styles.sectionHeaderRight}>
          <Text style={styles.sectionCount}>{group.items.length}</Text>
          {groupPending === group.group ? (
            <ActivityIndicator size="small" color={Colors.textMuted} />
          ) : (
            <Pressable
              style={({ pressed }) => [
                styles.groupDeleteBtn,
                pressed && { opacity: 0.6 },
              ]}
              onPress={(e) => {
                e.stopPropagation?.();
                onDeleteGroup(group.group);
              }}
            >
              <Text style={styles.groupDeleteText}>Borrar grupo</Text>
            </Pressable>
          )}
          <Feather
            name={open ? "chevron-up" : "chevron-down"}
            size={16}
            color={Colors.textMuted}
          />
        </View>
      </Pressable>

      {open
        ? group.items.map((item) => (
            <InsightRow
              key={item.id}
              item={item}
              onDelete={onDeleteItem}
              isPending={pendingId === item.id}
            />
          ))
        : null}
    </View>
  );
}

// ── main screen ───────────────────────────────────────────────────────────────

export function MemoryScreen() {
  const [refreshing, setRefreshing] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [groupPending, setGroupPending] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const queryClient = useQueryClient();

  const insightsQuery = useQuery({
    queryKey: ["insights"],
    queryFn: fetchInsights,
  });

  const data = insightsQuery.data;
  const isLoading = insightsQuery.isLoading && !refreshing;

  const deleteAllMutation = useMutation({
    mutationFn: deleteAllInsights,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["insights"] });
    },
    onError: () => Alert.alert("Error", "No se pudo borrar la memoria."),
  });

  async function handleDeleteItem(id: string) {
    setPendingId(id);
    try {
      await deleteInsight(id);
      queryClient.invalidateQueries({ queryKey: ["insights"] });
    } catch {
      Alert.alert("Error", "No se pudo eliminar el insight.");
    } finally {
      setPendingId(null);
    }
  }

  function handleDeleteGroup(group: string) {
    Alert.alert(
      "Borrar grupo",
      "Se eliminarán todos los insights de este grupo.",
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: "Borrar",
          style: "destructive",
          onPress: async () => {
            setGroupPending(group);
            try {
              await deleteInsightGroup(group);
              queryClient.invalidateQueries({ queryKey: ["insights"] });
            } catch {
              Alert.alert("Error", "No se pudo borrar el grupo.");
            } finally {
              setGroupPending(null);
            }
          },
        },
      ]
    );
  }

  function confirmDeleteAll() {
    Alert.alert(
      "Borrar toda mi memoria",
      "Esta acción es permanente. Se eliminarán todos los insights del asistente sobre vos.",
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: "Sí, borrar todo",
          style: "destructive",
          onPress: () => deleteAllMutation.mutate(),
        },
      ]
    );
  }

  async function handleExport() {
    setExporting(true);
    try {
      const payload = await exportInsights();
      const text = JSON.stringify(payload, null, 2);
      await Share.share({
        message: text,
        title: "Mi memoria — Personal Finance",
      });
    } catch {
      Alert.alert("Error", "No se pudo exportar la memoria.");
    } finally {
      setExporting(false);
    }
  }

  async function onRefresh() {
    setRefreshing(true);
    await insightsQuery.refetch();
    setRefreshing(false);
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Memoria del asistente</Text>
        <Text style={styles.headerSub}>Lo que el asistente recuerda de vos</Text>
      </View>

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={Colors.accent} />
        </View>
      ) : (
        <ScrollView
          contentContainerStyle={styles.scroll}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              tintColor={Colors.accent}
            />
          }
        >
          {data?.total === 0 || !data ? (
            <View style={styles.emptyCard}>
              <Feather name="cpu" size={32} color={Colors.accentSoft} />
              <Text style={styles.emptyTitle}>Sin memoria registrada</Text>
              <Text style={styles.emptySub}>
                El asistente aprende de tus hábitos financieros con el tiempo.
              </Text>
            </View>
          ) : (
            <>
              <Text style={styles.totalText}>
                {data.total} insight{data.total !== 1 ? "s" : ""} guardados
              </Text>

              {data.groups.map((group) => (
                <GroupSection
                  key={group.group}
                  group={group}
                  onDeleteItem={handleDeleteItem}
                  onDeleteGroup={handleDeleteGroup}
                  pendingId={pendingId}
                  groupPending={groupPending}
                />
              ))}
            </>
          )}

          {/* ── privacy actions ── */}
          <View style={styles.privacyCard}>
            <Text style={styles.privacyTitle}>Privacidad y datos</Text>

            <Pressable
              style={({ pressed }) => [
                styles.privacyBtn,
                pressed && { opacity: 0.7 },
                exporting && { opacity: 0.6 },
              ]}
              onPress={handleExport}
              disabled={exporting}
            >
              {exporting ? (
                <ActivityIndicator size="small" color={Colors.accent} />
              ) : (
                <Feather name="download" size={15} color={Colors.accent} />
              )}
              <Text style={[styles.privacyBtnText, { color: Colors.accent }]}>
                Exportar mi memoria (JSON)
              </Text>
            </Pressable>

            <Pressable
              style={({ pressed }) => [
                styles.privacyBtn,
                styles.privacyBtnDestructive,
                pressed && { opacity: 0.7 },
                deleteAllMutation.isPending && { opacity: 0.6 },
              ]}
              onPress={confirmDeleteAll}
              disabled={deleteAllMutation.isPending}
            >
              {deleteAllMutation.isPending ? (
                <ActivityIndicator size="small" color={Colors.expense} />
              ) : (
                <Feather name="trash-2" size={15} color={Colors.expense} />
              )}
              <Text style={[styles.privacyBtnText, { color: Colors.expense }]}>
                Borrar toda mi memoria
              </Text>
            </Pressable>
          </View>
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

// ── styles ────────────────────────────────────────────────────────────────────

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
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  scroll: { padding: Spacing.md, gap: Spacing.md },
  totalText: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    marginBottom: Spacing.xs,
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
  // group section
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
  sectionLabel: {
    fontSize: FontSize.sm,
    fontWeight: "700",
    color: Colors.textPrimary,
    flex: 1,
  },
  sectionHeaderRight: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
  },
  sectionCount: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    backgroundColor: Colors.bgElevated,
    borderRadius: Radius.sm,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  groupDeleteBtn: {
    paddingHorizontal: Spacing.sm,
    paddingVertical: 3,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.expense + "60",
  },
  groupDeleteText: {
    fontSize: FontSize.xs,
    fontWeight: "600",
    color: Colors.expense,
  },
  // insight row
  insightRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    gap: Spacing.sm,
  },
  insightContent: { flex: 1, gap: 2 },
  insightType: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    color: Colors.textMuted,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  insightDesc: {
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
    lineHeight: 18,
  },
  insightMeta: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    marginTop: 2,
  },
  insightSource: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },
  insightConf: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },
  deleteBtn: {
    padding: 6,
  },
  // privacy card
  privacyCard: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    gap: Spacing.sm,
    ...CardShadow,
  },
  privacyTitle: {
    fontSize: FontSize.sm,
    fontWeight: "700",
    color: Colors.textPrimary,
    marginBottom: 2,
  },
  privacyBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    paddingVertical: Spacing.sm,
    paddingHorizontal: Spacing.sm,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.accentSoft,
    backgroundColor: Colors.accentBg,
  },
  privacyBtnDestructive: {
    borderColor: Colors.expense + "60",
    backgroundColor: Colors.expense + "10",
  },
  privacyBtnText: {
    fontSize: FontSize.sm,
    fontWeight: "600",
  },
});
