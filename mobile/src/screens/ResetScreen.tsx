/**
 * «Empezar de cero» — reset flows (fix pack B4).
 *
 * Two options, A recommended:
 *   A. Reiniciar saldos (non-destructive): re-anchor every account to its real
 *      balance today. History intact — reuses the reconciliation machinery.
 *   B. Borrar historial (destructive): after a typed confirmation, wipe the
 *      movements + derived records + anchors, then land in the SAME balance
 *      form (A) to set fresh starting balances.
 *
 * The balance-entry form is shared by both paths (one implementation).
 */
import { useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useNavigation } from "@react-navigation/native";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchAccounts,
  resetBalances,
  wipeHistory,
  type AccountResponse,
  type ResetBalanceItem,
} from "../api/accounts";
import { AmountInput } from "../components/fields/AmountInput";
import { formatMoney } from "../lib/format";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";

const WIPE_PHRASE = "BORRAR HISTORIAL";

type Step = "choose" | "balances" | "wipeConfirm";

export function ResetScreen() {
  const nav = useNavigation();
  const qc = useQueryClient();
  const [step, setStep] = useState<Step>("choose");

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ["accounts"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
    qc.invalidateQueries({ queryKey: ["calendar"] });
    qc.invalidateQueries({ queryKey: ["envelopes"] });
    qc.invalidateQueries({ queryKey: ["debts"] });
    qc.invalidateQueries({ queryKey: ["goals"] });
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <View style={styles.header}>
        <Pressable
          onPress={() => (step === "choose" ? nav.goBack() : setStep("choose"))}
          hitSlop={10}
          style={({ pressed }) => pressed && { opacity: 0.6 }}
        >
          <Feather name="chevron-left" size={24} color={Colors.textPrimary} />
        </Pressable>
        <Text style={styles.headerTitle}>Empezar de cero</Text>
        <View style={{ width: 24 }} />
      </View>

      {step === "choose" && (
        <ChooseView
          onPickA={() => setStep("balances")}
          onPickB={() => setStep("wipeConfirm")}
        />
      )}
      {step === "wipeConfirm" && (
        <WipeConfirmView
          onCancel={() => setStep("choose")}
          onWiped={() => {
            invalidateAll();
            setStep("balances");
          }}
        />
      )}
      {step === "balances" && (
        <BalanceForm
          onDone={() => {
            invalidateAll();
            Alert.alert("Listo", "Tus saldos quedaron actualizados.");
            nav.goBack();
          }}
        />
      )}
    </SafeAreaView>
  );
}

// ── option chooser ───────────────────────────────────────────────────────────

function ChooseView({
  onPickA,
  onPickB,
}: {
  onPickA: () => void;
  onPickB: () => void;
}) {
  return (
    <ScrollView contentContainerStyle={styles.content}>
      <Text style={styles.intro}>
        ¿Querés volver a empezar? Elegí cómo. Lo recomendado no borra nada.
      </Text>

      <Pressable
        onPress={onPickA}
        style={({ pressed }) => [styles.optionCard, pressed && { opacity: 0.85 }]}
      >
        <View style={styles.optionHead}>
          <Feather name="refresh-cw" size={18} color={Colors.accent} />
          <Text style={styles.optionTitle}>Reiniciar saldos</Text>
          <Text style={styles.recommendedTag}>Recomendado</Text>
        </View>
        <Text style={styles.optionBody}>
          Ponés el saldo real de cada cuenta hoy y todo se recalcula desde ahí.
          No se borra ningún movimiento — tu historial queda intacto.
        </Text>
      </Pressable>

      <Pressable
        onPress={onPickB}
        style={({ pressed }) => [
          styles.optionCard,
          styles.optionCardDanger,
          pressed && { opacity: 0.85 },
        ]}
      >
        <View style={styles.optionHead}>
          <Feather name="trash-2" size={18} color={Colors.expense} />
          <Text style={[styles.optionTitle, { color: Colors.expense }]}>
            Borrar historial
          </Text>
        </View>
        <Text style={styles.optionBody}>
          Elimina todos tus movimientos y pagos registrados (no se puede
          deshacer). Se conservan tus cuentas, deudas, sobres y metas. Al
          terminar, ponés tus saldos de arranque.
        </Text>
      </Pressable>
    </ScrollView>
  );
}

// ── destructive confirm ──────────────────────────────────────────────────────

function WipeConfirmView({
  onCancel,
  onWiped,
}: {
  onCancel: () => void;
  onWiped: () => void;
}) {
  const [text, setText] = useState("");
  const matches = text.trim().toUpperCase() === WIPE_PHRASE;

  const mutation = useMutation({
    mutationFn: () => wipeHistory(text.trim()),
    onSuccess: onWiped,
    onError: () =>
      Alert.alert("Error", "No se pudo borrar el historial. Intentá de nuevo."),
  });

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={{ flex: 1 }}
    >
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.warnCard}>
          <Feather name="alert-triangle" size={20} color={Colors.expense} />
          <Text style={styles.warnText}>
            Esto elimina TODOS tus movimientos, pagos, transferencias y anclas de
            saldo. No se puede deshacer. Tus cuentas, deudas, sobres y metas se
            conservan.
          </Text>
        </View>
        <Text style={styles.confirmLabel}>
          Escribí <Text style={styles.phrase}>{WIPE_PHRASE}</Text> para confirmar:
        </Text>
        <TextInput
          value={text}
          onChangeText={setText}
          autoCapitalize="characters"
          autoCorrect={false}
          placeholder={WIPE_PHRASE}
          placeholderTextColor={Colors.textMuted}
          style={styles.input}
        />
        <Pressable
          onPress={() => matches && mutation.mutate()}
          disabled={!matches || mutation.isPending}
          style={({ pressed }) => [
            styles.dangerBtn,
            (!matches || mutation.isPending) && styles.dangerBtnDisabled,
            pressed && matches && { opacity: 0.85 },
          ]}
        >
          {mutation.isPending ? (
            <ActivityIndicator color={Colors.textOnDark} />
          ) : (
            <Text style={styles.dangerBtnText}>Borrar mi historial</Text>
          )}
        </Pressable>
        <Pressable onPress={onCancel} style={styles.cancelBtn}>
          <Text style={styles.cancelBtnText}>Cancelar</Text>
        </Pressable>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

// ── shared balance-entry form (Option A, and the tail of Option B) ────────────

function BalanceForm({ onDone }: { onDone: () => void }) {
  const { data: accounts, isLoading } = useQuery({
    queryKey: ["accounts", "active"],
    queryFn: () => fetchAccounts(false),
  });

  // Fund accounts only — credit balances are movement-driven (not re-anchored).
  const fundAccounts = useMemo(
    () => (accounts ?? []).filter((a) => a.account_type !== "credit"),
    [accounts],
  );

  const [values, setValues] = useState<Record<string, string>>({});
  const valueFor = (a: AccountResponse) =>
    values[a.id] ?? (a.current_balance ?? "0");

  const mutation = useMutation({
    mutationFn: () => {
      const items: ResetBalanceItem[] = fundAccounts.map((a) => ({
        account_id: a.id,
        value: valueFor(a).trim() || "0",
      }));
      return resetBalances(items);
    },
    onSuccess: onDone,
    onError: () =>
      Alert.alert("Error", "No se pudieron actualizar los saldos."),
  });

  if (isLoading) {
    return (
      <View style={styles.loading}>
        <ActivityIndicator color={Colors.accent} />
      </View>
    );
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={{ flex: 1 }}
    >
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
      >
        <Text style={styles.intro}>
          Escribí el saldo real de cada cuenta hoy. Ajustamos todo a partir de
          estos valores.
        </Text>
        {fundAccounts.map((a) => (
          <View key={a.id} style={styles.balRow}>
            <View style={styles.balMeta}>
              <Text style={styles.balName}>{a.name}</Text>
              <Text style={styles.balHint}>
                Actual: {formatMoney(Number(a.current_balance ?? 0), a.currency)}
              </Text>
            </View>
            <AmountInput
              value={valueFor(a)}
              onChangeValue={(raw) =>
                setValues((prev) => ({ ...prev, [a.id]: raw }))
              }
              style={styles.balInput}
              placeholder="0"
              placeholderTextColor={Colors.textMuted}
            />
          </View>
        ))}
        {fundAccounts.length === 0 && (
          <Text style={styles.intro}>No tenés cuentas para ajustar.</Text>
        )}
        <Pressable
          onPress={() => fundAccounts.length > 0 && mutation.mutate()}
          disabled={fundAccounts.length === 0 || mutation.isPending}
          style={({ pressed }) => [
            styles.primaryBtn,
            (fundAccounts.length === 0 || mutation.isPending) &&
              styles.dangerBtnDisabled,
            pressed && { opacity: 0.85 },
          ]}
        >
          {mutation.isPending ? (
            <ActivityIndicator color={Colors.textOnDark} />
          ) : (
            <Text style={styles.primaryBtnText}>Guardar saldos</Text>
          )}
        </Pressable>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Colors.bg },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    backgroundColor: Colors.bgCard,
  },
  headerTitle: { fontSize: FontSize.md, fontWeight: "700", color: Colors.textPrimary },
  content: { padding: Spacing.md, gap: Spacing.md, paddingBottom: Spacing.xl * 2 },
  intro: { fontSize: FontSize.sm, color: Colors.textSecondary, lineHeight: 20 },

  optionCard: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    gap: Spacing.xs,
    ...CardShadow,
  },
  optionCardDanger: { borderColor: Colors.expense },
  optionHead: { flexDirection: "row", alignItems: "center", gap: Spacing.sm },
  optionTitle: {
    fontSize: FontSize.md,
    fontWeight: "700",
    color: Colors.textPrimary,
    flex: 1,
  },
  recommendedTag: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    color: Colors.accent,
    backgroundColor: Colors.accentBg,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 2,
    overflow: "hidden",
  },
  optionBody: { fontSize: FontSize.sm, color: Colors.textSecondary, lineHeight: 20 },

  warnCard: {
    flexDirection: "row",
    gap: Spacing.sm,
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.expense,
    borderRadius: Radius.md,
    padding: Spacing.md,
  },
  warnText: { flex: 1, fontSize: FontSize.sm, color: Colors.textSecondary, lineHeight: 20 },
  confirmLabel: { fontSize: FontSize.sm, color: Colors.textSecondary },
  phrase: { fontWeight: "700", color: Colors.textPrimary },
  input: {
    backgroundColor: Colors.bgInput,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    fontSize: FontSize.md,
    color: Colors.textPrimary,
  },
  dangerBtn: {
    backgroundColor: Colors.expense,
    borderRadius: Radius.md,
    paddingVertical: Spacing.md,
    alignItems: "center",
  },
  dangerBtnDisabled: { opacity: 0.5 },
  dangerBtnText: { color: Colors.textOnDark, fontSize: FontSize.md, fontWeight: "700" },
  cancelBtn: { alignItems: "center", paddingVertical: Spacing.sm },
  cancelBtnText: { color: Colors.textSecondary, fontSize: FontSize.md },

  balRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.md,
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    padding: Spacing.md,
  },
  balMeta: { flex: 1, gap: 2 },
  balName: { fontSize: FontSize.md, fontWeight: "600", color: Colors.textPrimary },
  balHint: { fontSize: FontSize.xs, color: Colors.textMuted },
  balInput: {
    width: 130,
    backgroundColor: Colors.bgInput,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.sm,
    paddingVertical: Spacing.sm,
    fontSize: FontSize.md,
    color: Colors.textPrimary,
    textAlign: "right",
  },
  primaryBtn: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.md,
    alignItems: "center",
    marginTop: Spacing.sm,
  },
  primaryBtnText: { color: Colors.textOnDark, fontSize: FontSize.md, fontWeight: "700" },
  loading: { flex: 1, alignItems: "center", justifyContent: "center" },
});
