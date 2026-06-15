/**
 * Envelope budgeting (Sobres) — home-tab section.
 *
 * The interactive surface the operator asked for: per-envelope monthly
 * spending caps with a progress bar (spent ÷ limit), red when over. Grouped
 * by class (Necesidades / Gustos / Ahorro / Inversión) with a per-class
 * subtotal. Tap an envelope to edit; "+ Nuevo sobre" to create. Spend is the
 * live figure from /envelopes/summary (current month, user timezone).
 *
 * Bauhaus: no chart lib — bars are flex Views. Red only signals over-limit.
 */
import { useMemo, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Feather } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";

import {
  ENVELOPE_CLASS_COLORS,
  ENVELOPE_CLASS_LABELS,
  ENVELOPE_CLASS_ORDER,
  envelopeProgress,
  fetchEnvelopeSummary,
  flattenEnvelopeTree,
  type EnvelopeClass,
  type EnvelopeSummaryItem,
} from "../api/envelopes";
import { formatMoney } from "../lib/format";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";
import { EnvelopeDetailModal } from "./EnvelopeDetailModal";
import { EnvelopeEditModal } from "./EnvelopeEditModal";

export function SobresSection({
  onOpenAnalytics,
}: {
  // Phase 7h: when provided, the header shows a "Análisis" affordance that
  // opens the Analytics screen (charts of the budget execution + cash flow).
  onOpenAnalytics?: () => void;
}) {
  const [createOpen, setCreateOpen] = useState(false);
  const [detailItem, setDetailItem] = useState<EnvelopeSummaryItem | null>(null);

  const summaryQuery = useQuery({
    queryKey: ["envelopes", "summary"],
    queryFn: fetchEnvelopeSummary,
  });

  const summary = summaryQuery.data;
  const grouped = useMemo(() => groupItems(summary?.envelopes ?? []), [summary]);

  // Tapping an envelope opens the detail (spend bar + this month's expenses
  // with assign toggles + an "Editar" entry). "+ Nuevo" opens the create sheet.
  const openDetail = (item: EnvelopeSummaryItem) => setDetailItem(item);
  const openCreate = () => setCreateOpen(true);

  const hasEnvelopes = (summary?.envelopes.length ?? 0) > 0;

  return (
    <View style={[styles.card]}>
      <View style={styles.header}>
        <Text style={styles.title}>Sobres</Text>
        <View style={styles.headerActions}>
          {onOpenAnalytics && (
            <Pressable
              onPress={onOpenAnalytics}
              style={({ pressed }) => [styles.analyticsBtn, pressed && { opacity: 0.7 }]}
              hitSlop={6}
            >
              <Feather name="bar-chart-2" size={14} color={Colors.accent} />
              <Text style={styles.addText}>Análisis</Text>
            </Pressable>
          )}
          <Pressable
            onPress={openCreate}
            style={({ pressed }) => [styles.addBtn, pressed && { opacity: 0.7 }]}
          >
            <Feather name="plus" size={14} color={Colors.accent} />
            <Text style={styles.addText}>Nuevo</Text>
          </Pressable>
        </View>
      </View>

      {summaryQuery.isLoading ? (
        <ActivityIndicator color={Colors.accent} style={{ paddingVertical: Spacing.lg }} />
      ) : !hasEnvelopes ? (
        <Pressable onPress={openCreate} style={styles.emptyCta}>
          <Feather name="inbox" size={20} color={Colors.textMuted} />
          <Text style={styles.emptyTitle}>Creá tu primer sobre</Text>
          <Text style={styles.emptyBody}>
            Poné un tope mensual por tipo (necesidades, gustos, ahorro,
            inversión) y mirá cuánto llevás gastado.
          </Text>
        </Pressable>
      ) : (
        <View style={styles.body}>
          {ENVELOPE_CLASS_ORDER.filter((c) => grouped[c]?.length).map((cls) => {
            const sub = summary!.by_class.find((s) => s.envelope_class === cls);
            return (
              <View key={cls} style={styles.classBlock}>
                <View style={styles.classHeaderRow}>
                  <View style={styles.classHeaderLeft}>
                    <View
                      style={[styles.classDot, { backgroundColor: ENVELOPE_CLASS_COLORS[cls] }]}
                    />
                    <Text style={styles.classTitle}>{ENVELOPE_CLASS_LABELS[cls]}</Text>
                  </View>
                  {sub &&
                    (() => {
                      const { remaining, low } = envelopeProgress({
                        limit_amount: sub.limit_total,
                        remaining: sub.limit_total - sub.spent_total,
                      });
                      return (
                        <Text style={[styles.classSub, low && { color: Colors.expense }]}>
                          {formatMoney(Math.max(0, remaining), summary!.currency)} de{" "}
                          {formatMoney(sub.limit_total, summary!.currency)}
                        </Text>
                      );
                    })()}
                </View>

                {flattenEnvelopeTree(grouped[cls]).map((env) => (
                  <EnvelopeRow
                    key={env.id}
                    item={env}
                    currency={summary!.currency}
                    color={ENVELOPE_CLASS_COLORS[cls]}
                    onPress={() => openDetail(env)}
                  />
                ))}
              </View>
            );
          })}

          {summary!.monthly_income != null && (
            <Text style={styles.incomeNote}>
              Topes: {formatMoney(summary!.total_limit, summary!.currency)} de{" "}
              {formatMoney(summary!.monthly_income, summary!.currency)} de ingreso mensual
            </Text>
          )}
        </View>
      )}

      <EnvelopeEditModal
        visible={createOpen}
        envelope={null}
        onClose={() => setCreateOpen(false)}
        onSaved={() => setCreateOpen(false)}
      />

      <EnvelopeDetailModal
        visible={detailItem != null}
        item={detailItem}
        onClose={() => setDetailItem(null)}
      />
    </View>
  );
}

function EnvelopeRow({
  item,
  currency,
  color,
  onPress,
}: {
  item: EnvelopeSummaryItem;
  currency: string;
  color: string;
  onPress: () => void;
}) {
  // Money-left bar: starts full, drains with each expense + each reservation,
  // red in the last 5%. `remaining` is the free amount (limit − reserved − spent).
  const { remaining, fraction, low, reserved } = envelopeProgress(item);
  // Sub-sobres indent under their parent; a parent (allocated > 0) shows how
  // much of its budget is still unsplit.
  const indent = (item.depth - 1) * 14;
  const isParent = item.allocated > 0;
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.row, { marginLeft: indent }, pressed && { opacity: 0.7 }]}
    >
      <View style={styles.rowMeta}>
        <View style={styles.rowNameWrap}>
          {item.depth > 1 && (
            <Feather name="corner-down-right" size={12} color={Colors.textMuted} />
          )}
          <Text style={styles.rowName} numberOfLines={1}>
            {item.name}
          </Text>
          <Feather name="chevron-right" size={14} color={Colors.textMuted} />
        </View>
        <Text style={[styles.rowAmount, low && { color: Colors.expense }]}>
          {formatMoney(Math.max(0, remaining), currency)} de{" "}
          {formatMoney(item.limit_amount, currency)}
        </Text>
      </View>
      <View style={styles.barTrack}>
        <View
          style={[
            styles.barFill,
            { width: `${Math.round(fraction * 100)}%` as `${number}%` },
            { backgroundColor: low ? Colors.expense : color },
          ]}
        />
      </View>
      {reserved > 0 && (
        <Text style={styles.reservedNote}>
          {formatMoney(reserved, currency)} reservado para gastos fijos
        </Text>
      )}
      {item.over_limit && (
        <Text style={styles.overText}>
          Te pasaste por {formatMoney(item.spent - item.limit_amount, currency)}
        </Text>
      )}
      {isParent &&
        (item.over_allocated ? (
          <Text style={styles.overText}>
            Sobreasignado {formatMoney(-item.unallocated, currency)} entre sus sub-sobres
          </Text>
        ) : item.unallocated > 0 ? (
          <Text style={styles.allocNote}>
            Sin asignar {formatMoney(item.unallocated, currency)}
          </Text>
        ) : null)}
    </Pressable>
  );
}

function groupItems(
  items: EnvelopeSummaryItem[],
): Record<EnvelopeClass, EnvelopeSummaryItem[]> {
  const out = { needs: [], wants: [], savings: [], investing: [] } as Record<
    EnvelopeClass,
    EnvelopeSummaryItem[]
  >;
  for (const it of items) (out[it.envelope_class] ??= []).push(it);
  return out;
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    ...CardShadow,
    overflow: "hidden",
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    padding: Spacing.md,
  },
  title: { fontSize: FontSize.md, color: Colors.textPrimary, fontWeight: "600" },
  headerActions: { flexDirection: "row", alignItems: "center", gap: Spacing.sm },
  analyticsBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: Spacing.xs,
    paddingVertical: 4,
  },
  addBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 4,
  },
  addText: { color: Colors.accent, fontSize: FontSize.sm, fontWeight: "600" },

  body: {
    borderTopWidth: 1,
    borderTopColor: Colors.borderLight,
    padding: Spacing.md,
    gap: Spacing.md,
  },
  classBlock: { gap: Spacing.sm },
  classHeaderRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  classHeaderLeft: { flexDirection: "row", alignItems: "center", gap: Spacing.xs },
  classDot: { width: 9, height: 9, borderRadius: 5 },
  classTitle: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    letterSpacing: 0.4,
    textTransform: "uppercase",
    color: Colors.textSecondary,
  },
  classSub: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontVariant: ["tabular-nums"],
  },

  row: { gap: Spacing.xs },
  rowMeta: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "baseline",
  },
  rowNameWrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 2,
    flex: 1,
    marginRight: Spacing.sm,
  },
  rowName: {
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
    fontWeight: "500",
    flexShrink: 1,
  },
  rowAmount: {
    fontSize: FontSize.xs,
    color: Colors.textSecondary,
    fontVariant: ["tabular-nums"],
  },
  barTrack: { height: 6, backgroundColor: Colors.border, borderRadius: 3 },
  barFill: { height: 6, borderRadius: 3 },
  overText: {
    fontSize: FontSize.xs,
    color: Colors.expense,
    fontWeight: "500",
  },
  allocNote: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },
  reservedNote: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontStyle: "italic",
  },
  incomeNote: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    textAlign: "center",
    marginTop: Spacing.xs,
  },

  // ── empty state ──────────────────────────────────────────────────────────────
  emptyCta: {
    alignItems: "center",
    gap: Spacing.xs,
    paddingHorizontal: Spacing.lg,
    paddingBottom: Spacing.lg,
    paddingTop: Spacing.sm,
  },
  emptyTitle: { fontSize: FontSize.sm, color: Colors.textSecondary, fontWeight: "600" },
  emptyBody: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    textAlign: "center",
    lineHeight: 17,
  },
});
