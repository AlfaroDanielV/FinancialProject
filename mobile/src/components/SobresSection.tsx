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
import { JoinEnvelopeModal } from "./JoinEnvelopeModal";
import { ReallocateModal } from "./ReallocateModal";
import { StarterPackModal } from "./StarterPackModal";

export function SobresSection({
  onOpenAnalytics,
}: {
  // Phase 7h: when provided, the header shows a "Análisis" affordance that
  // opens the Analytics screen (charts of the budget execution + cash flow).
  onOpenAnalytics?: () => void;
}) {
  const [createOpen, setCreateOpen] = useState(false);
  const [joinOpen, setJoinOpen] = useState(false);
  const [starterOpen, setStarterOpen] = useState(false);
  const [detailItem, setDetailItem] = useState<EnvelopeSummaryItem | null>(null);
  // Over-limit reallocation target (Phase 8 B6): the over-limit sobre the user
  // wants to cover by moving budget from another same-level sobre.
  const [reallocOver, setReallocOver] = useState<EnvelopeSummaryItem | null>(null);
  // Progressive disclosure: nesting (sub-sobres) is a power feature hidden
  // behind this toggle so a first-time user sees a flat list.
  const [advanced, setAdvanced] = useState(false);

  const summaryQuery = useQuery({
    queryKey: ["envelopes", "summary"],
    queryFn: fetchEnvelopeSummary,
  });

  const summary = summaryQuery.data;
  // Shared envelopes (ones you JOINED) come appended to the summary with
  // is_shared=true. Keep them OUT of the class groups + subtotals (those are
  // your own budget) and render them in their own "Compartidos con vos" block.
  const ownItems = useMemo(
    () => (summary?.envelopes ?? []).filter((e) => !e.is_shared),
    [summary],
  );
  const sharedItems = useMemo(
    () => (summary?.envelopes ?? []).filter((e) => e.is_shared),
    [summary],
  );
  const grouped = useMemo(() => groupItems(ownItems), [ownItems]);
  // Progressive disclosure: a parent's allocation jargon ("Sin asignar / Sobre-
  // asignado") only shows once it actually has sub-sobres. The "Avanzado" toggle
  // surfaces only once the user already nests, or when they opt in.
  const parentIdsWithChildren = useMemo(
    () =>
      new Set(
        ownItems.filter((e) => e.parent_id != null).map((e) => e.parent_id as string),
      ),
    [ownItems],
  );
  const hasAnySubSobres = parentIdsWithChildren.size > 0;

  // Tapping an envelope opens the detail (spend bar + this month's expenses
  // with assign toggles + an "Editar" entry). "+ Nuevo" opens the create sheet.
  const openDetail = (item: EnvelopeSummaryItem) => setDetailItem(item);
  const openCreate = () => setCreateOpen(true);

  // Same-level, same-currency own sobres with budget to spare — the candidates
  // the over-limit sobre can pull from (mirrors the backend reallocate rule).
  const reallocCandidates = useMemo(() => {
    if (reallocOver == null) return [];
    return ownItems.filter(
      (e) =>
        e.id !== reallocOver.id &&
        e.parent_id === reallocOver.parent_id &&
        e.currency === reallocOver.currency &&
        (e.available ?? e.remaining ?? 0) > 0,
    );
  }, [ownItems, reallocOver]);

  const hasEnvelopes = ownItems.length > 0 || sharedItems.length > 0;

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
            onPress={() => setJoinOpen(true)}
            style={({ pressed }) => [styles.analyticsBtn, pressed && { opacity: 0.7 }]}
            hitSlop={6}
          >
            <Feather name="user-plus" size={14} color={Colors.accent} />
            <Text style={styles.addText}>Unirme</Text>
          </Pressable>
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
        <View style={styles.emptyCta}>
          <Feather name="inbox" size={20} color={Colors.textMuted} />
          <Text style={styles.emptyTitle}>Armá tu presupuesto en 1 minuto</Text>
          <Text style={styles.emptyBody}>
            Empezá con un paquete de sobres listo (comida, servicios, gustos,
            ahorro, inversión). Ajustás los montos a tu gusto.
          </Text>
          <Pressable
            onPress={() => setStarterOpen(true)}
            style={({ pressed }) => [styles.emptyPrimaryBtn, pressed && { opacity: 0.85 }]}
          >
            <Feather name="zap" size={14} color={Colors.bgCard} />
            <Text style={styles.emptyPrimaryText}>Armar mi presupuesto</Text>
          </Pressable>
          <Pressable onPress={openCreate} hitSlop={8}>
            <Text style={styles.emptySecondaryText}>o creá un sobre a mano</Text>
          </Pressable>
        </View>
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
                    hasChildren={parentIdsWithChildren.has(env.id)}
                    onReallocate={() => setReallocOver(env)}
                  />
                ))}
              </View>
            );
          })}

          {ownItems.length > 0 && summary!.monthly_income != null && (
            <Text style={styles.incomeNote}>
              Topes: {formatMoney(summary!.total_limit, summary!.currency)} de{" "}
              {formatMoney(summary!.monthly_income, summary!.currency)} de ingreso mensual
            </Text>
          )}

          {/* Progressive disclosure: nesting is a power feature. The toggle only
              shows for a first-timer (no sub-sobres yet); once they nest, the
              sub-sobre affordance is always available (no toggle needed). */}
          {ownItems.length > 0 && !hasAnySubSobres && (
            <Pressable
              onPress={() => setAdvanced((v) => !v)}
              hitSlop={6}
              style={({ pressed }) => [styles.advancedToggle, pressed && { opacity: 0.7 }]}
            >
              <Feather
                name={advanced ? "chevron-up" : "sliders"}
                size={12}
                color={Colors.textMuted}
              />
              <Text style={styles.advancedText}>
                {advanced
                  ? "Ocultar opciones avanzadas"
                  : "Opciones avanzadas (sub-sobres)"}
              </Text>
            </Pressable>
          )}

          {sharedItems.length > 0 && (
            <View style={styles.classBlock}>
              <View style={styles.classHeaderRow}>
                <View style={styles.classHeaderLeft}>
                  <Feather name="users" size={13} color={Colors.textSecondary} />
                  <Text style={styles.classTitle}>Compartidos con vos</Text>
                </View>
              </View>
              {flattenEnvelopeTree(sharedItems).map((env) => (
                <EnvelopeRow
                  key={env.id}
                  item={env}
                  currency={env.currency}
                  color={ENVELOPE_CLASS_COLORS[env.envelope_class]}
                  onPress={() => openDetail(env)}
                  sharedBy={
                    env.parent_id == null
                      ? env.shared_by_name ?? undefined
                      : undefined
                  }
                />
              ))}
            </View>
          )}
        </View>
      )}

      <EnvelopeEditModal
        visible={createOpen}
        envelope={null}
        onClose={() => setCreateOpen(false)}
        onSaved={() => setCreateOpen(false)}
      />

      <JoinEnvelopeModal visible={joinOpen} onClose={() => setJoinOpen(false)} />

      <StarterPackModal
        visible={starterOpen}
        currency={summary?.currency ?? "CRC"}
        monthlyIncome={summary?.monthly_income ?? null}
        onClose={() => setStarterOpen(false)}
        onCreated={() => setStarterOpen(false)}
      />

      <ReallocateModal
        visible={reallocOver != null}
        over={reallocOver}
        candidates={reallocCandidates}
        onClose={() => setReallocOver(null)}
        onDone={() => setReallocOver(null)}
      />

      <EnvelopeDetailModal
        visible={detailItem != null}
        item={detailItem}
        allowSubSobres={advanced || hasAnySubSobres}
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
  sharedBy,
  hasChildren = false,
  onReallocate,
}: {
  item: EnvelopeSummaryItem;
  currency: string;
  color: string;
  onPress: () => void;
  // Shared root: "Compartido por X" caption (undefined for own + sub-sobres).
  sharedBy?: string;
  // Progressive disclosure: allocation jargon only shows when the sobre nests.
  hasChildren?: boolean;
  // Phase 8 B6: when over-limit, offer a one-tap reallocation (own rows only —
  // shared rows you joined can't be reallocated, so this is left undefined).
  onReallocate?: () => void;
}) {
  // Money-left bar: starts full, drains with each expense + each reservation,
  // red in the last 5%. `remaining` is the free amount (limit − reserved − spent).
  const { remaining, fraction, low, reserved } = envelopeProgress(item);
  // Sub-sobres indent under their parent; a parent with sub-sobres shows how
  // much of its budget is still unsplit (hidden for flat sobres).
  const indent = (item.depth - 1) * 14;
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
      {sharedBy && (
        <Text style={styles.sharedNote}>
          Compartido por {sharedBy}
          {item.member_count ? ` · ${item.member_count} personas` : ""}
        </Text>
      )}
      {reserved > 0 && (
        <Text style={styles.reservedNote}>
          {formatMoney(reserved, currency)} reservado para gastos fijos
        </Text>
      )}
      {item.over_limit && (
        <View style={styles.overRow}>
          <Text style={styles.overText}>
            Te pasaste por {formatMoney(item.spent - item.limit_amount, currency)}
          </Text>
          {onReallocate && (
            <Pressable
              onPress={onReallocate}
              hitSlop={6}
              style={({ pressed }) => [styles.coverBtn, pressed && { opacity: 0.7 }]}
            >
              <Feather name="repeat" size={12} color={Colors.accent} />
              <Text style={styles.coverBtnText}>¿Cubrís moviendo de otro sobre?</Text>
            </Pressable>
          )}
        </View>
      )}
      {hasChildren &&
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
  overRow: { gap: 4 },
  overText: {
    fontSize: FontSize.xs,
    color: Colors.expense,
    fontWeight: "500",
  },
  coverBtn: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: 4,
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 3,
  },
  coverBtnText: { color: Colors.accent, fontSize: FontSize.xs, fontWeight: "600" },
  advancedToggle: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "center",
    gap: 4,
    paddingVertical: Spacing.xs,
  },
  advancedText: { fontSize: FontSize.xs, color: Colors.textMuted },
  allocNote: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },
  reservedNote: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontStyle: "italic",
  },
  sharedNote: {
    fontSize: FontSize.xs,
    color: Colors.textSecondary,
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
  emptyPrimaryBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.lg,
    paddingVertical: Spacing.sm + 2,
    marginTop: Spacing.xs,
  },
  emptyPrimaryText: { color: Colors.bgCard, fontSize: FontSize.sm, fontWeight: "600" },
  emptySecondaryText: {
    fontSize: FontSize.xs,
    color: Colors.accent,
    fontWeight: "500",
    marginTop: 2,
  },
});
