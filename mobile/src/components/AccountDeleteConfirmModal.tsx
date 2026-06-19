/**
 * Phase 7b B2 — typed-name confirmation for the PERMANENT account delete.
 *
 * Shows the delete-impact counts (what gets removed vs detached) and requires
 * typing the exact account name before the destructive button enables.
 * Keyboard scaffold copied from TransactionEditModal.
 */
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Feather } from "@expo/vector-icons";
import { useMutation, useQuery } from "@tanstack/react-query";

import {
  fetchDeleteImpact,
  hardDeleteAccount,
  type AccountResponse,
} from "../api/accounts";
import { Colors, FontSize, Radius, Spacing } from "../theme";

interface Props {
  visible: boolean;
  account: AccountResponse;
  onClose: () => void;
  onDeleted: () => void;
}

export function AccountDeleteConfirmModal({
  visible,
  account,
  onClose,
  onDeleted,
}: Props) {
  const [typedName, setTypedName] = useState("");

  useEffect(() => {
    if (visible) setTypedName("");
  }, [visible]);

  const { data: impact, isLoading } = useQuery({
    queryKey: ["accountDeleteImpact", account.id],
    queryFn: () => fetchDeleteImpact(account.id),
    enabled: visible,
  });

  const mutation = useMutation({
    mutationFn: () => hardDeleteAccount(account.id, typedName),
    onSuccess: () => onDeleted(),
  });

  const nameMatches = typedName === account.name;
  const txCount = impact?.transactions ?? 0;
  const detached =
    (impact?.debts_detached ?? 0) +
    (impact?.bills_detached ?? 0) +
    (impact?.goals_detached ?? 0);

  const serverError =
    (mutation.error as { response?: { data?: { detail?: unknown } } } | null)
      ?.response?.data?.detail;
  const errorText =
    (typeof serverError === "string" ? serverError : null) ??
    (mutation.isError ? "No se pudo eliminar. Intentá de nuevo." : null);

  return (
    <Modal
      visible={visible}
      animationType="slide"
      transparent
      onRequestClose={onClose}
    >
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={styles.overlay}
      >
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <View style={styles.titleRow}>
            <Feather name="alert-triangle" size={20} color={Colors.expense} />
            <Text style={styles.title}>Eliminar cuenta definitivamente</Text>
          </View>
          <ScrollView
            style={styles.body}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
          >
            {isLoading ? (
              <ActivityIndicator color={Colors.accent} style={{ marginVertical: 20 }} />
            ) : (
              <View style={styles.impactBox}>
                <Text style={styles.impactLine}>
                  Se {txCount === 1 ? "eliminará" : "eliminarán"}{" "}
                  <Text style={styles.impactStrong}>
                    {txCount} {txCount === 1 ? "movimiento" : "movimientos"}
                  </Text>{" "}
                  para siempre
                  {impact && impact.transfers > 0
                    ? `, incluyendo ${impact.transfers} ${
                        impact.transfers === 1 ? "transferencia" : "transferencias"
                      }`
                    : ""}
                  . Esto no se puede deshacer.
                </Text>
                {detached > 0 && (
                  <Text style={styles.impactLine}>
                    Tus deudas, gastos fijos y metas vinculadas a esta cuenta
                    se desvinculan, no se borran.
                  </Text>
                )}
              </View>
            )}
            <Text style={styles.fieldLabel}>
              Escribí el nombre exacto de la cuenta para confirmar:
            </Text>
            <TextInput
              value={typedName}
              onChangeText={setTypedName}
              style={styles.input}
              placeholder={account.name}
              placeholderTextColor={Colors.textMuted}
              autoCapitalize="none"
              autoCorrect={false}
            />
            {errorText != null && <Text style={styles.error}>{errorText}</Text>}
          </ScrollView>
          <View style={styles.actions}>
            <Pressable
              onPress={onClose}
              disabled={mutation.isPending}
              style={({ pressed }) => [
                styles.btn,
                styles.btnCancel,
                pressed && { opacity: 0.75 },
              ]}
            >
              <Text style={styles.btnCancelText}>Cancelar</Text>
            </Pressable>
            <Pressable
              onPress={() => mutation.mutate()}
              disabled={!nameMatches || mutation.isPending}
              style={({ pressed }) => [
                styles.btn,
                styles.btnDelete,
                (!nameMatches || mutation.isPending) && { opacity: 0.4 },
                pressed && { opacity: 0.85 },
              ]}
            >
              {mutation.isPending ? (
                <ActivityIndicator color={Colors.bgCard} size="small" />
              ) : (
                <Text style={styles.btnDeleteText}>Eliminar para siempre</Text>
              )}
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
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
    maxHeight: "88%",
  },
  handle: {
    alignSelf: "center",
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: Colors.border,
    marginBottom: Spacing.sm,
  },
  titleRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.xs,
    marginBottom: Spacing.md,
  },
  title: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.expense,
    flexShrink: 1,
  },
  body: { flexGrow: 0 },
  impactBox: {
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderRadius: Radius.md,
    padding: Spacing.md,
    marginBottom: Spacing.md,
    gap: Spacing.sm,
  },
  impactLine: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    lineHeight: 20,
  },
  impactStrong: { fontWeight: "700", color: Colors.expense },
  fieldLabel: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    fontWeight: "500",
    letterSpacing: 0.3,
    marginBottom: 4,
  },
  input: {
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    fontSize: FontSize.md,
    color: Colors.textPrimary,
    marginBottom: Spacing.sm,
  },
  error: {
    color: Colors.expense,
    fontSize: FontSize.sm,
    marginBottom: Spacing.sm,
  },
  actions: {
    flexDirection: "row",
    gap: Spacing.sm,
    marginTop: Spacing.sm,
  },
  btn: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: Radius.md,
    paddingVertical: Spacing.md,
  },
  btnCancel: {
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bg,
  },
  btnCancelText: {
    color: Colors.textSecondary,
    fontSize: FontSize.md,
    fontWeight: "500",
  },
  btnDelete: { backgroundColor: Colors.expense },
  btnDeleteText: { color: Colors.bgCard, fontSize: FontSize.md, fontWeight: "600" },
});
