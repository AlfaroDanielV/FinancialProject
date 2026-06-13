/**
 * Phase 6f B8 — Create account form.
 *
 * Fields: name, account_type (segmented picker), currency (CRC/USD toggle),
 * initial_balance (numeric input). Saves via POST /accounts.
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
import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { createAccount, ACCOUNT_TYPE_LABELS } from "../api/accounts";
import { AmountInput } from "../components/fields/AmountInput";
import { Colors, FontSize, Radius, Spacing } from "../theme";
import type { AccountsStackParamList } from "../navigation/AccountsNavigator";

type Nav = NativeStackNavigationProp<AccountsStackParamList, "AccountCreate">;

const ACCOUNT_TYPES = ["checking", "savings", "credit", "investment"] as const;
type AccountType = (typeof ACCOUNT_TYPES)[number];

export function AccountCreateScreen() {
  const nav = useNavigation<Nav>();
  const qc = useQueryClient();

  const [name, setName] = useState("");
  const [accountType, setAccountType] = useState<AccountType>("checking");
  const [currency, setCurrency] = useState<"CRC" | "USD">("CRC");
  const [initialBalance, setInitialBalance] = useState("");

  const mutation = useMutation({
    mutationFn: () =>
      createAccount({
        name: name.trim(),
        account_type: accountType,
        currency,
        initial_balance: initialBalance.trim() || "0",
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      nav.goBack();
    },
    onError: () => {
      Alert.alert("Error", "No se pudo crear la cuenta. Revisá los datos e intentá de nuevo.");
    },
  });

  const canSubmit = name.trim().length > 0 && !mutation.isPending;

  const onSubmit = () => {
    if (!canSubmit) return;
    const balNum = parseFloat(initialBalance.replace(",", "."));
    if (initialBalance.trim() !== "" && isNaN(balNum)) {
      Alert.alert("Saldo inválido", "Ingresá un número válido para el saldo inicial.");
      return;
    }
    mutation.mutate();
  };

  return (
    <SafeAreaView style={styles.safe} edges={["bottom"]}>
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
      >
        {/* ── Name ────────────────────────────────────────────────────────── */}
        <View style={styles.fieldGroup}>
          <Text style={styles.label}>Nombre</Text>
          <TextInput
            style={styles.input}
            value={name}
            onChangeText={setName}
            placeholder="Ej: BAC Colones, BNCR Ahorros"
            placeholderTextColor={Colors.textMuted}
            autoFocus
            maxLength={80}
            returnKeyType="next"
          />
        </View>

        {/* ── Account type ─────────────────────────────────────────────────── */}
        <View style={styles.fieldGroup}>
          <Text style={styles.label}>Tipo de cuenta</Text>
          <View style={styles.segmented}>
            {ACCOUNT_TYPES.map((t) => (
              <Pressable
                key={t}
                onPress={() => {
                  // Phase 7b B3: credit cards have their own creation flow
                  // (terms + PDF upload) — redirect instead of the bare form.
                  if (t === "credit") {
                    nav.replace("CardCreate", undefined);
                    return;
                  }
                  setAccountType(t);
                }}
                style={({ pressed }) => [
                  styles.segmentItem,
                  accountType === t && styles.segmentItemActive,
                  pressed && { opacity: 0.8 },
                ]}
              >
                <Text
                  style={[
                    styles.segmentLabel,
                    accountType === t && styles.segmentLabelActive,
                  ]}
                >
                  {ACCOUNT_TYPE_LABELS[t]}
                </Text>
              </Pressable>
            ))}
          </View>
          {accountType === "savings" && (
            <Text style={styles.hint}>
              💡 El dinero en cuentas de ahorro no se cuenta en tu disponible
              del mes — lo tratamos como plata apartada para tus metas.
            </Text>
          )}
        </View>

        {/* ── Currency ─────────────────────────────────────────────────────── */}
        <View style={styles.fieldGroup}>
          <Text style={styles.label}>Moneda</Text>
          <View style={styles.currencyRow}>
            {(["CRC", "USD"] as const).map((c) => (
              <Pressable
                key={c}
                onPress={() => setCurrency(c)}
                style={({ pressed }) => [
                  styles.currencyBtn,
                  currency === c && styles.currencyBtnActive,
                  pressed && { opacity: 0.8 },
                ]}
              >
                <Text
                  style={[
                    styles.currencyLabel,
                    currency === c && styles.currencyLabelActive,
                  ]}
                >
                  {c === "CRC" ? "₡ Colones" : "$ Dólares"}
                </Text>
              </Pressable>
            ))}
          </View>
        </View>

        {/* ── Initial balance ──────────────────────────────────────────────── */}
        <View style={styles.fieldGroup}>
          <Text style={styles.label}>Saldo inicial</Text>
          <AmountInput
            style={styles.input}
            value={initialBalance}
            onChangeValue={setInitialBalance}
            placeholder="0"
            placeholderTextColor={Colors.textMuted}
            returnKeyType="done"
            onSubmitEditing={onSubmit}
          />
          <Text style={styles.hint}>
            Dejá en 0 si querés empezar desde cero o si la cuenta ya tiene
            transacciones en el sistema.
          </Text>
        </View>

        {/* ── Submit ───────────────────────────────────────────────────────── */}
        <Pressable
          onPress={onSubmit}
          disabled={!canSubmit}
          style={({ pressed }) => [
            styles.submitBtn,
            !canSubmit && styles.submitDisabled,
            pressed && canSubmit && { opacity: 0.85 },
          ]}
        >
          {mutation.isPending ? (
            <ActivityIndicator color={Colors.textOnDark} />
          ) : (
            <Text style={styles.submitLabel}>Crear cuenta</Text>
          )}
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },
  scroll: { flex: 1 },
  content: {
    padding: Spacing.md,
    gap: Spacing.lg,
    paddingBottom: Spacing.xl,
  },

  // ── field ─────────────────────────────────────────────────────────────────
  fieldGroup: {
    gap: Spacing.xs,
  },
  label: {
    fontSize: FontSize.sm,
    fontWeight: "600",
    color: Colors.textSecondary,
    letterSpacing: 0.3,
  },
  hint: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    lineHeight: 17,
    marginTop: 2,
  },
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

  // ── segmented ─────────────────────────────────────────────────────────────
  segmented: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: Spacing.sm,
  },
  segmentItem: {
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    backgroundColor: Colors.bgCard,
  },
  segmentItemActive: {
    borderColor: Colors.accent,
    backgroundColor: Colors.accentBg,
  },
  segmentLabel: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontWeight: "500",
  },
  segmentLabelActive: {
    color: Colors.accent,
    fontWeight: "600",
  },

  // ── currency ──────────────────────────────────────────────────────────────
  currencyRow: {
    flexDirection: "row",
    gap: Spacing.sm,
  },
  currencyBtn: {
    flex: 1,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingVertical: Spacing.sm + 2,
    alignItems: "center",
    backgroundColor: Colors.bgCard,
  },
  currencyBtnActive: {
    borderColor: Colors.accent,
    backgroundColor: Colors.accentBg,
  },
  currencyLabel: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontWeight: "500",
  },
  currencyLabelActive: {
    color: Colors.accent,
    fontWeight: "600",
  },

  // ── submit ────────────────────────────────────────────────────────────────
  submitBtn: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.md,
    alignItems: "center",
    marginTop: Spacing.sm,
  },
  submitDisabled: {
    backgroundColor: Colors.border,
  },
  submitLabel: {
    fontSize: FontSize.md,
    fontWeight: "600",
    color: Colors.textOnDark,
  },
});
