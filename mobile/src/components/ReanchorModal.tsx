/**
 * Bank-reconciliation re-anchor sheet (A6/A7). The user states their REAL
 * current balance (read from their banking app); the backend appends an anchor
 * and records an informational "ajuste de reconciliación" for the gap, so the
 * displayed balance heals to the stated value in one step. Serves both the
 * "confirmá tu saldo" nudge (migrated accounts) and "corregí mi saldo".
 *
 * Keyboard scaffold copied from AccountEditModal (KeyboardAvoidingView +
 * ScrollView — bottom sheets with inputs render behind the keyboard otherwise).
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
import { useMutation } from "@tanstack/react-query";

import { setAccountAnchor, type AccountResponse } from "../api/accounts";
import { AmountInput } from "./fields/AmountInput";
import { Colors, FontSize, Radius, Spacing } from "../theme";

interface Props {
  visible: boolean;
  account: AccountResponse;
  onClose: () => void;
  /** Called after a successful re-anchor with the reconciled delta (signed),
   *  so the host can refresh balances and surface the result. */
  onSaved: (delta: number) => void;
}

export function ReanchorModal({ visible, account, onClose, onSaved }: Props) {
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (visible) {
      const cur = parseFloat(account.current_balance ?? "0");
      setValue(Number.isFinite(cur) ? String(cur) : "");
      setNote("");
      setError(null);
    }
  }, [visible, account]);

  const mutation = useMutation({
    mutationFn: () =>
      setAccountAnchor(account.id, value.trim(), note.trim() || undefined),
    onSuccess: (res) => onSaved(parseFloat(res.delta)),
  });

  const save = () => {
    const n = parseFloat(value.replace(",", "."));
    if (value.trim() === "" || isNaN(n)) {
      setError("Ingresá el saldo que te aparece en tu banco.");
      return;
    }
    setError(null);
    mutation.mutate();
  };

  const serverError =
    (mutation.error as { response?: { data?: { detail?: unknown } } } | null)
      ?.response?.data?.detail;
  const errorText =
    error ??
    (typeof serverError === "string" ? serverError : null) ??
    (mutation.isError ? "No se pudo guardar. Intentá de nuevo." : null);

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
          <Text style={styles.title}>Corregí tu saldo</Text>
          <ScrollView
            style={styles.body}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
          >
            <Text style={styles.help}>
              Abrí tu app del banco y escribí el saldo que te aparece HOY en
              «{account.name}». Ese número manda — yo ajusto la diferencia para
              que cuadre.
            </Text>
            <View style={styles.field}>
              <Text style={styles.fieldLabel}>
                Saldo real ({account.currency})
              </Text>
              <AmountInput
                style={styles.input}
                value={value}
                onChangeValue={setValue}
                placeholder="0"
                placeholderTextColor={Colors.textMuted}
                autoFocus
                returnKeyType="done"
                onSubmitEditing={save}
              />
            </View>
            <View style={styles.field}>
              <Text style={styles.fieldLabel}>Nota (opcional)</Text>
              <TextInput
                value={note}
                onChangeText={setNote}
                style={styles.input}
                placeholder="Ej: ajuste tras revisar el banco"
                placeholderTextColor={Colors.textMuted}
                maxLength={200}
              />
            </View>
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
              onPress={save}
              disabled={mutation.isPending}
              style={({ pressed }) => [
                styles.btn,
                styles.btnSave,
                mutation.isPending && { opacity: 0.6 },
                pressed && { opacity: 0.85 },
              ]}
            >
              {mutation.isPending ? (
                <ActivityIndicator color={Colors.bgCard} size="small" />
              ) : (
                <Text style={styles.btnSaveText}>Guardar saldo</Text>
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
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(0,0,0,0.35)",
  },
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
  title: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
    marginBottom: Spacing.sm,
  },
  help: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    lineHeight: 19,
    marginBottom: Spacing.md,
  },
  body: { flexGrow: 0 },
  field: { marginBottom: Spacing.md },
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
  btnSave: { backgroundColor: Colors.accent },
  btnSaveText: {
    color: Colors.bgCard,
    fontSize: FontSize.md,
    fontWeight: "600",
  },
});
