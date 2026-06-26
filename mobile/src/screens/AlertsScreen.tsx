/**
 * Alertas — native in-app nudge feed (Phase 7 pull surface).
 *
 * Lists pending nudges (over-commitment, missing income, stale proposals,
 * upcoming bills) rendered server-side to CR-voseo text. Each card offers
 * the same three actions as the Telegram nudge: act (→ /act), later (hide
 * locally, returns next visit), dismiss (→ /dismiss, may auto-silence the
 * type after repeated dismissals — handled server-side).
 */
import { useCallback, useState } from "react";
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
import { useFocusEffect } from "@react-navigation/native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  actOnNudge,
  dismissNudge,
  fetchNudgeFeed,
  isCelebrationNudge,
  type NudgeFeedItem,
} from "../api/nudges";
import { Colors, FontSize, Radius, Spacing } from "../theme";

function labelFor(item: NudgeFeedItem, verb: string, fallback: string): string {
  return item.buttons.find((b) => b.verb === verb)?.label ?? fallback;
}

function hasVerb(item: NudgeFeedItem, verb: string): boolean {
  return item.buttons.some((b) => b.verb === verb);
}

export function AlertsScreen() {
  const qc = useQueryClient();
  const feedQ = useQuery({ queryKey: ["nudgeFeed"], queryFn: fetchNudgeFeed });
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  // Fresh on every focus: clear local "más tarde" hides and refetch.
  useFocusEffect(
    useCallback(() => {
      setHidden(new Set());
      qc.invalidateQueries({ queryKey: ["nudgeFeed"] });
    }, [qc]),
  );

  const actM = useMutation({
    mutationFn: (id: string) => actOnNudge(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["nudgeFeed"] }),
    onError: () => Alert.alert("Error", "No se pudo completar la acción."),
  });
  const dismissM = useMutation({
    mutationFn: (id: string) => dismissNudge(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["nudgeFeed"] }),
    onError: () => Alert.alert("Error", "No se pudo descartar."),
  });

  const items = (feedQ.data ?? []).filter((i) => !hidden.has(i.id));
  const busy = (id: string) =>
    (actM.isPending && actM.variables === id) ||
    (dismissM.isPending && dismissM.variables === id);

  const later = (id: string) =>
    setHidden((prev) => new Set(prev).add(id));

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <View style={styles.headerRow}>
          <Text style={styles.headerTitle}>Alertas</Text>
          <Pressable onPress={() => feedQ.refetch()} hitSlop={8}>
            <Feather name="refresh-cw" size={16} color={Colors.textMuted} />
          </Pressable>
        </View>
        <Text style={styles.headerSub}>
          {feedQ.isLoading
            ? "Cargando…"
            : feedQ.isError
              ? "No pude cargar tus alertas."
              : items.length === 0
                ? "Nada pendiente por ahora."
                : `${items.length} aviso(s) del asistente`}
        </Text>
      </View>

      <FlatList
        data={items}
        keyExtractor={(i) => i.id}
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            refreshing={feedQ.isFetching && !feedQ.isLoading}
            onRefresh={() => feedQ.refetch()}
            tintColor={Colors.accent}
          />
        }
        renderItem={({ item }) => {
          const isBusy = busy(item.id);
          const high = item.priority === "high";
          const celebrate = isCelebrationNudge(item.nudge_type);
          return (
            <View style={[styles.card, celebrate && styles.cardCelebrate]}>
              <View style={styles.cardTop}>
                <View
                  style={[
                    styles.iconBox,
                    high && styles.iconBoxHigh,
                    celebrate && styles.iconBoxCelebrate,
                  ]}
                >
                  <Feather
                    name={celebrate ? "award" : high ? "alert-triangle" : "bell"}
                    size={18}
                    color={
                      celebrate
                        ? Colors.income
                        : high
                          ? Colors.warning
                          : Colors.accent
                    }
                  />
                </View>
                {celebrate ? (
                  <Text style={styles.celebrateChip}>¡Logro!</Text>
                ) : (
                  high && <Text style={styles.highChip}>Importante</Text>
                )}
              </View>

              <Text style={styles.cardText}>{item.text}</Text>

              {celebrate ? (
                // Positive nudges: only the buttons the server sent (act/dismiss),
                // no "Más tarde". act → screen, dismiss ("Cerrar") closes the card.
                <View style={styles.actions}>
                  {hasVerb(item, "act") && (
                    <Pressable
                      onPress={() => actM.mutate(item.id)}
                      disabled={isBusy}
                      style={({ pressed }) => [
                        styles.btn,
                        styles.btnCelebrate,
                        isBusy && styles.btnDisabled,
                        pressed && !isBusy && { opacity: 0.85 },
                      ]}
                    >
                      {isBusy ? (
                        <ActivityIndicator size="small" color={Colors.textOnDark} />
                      ) : (
                        <Text style={styles.btnPrimaryLabel}>
                          {labelFor(item, "act", "Ver")}
                        </Text>
                      )}
                    </Pressable>
                  )}
                  <Pressable
                    onPress={() => dismissM.mutate(item.id)}
                    disabled={isBusy}
                    style={({ pressed }) => [
                      styles.btn,
                      styles.btnOutline,
                      isBusy && styles.btnDisabled,
                      pressed && !isBusy && { opacity: 0.7 },
                    ]}
                  >
                    <Text style={styles.btnOutlineLabel}>
                      {labelFor(item, "dismiss", "Cerrar")}
                    </Text>
                  </Pressable>
                </View>
              ) : (
                <View style={styles.actions}>
                  <Pressable
                    onPress={() => actM.mutate(item.id)}
                    disabled={isBusy}
                    style={({ pressed }) => [
                      styles.btn,
                      styles.btnPrimary,
                      isBusy && styles.btnDisabled,
                      pressed && !isBusy && { opacity: 0.85 },
                    ]}
                  >
                    {isBusy ? (
                      <ActivityIndicator size="small" color={Colors.textOnDark} />
                    ) : (
                      <Text style={styles.btnPrimaryLabel}>
                        {labelFor(item, "act", "Revisar")}
                      </Text>
                    )}
                  </Pressable>
                  <Pressable
                    onPress={() => later(item.id)}
                    disabled={isBusy}
                    style={({ pressed }) => [
                      styles.btn,
                      pressed && { opacity: 0.6 },
                    ]}
                  >
                    <Text style={styles.btnGhostLabel}>
                      {labelFor(item, "later", "Más tarde")}
                    </Text>
                  </Pressable>
                  <Pressable
                    onPress={() => dismissM.mutate(item.id)}
                    disabled={isBusy}
                    style={({ pressed }) => [
                      styles.btn,
                      styles.btnOutline,
                      isBusy && styles.btnDisabled,
                      pressed && !isBusy && { opacity: 0.7 },
                    ]}
                  >
                    <Text style={styles.btnOutlineLabel}>
                      {labelFor(item, "dismiss", "No mostrar más")}
                    </Text>
                  </Pressable>
                </View>
              )}
            </View>
          );
        }}
        ListEmptyComponent={
          feedQ.isLoading ? (
            <ActivityIndicator style={{ marginTop: Spacing.xl }} color={Colors.accent} />
          ) : feedQ.isError ? (
            <View style={styles.emptyBox}>
              <Feather name="alert-triangle" size={28} color={Colors.warning} />
              <Text style={styles.empty}>
                No pude cargar tus alertas. Deslizá para reintentar.
              </Text>
              <Pressable
                onPress={() => feedQ.refetch()}
                style={({ pressed }) => [styles.retryBtn, pressed && { opacity: 0.85 }]}
              >
                <Text style={styles.retryLabel}>Reintentar</Text>
              </Pressable>
            </View>
          ) : (
            <View style={styles.emptyBox}>
              <Feather name="check-circle" size={28} color={Colors.income} />
              <Text style={styles.empty}>
                Sin alertas pendientes. Te aviso acá cuando algo necesite tu
                atención.
              </Text>
            </View>
          )
        }
      />
    </SafeAreaView>
  );
}

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
  headerRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  headerTitle: { fontSize: FontSize.lg, fontWeight: "700", color: Colors.textPrimary },
  headerSub: { fontSize: FontSize.sm, color: Colors.textMuted, marginTop: 2 },
  content: { padding: Spacing.md, gap: Spacing.sm, paddingBottom: Spacing.xl },
  card: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    gap: Spacing.sm,
  },
  cardCelebrate: { borderColor: Colors.income },
  cardTop: { flexDirection: "row", alignItems: "center", gap: Spacing.sm },
  iconBox: {
    width: 34,
    height: 34,
    borderRadius: Radius.md,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: Colors.accentBg,
  },
  iconBoxHigh: { backgroundColor: Colors.warningBg },
  iconBoxCelebrate: { backgroundColor: "#E4F0E8" },
  highChip: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    color: Colors.warning,
    backgroundColor: Colors.warningBg,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 2,
    overflow: "hidden",
  },
  celebrateChip: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    color: Colors.income,
    backgroundColor: "#E4F0E8",
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 2,
    overflow: "hidden",
  },
  cardText: {
    fontSize: FontSize.md,
    color: Colors.textPrimary,
    lineHeight: 22,
  },
  actions: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    marginTop: Spacing.xs,
  },
  btn: {
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    alignItems: "center",
    justifyContent: "center",
  },
  btnPrimary: { backgroundColor: Colors.accent },
  btnCelebrate: { backgroundColor: Colors.income },
  btnPrimaryLabel: { fontSize: FontSize.sm, fontWeight: "700", color: Colors.textOnDark },
  btnGhostLabel: { fontSize: FontSize.sm, fontWeight: "600", color: Colors.textMuted },
  btnOutline: { borderWidth: 1, borderColor: Colors.border },
  btnOutlineLabel: { fontSize: FontSize.sm, fontWeight: "600", color: Colors.textSecondary },
  btnDisabled: { opacity: 0.5 },
  emptyBox: {
    alignItems: "center",
    gap: Spacing.sm,
    marginTop: Spacing.xl,
    paddingHorizontal: Spacing.md,
  },
  empty: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    textAlign: "center",
    lineHeight: 20,
  },
  retryBtn: {
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.lg,
    paddingVertical: Spacing.sm,
  },
  retryLabel: { color: Colors.accent, fontWeight: "600", fontSize: FontSize.sm },
});
