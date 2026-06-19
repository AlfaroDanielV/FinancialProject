/**
 * Phase 7b B2 — account edit (name + type). Currency and initial balance are
 * immutable post-create (backend silently drops them; we don't show them).
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
import { useMutation } from "@tanstack/react-query";

import {
  ACCOUNT_TYPE_LABELS,
  updateAccount,
  type AccountResponse,
  type AccountUpdate,
} from "../api/accounts";
import { Colors, FontSize, Radius, Spacing } from "../theme";

interface Props {
  visible: boolean;
  account: AccountResponse;
  onClose: () => void;
  onSaved: (updated: AccountResponse) => void;
}

const TYPES = Object.keys(ACCOUNT_TYPE_LABELS);

export function AccountEditModal({ visible, account, onClose, onSaved }: Props) {
  const [name, setName] = useState(account.name);
  const [accountType, setAccountType] = useState(account.account_type);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setName(account.name);
    setAccountType(account.account_type);
    setError(null);
  }, [account, visible]);

  const mutation = useMutation({
    mutationFn: (payload: AccountUpdate) => updateAccount(account.id, payload),
    onSuccess: (updated) => onSaved(updated),
  });

  const save = () => {
    if (!name.trim()) {
      setError("Ingresá un nombre para la cuenta.");
      return;
    }
    setError(null);
    mutation.mutate({ name: name.trim(), account_type: accountType });
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
          <Text style={styles.title}>Editar cuenta</Text>
          <ScrollView
            style={styles.body}
            keyboardShouldPersistTaps="handled"
            showsVerticalScrollIndicator={false}
          >
            <View style={styles.field}>
              <Text style={styles.fieldLabel}>Nombre</Text>
              <TextInput
                value={name}
                onChangeText={setName}
                style={styles.input}
                placeholder="BAC Corriente…"
                placeholderTextColor={Colors.textMuted}
                maxLength={80}
              />
            </View>
            <View style={styles.field}>
              <Text style={styles.fieldLabel}>Tipo</Text>
              <View style={styles.segmentRow}>
                {TYPES.map((t) => (
                  <Pressable
                    key={t}
                    onPress={() => setAccountType(t)}
                    style={({ pressed }) => [
                      styles.segment,
                      accountType === t && styles.segmentActive,
                      pressed && { opacity: 0.7 },
                    ]}
                  >
                    <Text
                      style={[
                        styles.segmentText,
                        accountType === t && styles.segmentTextActive,
                      ]}
                    >
                      {ACCOUNT_TYPE_LABELS[t]}
                    </Text>
                  </Pressable>
                ))}
              </View>
            </View>
            <Text style={styles.immutableNote}>
              La moneda y el saldo inicial no se pueden cambiar después de
              crear la cuenta.
            </Text>
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
                <Text style={styles.btnSaveText}>Guardar</Text>
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
  title: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
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
  segmentRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: Spacing.xs,
  },
  segment: {
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.sm + 2,
    paddingVertical: Spacing.xs + 2,
    backgroundColor: Colors.bgCard,
  },
  segmentActive: {
    borderColor: Colors.accent,
    backgroundColor: Colors.accentBg,
  },
  segmentText: { fontSize: FontSize.sm, color: Colors.textSecondary },
  segmentTextActive: { color: Colors.accent, fontWeight: "600" },
  immutableNote: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    marginBottom: Spacing.sm,
    lineHeight: 17,
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
  btnSaveText: { color: Colors.bgCard, fontSize: FontSize.md, fontWeight: "600" },
});
