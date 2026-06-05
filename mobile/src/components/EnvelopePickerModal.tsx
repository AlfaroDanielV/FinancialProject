/**
 * Envelope budgeting (Sobres) — reusable picker bottom sheet.
 *
 * Lists the caller's active envelopes grouped by class (Necesidades / Gustos /
 * Ahorro / Inversión) plus a "Sin sobre" row to clear the assignment. Calls
 * `onSelect(envelopeId | null)` and closes — it does NOT mutate anything
 * itself, so it's reusable both for in-chat assignment (Chat) and for the
 * edit modal (TransactionEditModal) which stores the choice in local state.
 */
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
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
  type EnvelopeClass,
  type EnvelopeResponse,
  fetchEnvelopes,
} from "../api/envelopes";
import { formatMoney } from "../lib/format";
import { Colors, FontSize, Radius, Spacing } from "../theme";

interface Props {
  visible: boolean;
  currentEnvelopeId?: string | null;
  onClose: () => void;
  onSelect: (envelopeId: string | null) => void;
}

export function EnvelopePickerModal({
  visible,
  currentEnvelopeId,
  onClose,
  onSelect,
}: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["envelopes", "active"],
    queryFn: () => fetchEnvelopes(false),
    enabled: visible,
  });

  const grouped = groupByClass(data ?? []);

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.overlay}>
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.title}>Asignar a un sobre</Text>

          {isLoading ? (
            <View style={styles.center}>
              <ActivityIndicator color={Colors.accent} />
            </View>
          ) : isError ? (
            <Text style={styles.muted}>No se pudieron cargar los sobres.</Text>
          ) : (data ?? []).length === 0 ? (
            <Text style={styles.muted}>
              No tenés sobres todavía. Creá uno en la pestaña Inicio.
            </Text>
          ) : (
            <ScrollView
              style={styles.body}
              showsVerticalScrollIndicator={false}
              keyboardShouldPersistTaps="handled"
            >
              {/* Clear assignment */}
              <Pressable
                onPress={() => onSelect(null)}
                style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
              >
                <View style={[styles.dot, { backgroundColor: Colors.border }]} />
                <Text style={styles.rowName}>Sin sobre</Text>
                {currentEnvelopeId == null && (
                  <Feather name="check" size={18} color={Colors.accent} />
                )}
              </Pressable>

              {ENVELOPE_CLASS_ORDER.filter((c) => grouped[c]?.length).map((cls) => (
                <View key={cls}>
                  <Text style={[styles.classHeader, { color: ENVELOPE_CLASS_COLORS[cls] }]}>
                    {ENVELOPE_CLASS_LABELS[cls]}
                  </Text>
                  {grouped[cls].map((env) => (
                    <Pressable
                      key={env.id}
                      onPress={() => onSelect(env.id)}
                      style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
                    >
                      <View
                        style={[
                          styles.dot,
                          { backgroundColor: ENVELOPE_CLASS_COLORS[cls] },
                        ]}
                      />
                      <View style={styles.rowTextWrap}>
                        <Text style={styles.rowName}>{env.name}</Text>
                        <Text style={styles.rowSub}>
                          Límite {formatMoney(env.limit_amount, env.currency)}
                        </Text>
                      </View>
                      {currentEnvelopeId === env.id && (
                        <Feather name="check" size={18} color={Colors.accent} />
                      )}
                    </Pressable>
                  ))}
                </View>
              ))}
            </ScrollView>
          )}

          <Pressable
            onPress={onClose}
            style={({ pressed }) => [styles.cancel, pressed && { opacity: 0.75 }]}
          >
            <Text style={styles.cancelText}>Cerrar</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

function groupByClass(
  envelopes: EnvelopeResponse[]
): Record<EnvelopeClass, EnvelopeResponse[]> {
  const out = { needs: [], wants: [], savings: [], investing: [] } as Record<
    EnvelopeClass,
    EnvelopeResponse[]
  >;
  for (const env of envelopes) {
    (out[env.envelope_class] ??= []).push(env);
  }
  return out;
}

const styles = StyleSheet.create({
  overlay: { flex: 1, justifyContent: "flex-end" },
  backdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(0,0,0,0.35)" },
  sheet: {
    backgroundColor: Colors.bg,
    borderTopLeftRadius: Radius.lg,
    borderTopRightRadius: Radius.lg,
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.sm,
    paddingBottom: Spacing.xl,
    maxHeight: "82%",
  },
  handle: {
    alignSelf: "center",
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: Colors.border,
    marginBottom: Spacing.sm,
  },
  title: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
    marginBottom: Spacing.sm,
  },
  body: { flexGrow: 0 },
  center: { paddingVertical: Spacing.xl, alignItems: "center" },
  muted: {
    color: Colors.textMuted,
    fontSize: FontSize.sm,
    paddingVertical: Spacing.lg,
    textAlign: "center",
  },
  classHeader: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    letterSpacing: 0.4,
    textTransform: "uppercase",
    marginTop: Spacing.md,
    marginBottom: Spacing.xs,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: Spacing.sm + 2,
    gap: Spacing.sm,
  },
  rowPressed: { opacity: 0.6 },
  rowTextWrap: { flex: 1 },
  rowName: { flex: 1, fontSize: FontSize.md, color: Colors.textPrimary },
  rowSub: { fontSize: FontSize.xs, color: Colors.textMuted, marginTop: 1 },
  dot: { width: 10, height: 10, borderRadius: 5 },
  cancel: {
    marginTop: Spacing.md,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingVertical: Spacing.md,
    alignItems: "center",
  },
  cancelText: { color: Colors.textSecondary, fontSize: FontSize.md, fontWeight: "500" },
});
