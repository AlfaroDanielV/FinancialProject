/**
 * Envelope budgeting (Sobres) — detail + bulk-assign view.
 *
 * Opened by tapping an envelope on the Home tab. Shows the spend bar, an
 * "Editar" entry into the create/edit sheet, and **this month's expenses** with
 * a per-row toggle to assign / unassign the transaction to THIS envelope (a
 * transaction belongs to at most one envelope, so toggling on reassigns from
 * any other). Assignment goes through PATCH /transactions/{id}{envelope_id}.
 *
 * Fixed expenses (recurring bills + debts) attach/detach symmetrically:
 * "Gastos fijos en este sobre" rows offer "Quitar" (PATCH envelope_id: null),
 * "Gastos fijos sin sobre" rows offer "Asignar aquí" (root only).
 */
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  assignTransactionEnvelope,
  ENVELOPE_CLASS_COLORS,
  ENVELOPE_CLASS_LABELS,
  envelopeProgress,
  fetchEnvelopeMembers,
  fetchEnvelopeSummary,
  fetchMyUserId,
  removeEnvelopeMember,
  shareEnvelope,
  type EnvelopeMember,
  type EnvelopeSummaryItem,
} from "../api/envelopes";
import { fetchMonthExpenses, type TransactionResponse } from "../api/transactions";
import { attachBillToEnvelope, fetchRecurringBills } from "../api/bills";
import { attachDebtToEnvelope, fetchDebts } from "../api/debts";
import { formatMoney } from "../lib/format";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import { EnvelopeEditModal } from "./EnvelopeEditModal";
import { ReallocateModal } from "./ReallocateModal";

interface Props {
  visible: boolean;
  item: EnvelopeSummaryItem | null;
  // Progressive disclosure (Phase 8 B6): when false, the "+ Sub-sobre" creation
  // affordance is hidden so a first-time user isn't shown nesting. Existing
  // sub-sobres always render. Defaults true to preserve other callers.
  allowSubSobres?: boolean;
  onClose: () => void;
}

export function EnvelopeDetailModal({
  visible,
  item,
  allowSubSobres = true,
  onClose,
}: Props) {
  const qc = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);
  const [subOpen, setSubOpen] = useState(false);
  // Phase 8 B6: over-limit one-tap reallocation source picker.
  const [reallocOpen, setReallocOpen] = useState(false);
  // Phase 7f: expenses already assigned to ANOTHER envelope live in a
  // separate collapsed section so they can't be mistaken for assignable rows.
  const [othersOpen, setOthersOpen] = useState(false);
  // Drill-down stack of envelope ids opened beneath `item` (so a parent can
  // navigate into its sub-sobres and back).
  const [stack, setStack] = useState<string[]>([]);

  useEffect(() => {
    if (!visible) {
      setStack([]);
      setOthersOpen(false);
    }
  }, [visible]);
  useEffect(() => {
    setStack([]);
    setOthersOpen(false);
  }, [item?.id]);

  // The whole tree is read live from the summary so the bars + children stay
  // fresh after a create/edit (no stale prop snapshot).
  const summaryQuery = useQuery({
    queryKey: ["envelopes", "summary"],
    queryFn: fetchEnvelopeSummary,
    enabled: visible,
  });

  const expensesQuery = useQuery({
    queryKey: ["envelopes", "month-expenses"],
    queryFn: fetchMonthExpenses,
    enabled: visible && item != null,
  });

  const toggle = useMutation({
    mutationFn: ({ txId, envelopeId }: { txId: string; envelopeId: string | null }) =>
      assignTransactionEnvelope(txId, envelopeId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["envelopes"] });
      void qc.invalidateQueries({ queryKey: ["transactions"] });
    },
  });

  // Phase 8 B6: assign every still-unassigned gasto of the month to one sobre
  // in one pass — a gentle batch over the existing per-row assign mechanism.
  const bulkAssign = useMutation({
    mutationFn: async ({ txIds, envelopeId }: { txIds: string[]; envelopeId: string }) => {
      for (const id of txIds) await assignTransactionEnvelope(id, envelopeId);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["envelopes"] });
      void qc.invalidateQueries({ queryKey: ["transactions"] });
    },
  });

  // Fixed expenses (bills + debts) with their envelope link — split in render
  // into "unattached" (offered for one-tap attachment to THIS envelope) and
  // "attached here" (offered for detach). The reservation appears live (B2).
  const obligationsQuery = useQuery({
    queryKey: ["fixed-expenses"],
    // Members can't attach obligations — skip the fetch entirely for them.
    enabled: visible && item != null && !item.is_shared,
    queryFn: async () => {
      const [bills, debts] = await Promise.all([fetchRecurringBills(), fetchDebts()]);
      const out: {
        kind: "bill" | "debt";
        id: string;
        name: string;
        amount: number | null;
        envelope_id: string | null;
      }[] = [];
      for (const b of bills)
        out.push({
          kind: "bill",
          id: b.id,
          name: b.name,
          amount: b.amount_expected,
          envelope_id: b.envelope_id,
        });
      for (const d of debts)
        out.push({
          kind: "debt",
          id: d.id,
          name: d.name,
          amount: d.minimum_payment,
          envelope_id: d.envelope_id,
        });
      return out;
    },
  });

  const attach = useMutation({
    // `envelopeId: null` detaches (PATCH clears the FK server-side).
    mutationFn: async ({ kind, id, envelopeId }: { kind: "bill" | "debt"; id: string; envelopeId: string | null }) => {
      if (kind === "bill") await attachBillToEnvelope(id, envelopeId);
      else await attachDebtToEnvelope(id, envelopeId);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["envelopes"] });
      void qc.invalidateQueries({ queryKey: ["fixed-expenses"] });
      void qc.invalidateQueries({ queryKey: ["bills"] });
      void qc.invalidateQueries({ queryKey: ["debts"] });
    },
  });

  if (item == null) return null;

  const all = summaryQuery.data?.envelopes ?? [];
  const activeId = stack.length ? stack[stack.length - 1] : item.id;
  // Prefer the fresh summary row; fall back to the prop while it loads.
  const active = all.find((e) => e.id === activeId) ?? item;
  // A shared envelope you only JOINED: read-only (no edit / sub-sobre / attach),
  // but you can still assign your OWN expenses to it.
  const isMember = active.is_shared === true;
  const children = all.filter((e) => e.parent_id === active.id);
  const atRoot = stack.length === 0;
  const available = Math.max(0, active.limit_amount - active.allocated);

  // Money-left bar: starts full, drains with each expense, red in the last 5%.
  const { remaining, fraction, low } = envelopeProgress(active);
  const color = ENVELOPE_CLASS_COLORS[active.envelope_class];

  const obligations = obligationsQuery.data ?? [];
  const unattached = obligations.filter((o) => o.envelope_id == null);
  // Attached rows show at ANY depth (chat attach can target a sub-sobre),
  // so detach is always reachable from the node that holds the reservation.
  const attachedHere = obligations.filter((o) => o.envelope_id === active.id);

  // Phase 7f: the assignable list only shows unassigned rows + rows already
  // in THIS envelope. Expenses living in another envelope move to their own
  // collapsed section with a named pill + explicit "Mover aquí" action.
  const expenses = expensesQuery.data ?? [];
  const assignable = expenses.filter(
    (tx) => tx.envelope_id == null || tx.envelope_id === active.id,
  );
  const inOthers = expenses.filter(
    (tx) => tx.envelope_id != null && tx.envelope_id !== active.id,
  );
  const envelopeById = new Map(all.map((e) => [e.id, e]));

  // Phase 8 B6: same-level, same-currency own sobres with budget to spare — the
  // candidates an over-limit sobre can pull from (mirrors the backend rule).
  const reallocCandidates = all.filter(
    (e) =>
      !e.is_shared &&
      e.id !== active.id &&
      e.parent_id === active.parent_id &&
      e.currency === active.currency &&
      (e.available ?? e.remaining ?? 0) > 0,
  );
  // Unassigned gastos in the assignable list — drives the bulk-assign prompt.
  const unassignedInList = assignable.filter((tx) => tx.envelope_id == null);

  const goBack = () => setStack((s) => s.slice(0, -1));

  const Header = (
    <>
      {/* spend bar */}
      <View style={styles.summaryCard}>
        <View style={styles.summaryTop}>
          <View style={styles.classTag}>
            <View style={[styles.classDot, { backgroundColor: color }]} />
            <Text style={styles.classLabel}>
              {ENVELOPE_CLASS_LABELS[active.envelope_class]}
            </Text>
          </View>
          <Text style={[styles.summaryAmount, low && { color: Colors.expense }]}>
            {formatMoney(Math.max(0, remaining), active.currency)} de{" "}
            {formatMoney(active.limit_amount, active.currency)}
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
        <Text style={styles.summarySub}>
          {active.over_limit
            ? `Te pasaste por ${formatMoney(active.spent - active.limit_amount, active.currency)}`
            : `Gastado: ${formatMoney(active.spent, active.currency)}`}
        </Text>
        {active.over_limit && !isMember && (
          <Pressable
            onPress={() => setReallocOpen(true)}
            style={({ pressed }) => [styles.coverBtn, pressed && { opacity: 0.7 }]}
          >
            <Feather name="repeat" size={13} color={Colors.accent} />
            <Text style={styles.coverBtnText}>¿Cubrís moviendo de otro sobre?</Text>
          </Pressable>
        )}
        {active.allocated > 0 && (
          <Text style={styles.summarySub}>
            {`Sin asignar ${formatMoney(available, active.currency)} del presupuesto`}
          </Text>
        )}
        {isMember && (
          <>
            <Text style={styles.summarySub}>
              Compartido por {active.shared_by_name ?? "otra persona"}
              {active.member_count ? ` · ${active.member_count} personas` : ""}
            </Text>
            <Text style={styles.summarySub}>
              Vos: {formatMoney(active.your_spent ?? 0, active.currency)} de{" "}
              {formatMoney(active.limit_amount, active.currency)}
            </Text>
          </>
        )}
      </View>

      {/* sharing (root only): owner shares + manages members; member can leave */}
      {active.parent_id == null && (
        <SharingBlock active={active} isMember={isMember} onClose={onClose} />
      )}

      {/* sub-sobres */}
      {children.length > 0 && (
        <>
          <Text style={styles.listTitle}>Sub-sobres</Text>
          {children.map((c) => (
            <ChildRow key={c.id} item={c} onPress={() => setStack((s) => [...s, c.id])} />
          ))}
        </>
      )}

      {!isMember && allowSubSobres && active.depth < 5 && (
        <Pressable
          onPress={() => setSubOpen(true)}
          style={({ pressed }) => [styles.subBtn, pressed && { opacity: 0.7 }]}
        >
          <Feather name="plus" size={14} color={Colors.accent} />
          <Text style={styles.subBtnText}>Sub-sobre</Text>
        </Pressable>
      )}

      {!isMember && attachedHere.length > 0 && (
        <>
          <Text style={styles.listTitle}>Gastos fijos en este sobre</Text>
          {attachedHere.map((o) => (
            <View key={`${o.kind}-${o.id}`} style={styles.attachRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.attachName} numberOfLines={1}>
                  {o.name}
                </Text>
                <Text style={styles.attachMeta}>
                  {o.kind === "debt" ? "Cuota" : "Recibo"}
                  {o.amount != null
                    ? ` · ${formatMoney(o.amount, active.currency)}`
                    : ""}
                </Text>
              </View>
              <Pressable
                onPress={() =>
                  attach.mutate({ kind: o.kind, id: o.id, envelopeId: null })
                }
                disabled={attach.isPending}
                style={({ pressed }) => [styles.detachBtn, pressed && { opacity: 0.7 }]}
              >
                <Feather name="x" size={14} color={Colors.textSecondary} />
                <Text style={styles.detachBtnText}>Quitar</Text>
              </Pressable>
            </View>
          ))}
        </>
      )}

      {!isMember && atRoot && unattached.length > 0 && (
        <>
          <Text style={styles.listTitle}>Gastos fijos sin sobre</Text>
          {unattached.map((o) => (
            <View key={`${o.kind}-${o.id}`} style={styles.attachRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.attachName} numberOfLines={1}>
                  {o.name}
                </Text>
                <Text style={styles.attachMeta}>
                  {o.kind === "debt" ? "Cuota" : "Recibo"}
                  {o.amount != null
                    ? ` · ${formatMoney(o.amount, active.currency)}`
                    : ""}
                </Text>
              </View>
              <Pressable
                onPress={() =>
                  attach.mutate({ kind: o.kind, id: o.id, envelopeId: active.id })
                }
                disabled={attach.isPending}
                style={({ pressed }) => [styles.attachBtn, pressed && { opacity: 0.7 }]}
              >
                <Feather name="plus" size={14} color={Colors.accent} />
                <Text style={styles.attachBtnText}>Asignar aquí</Text>
              </Pressable>
            </View>
          ))}
        </>
      )}

      <Text style={styles.listTitle}>Gastos de este mes</Text>
      {unassignedInList.length > 0 && (
        <Pressable
          onPress={() =>
            bulkAssign.mutate({
              txIds: unassignedInList.map((tx) => tx.id),
              envelopeId: active.id,
            })
          }
          disabled={bulkAssign.isPending}
          style={({ pressed }) => [styles.bulkBtn, pressed && { opacity: 0.7 }]}
        >
          {bulkAssign.isPending ? (
            <ActivityIndicator color={Colors.accent} size="small" />
          ) : (
            <>
              <Feather name="check-circle" size={14} color={Colors.accent} />
              <Text style={styles.bulkBtnText}>
                Asignar {unassignedInList.length}{" "}
                {unassignedInList.length === 1 ? "gasto" : "gastos"} a este sobre
              </Text>
            </>
          )}
        </Pressable>
      )}
      {expensesQuery.isLoading && (
        <ActivityIndicator color={Colors.accent} style={{ marginTop: Spacing.md }} />
      )}
      {expensesQuery.isError && (
        <Text style={styles.muted}>No se pudieron cargar los gastos.</Text>
      )}
    </>
  );

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="pageSheet"
      onRequestClose={atRoot ? onClose : goBack}
    >
      <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
        <View style={styles.header}>
          <Pressable
            onPress={atRoot ? onClose : goBack}
            hitSlop={12}
            style={styles.headerBtn}
          >
            <Feather
              name={atRoot ? "x" : "chevron-left"}
              size={24}
              color={Colors.textSecondary}
            />
          </Pressable>
          <Text style={styles.headerTitle} numberOfLines={1}>
            {active.name}
          </Text>
          {isMember ? (
            <View style={styles.headerBtn} />
          ) : (
            <Pressable onPress={() => setEditOpen(true)} hitSlop={12} style={styles.headerBtn}>
              <Feather name="edit-2" size={20} color={Colors.accent} />
            </Pressable>
          )}
        </View>

        <FlatList
          data={assignable}
          keyExtractor={(t) => t.id}
          ListHeaderComponent={Header}
          contentContainerStyle={styles.listContent}
          ListEmptyComponent={
            !expensesQuery.isLoading && !expensesQuery.isError ? (
              <Text style={styles.muted}>
                {inOthers.length > 0
                  ? "Sin gastos disponibles — los demás ya están en otros sobres."
                  : "Sin gastos este mes."}
              </Text>
            ) : null
          }
          ListFooterComponent={
            inOthers.length > 0 ? (
              <View style={styles.othersSection}>
                <Pressable
                  onPress={() => setOthersOpen((v) => !v)}
                  style={({ pressed }) => [
                    styles.othersToggle,
                    pressed && { opacity: 0.7 },
                  ]}
                >
                  <Text style={styles.othersToggleText}>
                    En otros sobres ({inOthers.length})
                  </Text>
                  <Feather
                    name={othersOpen ? "chevron-up" : "chevron-down"}
                    size={16}
                    color={Colors.textSecondary}
                  />
                </Pressable>
                {othersOpen && (
                  <Text style={styles.othersHint}>
                    Estos gastos ya pertenecen a otro sobre. «Mover aquí» los
                    pasa a {active.name}.
                  </Text>
                )}
                {othersOpen &&
                  inOthers.map((tx) => {
                    const other = tx.envelope_id
                      ? envelopeById.get(tx.envelope_id)
                      : undefined;
                    return (
                      <OtherEnvelopeRow
                        key={tx.id}
                        tx={tx}
                        envelopeName={other?.name ?? "otro sobre"}
                        envelopeColor={
                          other
                            ? ENVELOPE_CLASS_COLORS[other.envelope_class]
                            : Colors.textMuted
                        }
                        onMove={() =>
                          toggle.mutate({ txId: tx.id, envelopeId: active.id })
                        }
                        pending={toggle.isPending}
                      />
                    );
                  })}
              </View>
            ) : null
          }
          renderItem={({ item: tx }) => (
            <ExpenseRow
              tx={tx}
              envelopeId={active.id}
              onToggle={(assigned) =>
                toggle.mutate({
                  txId: tx.id,
                  envelopeId: assigned ? null : active.id,
                })
              }
              pending={toggle.isPending}
            />
          )}
        />

        <EnvelopeEditModal
          visible={editOpen}
          envelope={active}
          onClose={() => setEditOpen(false)}
          onSaved={() => setEditOpen(false)}
        />

        <EnvelopeEditModal
          visible={subOpen}
          envelope={null}
          parentEnvelope={{
            id: active.id,
            name: active.name,
            envelope_class: active.envelope_class,
            currency: active.currency,
            available,
          }}
          onClose={() => setSubOpen(false)}
          onSaved={() => setSubOpen(false)}
        />

        <ReallocateModal
          visible={reallocOpen}
          over={active}
          candidates={reallocCandidates}
          onClose={() => setReallocOpen(false)}
          onDone={() => setReallocOpen(false)}
        />
      </SafeAreaView>
    </Modal>
  );
}

// ── sharing ("Sobres compartidos") ──────────────────────────────────────────
// Root-only. The OWNER mints a 24h code + manages members; a MEMBER can leave.

function SharingBlock({
  active,
  isMember,
  onClose,
}: {
  active: EnvelopeSummaryItem;
  isMember: boolean;
  onClose: () => void;
}) {
  if (active.parent_id != null) return null; // only roots are shareable
  return isMember ? (
    <MemberLeave rootId={active.id} onClose={onClose} />
  ) : (
    <OwnerShare rootId={active.id} />
  );
}

function OwnerShare({ rootId }: { rootId: string }) {
  const qc = useQueryClient();
  const [code, setCode] = useState<string | null>(null);

  const membersQuery = useQuery({
    queryKey: ["envelope-members", rootId],
    queryFn: () => fetchEnvelopeMembers(rootId),
  });

  const share = useMutation({
    mutationFn: () => shareEnvelope(rootId),
    onSuccess: (res) => {
      setCode(res.code);
      void qc.invalidateQueries({ queryKey: ["envelope-members", rootId] });
    },
    onError: () => Alert.alert("No se pudo compartir", "Intentá de nuevo."),
  });

  const remove = useMutation({
    mutationFn: (userId: string) => removeEnvelopeMember(rootId, userId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["envelope-members", rootId] });
      void qc.invalidateQueries({ queryKey: ["envelopes"] });
    },
  });

  const members = membersQuery.data ?? [];
  const invited = members.filter((m) => !m.is_owner);

  const confirmRemove = (m: EnvelopeMember) =>
    Alert.alert(
      `Quitar a ${m.full_name}`,
      "Sus gastos en este sobre se desvinculan (no se borran) y pierde el acceso.",
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: "Quitar",
          style: "destructive",
          onPress: () => remove.mutate(m.user_id),
        },
      ],
    );

  return (
    <View style={styles.shareBlock}>
      <Pressable
        onPress={() => share.mutate()}
        disabled={share.isPending}
        style={({ pressed }) => [styles.shareBtn, pressed && { opacity: 0.7 }]}
      >
        <Feather name="share-2" size={14} color={Colors.accent} />
        <Text style={styles.shareBtnText}>Compartir este sobre</Text>
      </Pressable>

      {code && (
        <View style={styles.codeBox}>
          <Text style={styles.codeText}>{code}</Text>
          <Text style={styles.codeHint}>
            Pasá este código a quien quieras. Vence en 24 h. Hasta 9 personas.
          </Text>
        </View>
      )}

      {invited.length > 0 && (
        <View style={styles.membersWrap}>
          <Text style={styles.membersTitle}>Miembros ({invited.length})</Text>
          {invited.map((m) => (
            <View key={m.user_id} style={styles.memberRow}>
              <Text style={styles.memberName} numberOfLines={1}>
                {m.full_name}
              </Text>
              <Pressable
                onPress={() => confirmRemove(m)}
                disabled={remove.isPending}
                style={({ pressed }) => [
                  styles.detachBtn,
                  pressed && { opacity: 0.7 },
                ]}
              >
                <Feather name="x" size={13} color={Colors.textSecondary} />
                <Text style={styles.detachBtnText}>Quitar</Text>
              </Pressable>
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

function MemberLeave({
  rootId,
  onClose,
}: {
  rootId: string;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const leave = useMutation({
    mutationFn: async () => {
      const myId = await fetchMyUserId();
      await removeEnvelopeMember(rootId, myId);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["envelopes"] });
      onClose();
    },
    onError: () => Alert.alert("No se pudo salir", "Intentá de nuevo."),
  });

  return (
    <View style={styles.shareBlock}>
      <Pressable
        onPress={() =>
          Alert.alert(
            "Salir del sobre",
            "Dejarás de ver este sobre compartido y tus gastos se desvinculan (no se borran).",
            [
              { text: "Cancelar", style: "cancel" },
              {
                text: "Salir",
                style: "destructive",
                onPress: () => leave.mutate(),
              },
            ],
          )
        }
        disabled={leave.isPending}
        style={({ pressed }) => [styles.leaveBtn, pressed && { opacity: 0.7 }]}
      >
        <Feather name="log-out" size={14} color={Colors.expense} />
        <Text style={styles.leaveBtnText}>Salir del sobre compartido</Text>
      </Pressable>
    </View>
  );
}

function ChildRow({
  item,
  onPress,
}: {
  item: EnvelopeSummaryItem;
  onPress: () => void;
}) {
  const { remaining, fraction, low } = envelopeProgress(item);
  const color = ENVELOPE_CLASS_COLORS[item.envelope_class];
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.childRow, pressed && { opacity: 0.6 }]}
    >
      <View style={styles.childMeta}>
        <Text style={styles.childName} numberOfLines={1}>
          {item.name}
        </Text>
        <Text style={[styles.childAmount, low && { color: Colors.expense }]}>
          {formatMoney(Math.max(0, remaining), item.currency)} de{" "}
          {formatMoney(item.limit_amount, item.currency)}
        </Text>
        <Feather name="chevron-right" size={16} color={Colors.textMuted} />
      </View>
      <View style={styles.childBarTrack}>
        <View
          style={[
            styles.barFill,
            { width: `${Math.round(fraction * 100)}%` as `${number}%` },
            { backgroundColor: low ? Colors.expense : color },
          ]}
        />
      </View>
    </Pressable>
  );
}

function ExpenseRow({
  tx,
  envelopeId,
  onToggle,
  pending,
}: {
  tx: TransactionResponse;
  envelopeId: string;
  onToggle: (assigned: boolean) => void;
  pending: boolean;
}) {
  // Rows assigned to ANOTHER envelope never reach this component (Phase 7f
  // moved them to the "En otros sobres" section below the list).
  const assignedHere = tx.envelope_id === envelopeId;
  const title = tx.merchant || tx.category || "Gasto";
  return (
    <Pressable
      onPress={() => !pending && onToggle(assignedHere)}
      style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}
    >
      <Feather
        name={assignedHere ? "check-circle" : "circle"}
        size={20}
        color={assignedHere ? Colors.accent : Colors.border}
      />
      <View style={styles.rowMeta}>
        <Text style={styles.rowTitle} numberOfLines={1}>
          {title}
        </Text>
        <Text style={styles.rowSub}>{formatDate(tx.transaction_date)}</Text>
      </View>
      <Text style={styles.rowAmount}>
        {formatMoney(Math.abs(tx.amount), tx.currency)}
      </Text>
    </Pressable>
  );
}

function OtherEnvelopeRow({
  tx,
  envelopeName,
  envelopeColor,
  onMove,
  pending,
}: {
  tx: TransactionResponse;
  envelopeName: string;
  envelopeColor: string;
  onMove: () => void;
  pending: boolean;
}) {
  const title = tx.merchant || tx.category || "Gasto";
  return (
    <View style={styles.row}>
      <View style={styles.rowMeta}>
        <Text style={styles.rowTitle} numberOfLines={1}>
          {title}
        </Text>
        <View style={styles.otherPillLine}>
          <View
            style={[styles.otherPill, { backgroundColor: envelopeColor + "22" }]}
          >
            <Feather name="inbox" size={10} color={envelopeColor} />
            <Text
              style={[styles.otherPillText, { color: envelopeColor }]}
              numberOfLines={1}
            >
              {envelopeName}
            </Text>
          </View>
          <Text style={styles.rowSub}>{formatDate(tx.transaction_date)}</Text>
        </View>
      </View>
      <View style={styles.otherRight}>
        <Text style={styles.rowAmount}>
          {formatMoney(Math.abs(tx.amount), tx.currency)}
        </Text>
        <Pressable
          onPress={() => !pending && onMove()}
          disabled={pending}
          style={({ pressed }) => [styles.moveBtn, pressed && { opacity: 0.7 }]}
        >
          <Text style={styles.moveBtnText}>Mover aquí</Text>
        </Pressable>
      </View>
    </View>
  );
}

function formatDate(iso: string): string {
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("es-CR", { day: "numeric", month: "short" });
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Colors.bg },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    gap: Spacing.sm,
  },
  headerTitle: {
    flex: 1,
    textAlign: "center",
    fontSize: FontSize.md,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  summaryCard: {
    marginHorizontal: Spacing.md,
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    padding: Spacing.md,
    gap: Spacing.sm,
  },
  summaryTop: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  classTag: { flexDirection: "row", alignItems: "center", gap: Spacing.xs },
  classDot: { width: 9, height: 9, borderRadius: 5 },
  classLabel: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    letterSpacing: 0.4,
    textTransform: "uppercase",
    color: Colors.textSecondary,
  },
  summaryAmount: {
    fontSize: FontSize.sm,
    fontWeight: "600",
    color: Colors.textPrimary,
    fontVariant: ["tabular-nums"],
  },
  barTrack: { height: 6, backgroundColor: Colors.border, borderRadius: 3 },
  barFill: { height: 6, borderRadius: 3 },
  summarySub: { fontSize: FontSize.xs, color: Colors.textMuted },
  coverBtn: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: 6,
    marginTop: 2,
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 5,
  },
  coverBtnText: { color: Colors.accent, fontSize: FontSize.xs, fontWeight: "600" },
  bulkBtn: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: 6,
    marginHorizontal: Spacing.md,
    marginTop: Spacing.xs,
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 5,
  },
  bulkBtnText: { color: Colors.accent, fontSize: FontSize.xs, fontWeight: "600" },

  subBtn: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: 4,
    marginHorizontal: Spacing.md,
    marginTop: Spacing.sm,
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 4,
  },
  subBtnText: { color: Colors.accent, fontSize: FontSize.sm, fontWeight: "600" },
  attachRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
  },
  attachName: { fontSize: FontSize.sm, color: Colors.textPrimary, fontWeight: "500" },
  attachMeta: { fontSize: FontSize.xs, color: Colors.textMuted, marginTop: 2 },
  attachBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 4,
  },
  attachBtnText: { color: Colors.accent, fontSize: FontSize.xs, fontWeight: "600" },
  detachBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 4,
  },
  detachBtnText: { color: Colors.textSecondary, fontSize: FontSize.xs, fontWeight: "600" },

  // ── sharing ──────────────────────────────────────────────────────────────────
  shareBlock: {
    marginHorizontal: Spacing.md,
    marginTop: Spacing.md,
    gap: Spacing.sm,
  },
  shareBtn: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: 6,
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.md,
    paddingVertical: 6,
  },
  shareBtnText: { color: Colors.accent, fontSize: FontSize.sm, fontWeight: "600" },
  codeBox: {
    backgroundColor: Colors.bgInput,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    gap: 4,
    alignItems: "center",
  },
  codeText: {
    fontSize: 26,
    letterSpacing: 6,
    fontFamily: "Menlo",
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  codeHint: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    textAlign: "center",
  },
  membersWrap: { gap: Spacing.xs },
  membersTitle: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    letterSpacing: 0.4,
    textTransform: "uppercase",
    color: Colors.textMuted,
  },
  memberRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: Spacing.sm,
    paddingVertical: 4,
  },
  memberName: {
    flex: 1,
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
    fontWeight: "500",
  },
  leaveBtn: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: 6,
    borderWidth: 1,
    borderColor: Colors.expense,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.md,
    paddingVertical: 6,
  },
  leaveBtnText: { color: Colors.expense, fontSize: FontSize.sm, fontWeight: "600" },

  headerBtn: { padding: 6 },
  childRow: {
    marginHorizontal: Spacing.md,
    marginTop: Spacing.sm,
    gap: Spacing.xs,
  },
  childMeta: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.xs,
  },
  childName: {
    flex: 1,
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
    fontWeight: "500",
  },
  childAmount: {
    fontSize: FontSize.xs,
    color: Colors.textSecondary,
    fontVariant: ["tabular-nums"],
  },
  childBarTrack: { height: 5, backgroundColor: Colors.border, borderRadius: 3 },

  listTitle: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    letterSpacing: 0.4,
    textTransform: "uppercase",
    color: Colors.textMuted,
    paddingHorizontal: Spacing.md,
    marginTop: Spacing.lg,
    marginBottom: Spacing.xs,
  },
  listContent: { paddingHorizontal: Spacing.md, paddingBottom: Spacing.xl },
  muted: {
    color: Colors.textMuted,
    fontSize: FontSize.sm,
    textAlign: "center",
    marginTop: Spacing.xl,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    paddingVertical: Spacing.sm + 2,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
  },
  rowMeta: { flex: 1 },
  rowTitle: { fontSize: FontSize.sm, color: Colors.textPrimary, fontWeight: "500" },
  rowSub: { fontSize: FontSize.xs, color: Colors.textMuted, marginTop: 1 },
  rowAmount: {
    fontSize: FontSize.sm,
    color: Colors.expense,
    fontWeight: "500",
    fontVariant: ["tabular-nums"],
  },

  // Phase 7f — "En otros sobres" section.
  othersSection: { marginTop: Spacing.lg },
  othersToggle: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: Spacing.xs,
  },
  othersToggleText: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    letterSpacing: 0.4,
    textTransform: "uppercase",
    color: Colors.textSecondary,
  },
  othersHint: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    marginBottom: Spacing.xs,
  },
  otherPillLine: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.xs,
    marginTop: 3,
  },
  otherPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderRadius: Radius.sm,
    paddingHorizontal: 6,
    paddingVertical: 2,
    maxWidth: 160,
  },
  otherPillText: {
    fontSize: FontSize.xs,
    fontWeight: "700",
  },
  otherRight: {
    alignItems: "flex-end",
    gap: 4,
  },
  moveBtn: {
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 3,
  },
  moveBtnText: {
    color: Colors.accent,
    fontSize: FontSize.xs,
    fontWeight: "600",
  },
});
