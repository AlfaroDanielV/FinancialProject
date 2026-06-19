/**
 * Join a shared envelope ("Sobre compartido") with a 6-char code from its owner.
 * Mirrors the Login screen's code input (same alphabet, auto-submit at 6 chars).
 * A member can only add/remove their OWN gastos to the shared envelope.
 */
import { useEffect, useRef, useState } from "react";
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
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { redeemEnvelope } from "../api/envelopes";
import { Colors, FontSize, Radius, Spacing } from "../theme";

const CODE_LEN = 6;
const VALID_CODE_RE = /^[A-HJ-NP-Z2-9]{6}$/;

function sanitize(input: string): string {
  return input
    .toUpperCase()
    .replace(/[^A-HJ-NP-Z2-9]/g, "")
    .slice(0, CODE_LEN);
}

export function JoinEnvelopeModal({
  visible,
  onClose,
}: {
  visible: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const lastSubmitted = useRef<string | null>(null);

  useEffect(() => {
    if (!visible) {
      setCode("");
      setError(null);
      lastSubmitted.current = null;
    }
  }, [visible]);

  const join = useMutation({
    mutationFn: (c: string) => redeemEnvelope(c),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["envelopes"] });
      onClose();
    },
    onError: (e: unknown) => {
      lastSubmitted.current = null;
      const detail = (e as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
      setError(
        typeof detail === "string" ? detail : "No pude unirte a ese sobre."
      );
    },
  });

  const submit = (raw: string) => {
    const value = sanitize(raw);
    if (!VALID_CODE_RE.test(value) || join.isPending) return;
    if (lastSubmitted.current === value) return;
    lastSubmitted.current = value;
    setError(null);
    join.mutate(value);
  };

  useEffect(() => {
    if (code.length === CODE_LEN && VALID_CODE_RE.test(code)) submit(code);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="pageSheet"
      onRequestClose={onClose}
    >
      <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
        <View style={styles.header}>
          <Pressable onPress={onClose} hitSlop={12} style={styles.headerBtn}>
            <Feather name="x" size={24} color={Colors.textSecondary} />
          </Pressable>
          <Text style={styles.headerTitle}>Unirme a un sobre</Text>
          <View style={styles.headerBtn} />
        </View>
        <KeyboardAvoidingView
          style={styles.flex}
          behavior={Platform.OS === "ios" ? "padding" : undefined}
        >
          <ScrollView
            contentContainerStyle={styles.scroll}
            keyboardShouldPersistTaps="handled"
          >
            <Text style={styles.intro}>
              Pedile a la persona dueña del sobre que lo comparta y te pase el
              código de 6 caracteres. Vos solo vas a poder agregar y quitar tus
              propios gastos.
            </Text>

            <TextInput
              value={code}
              onChangeText={(next) => {
                setCode(sanitize(next));
                if (error) setError(null);
                lastSubmitted.current = null;
              }}
              placeholder="ABCDEF"
              placeholderTextColor={Colors.textMuted}
              autoCapitalize="characters"
              autoCorrect={false}
              autoComplete="off"
              spellCheck={false}
              keyboardType={
                Platform.OS === "ios" ? "ascii-capable" : "visible-password"
              }
              returnKeyType="go"
              onSubmitEditing={() => submit(code)}
              blurOnSubmit
              maxLength={CODE_LEN}
              style={styles.codeInput}
              editable={!join.isPending}
              textAlign="center"
              selectTextOnFocus
            />
            <Text style={styles.hint}>Mayúsculas, sin 0/O ni 1/I/L.</Text>

            {error ? (
              <View style={styles.errorRow}>
                <Feather name="alert-circle" size={13} color={Colors.expense} />
                <Text style={styles.errorText}>{error}</Text>
              </View>
            ) : null}

            <Pressable
              onPress={() => submit(code)}
              disabled={join.isPending || !VALID_CODE_RE.test(code)}
              style={({ pressed }) => [
                styles.button,
                (join.isPending || !VALID_CODE_RE.test(code)) &&
                  styles.buttonDisabled,
                pressed && { opacity: 0.85 },
              ]}
            >
              {join.isPending ? (
                <ActivityIndicator color={Colors.textOnDark} />
              ) : (
                <Text style={styles.buttonLabel}>Unirme</Text>
              )}
            </Pressable>
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Colors.bg },
  flex: { flex: 1 },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
  },
  headerBtn: { padding: 6, minWidth: 36 },
  headerTitle: {
    flex: 1,
    textAlign: "center",
    fontSize: FontSize.md,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  scroll: { padding: Spacing.lg, gap: Spacing.md },
  intro: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    lineHeight: 21,
  },
  codeInput: {
    backgroundColor: Colors.bgInput,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.border,
    color: Colors.textPrimary,
    fontSize: 30,
    letterSpacing: 8,
    padding: Spacing.md,
    fontFamily: "Menlo",
    fontWeight: "600",
  },
  hint: {
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    textAlign: "center",
  },
  errorRow: { flexDirection: "row", alignItems: "center", gap: Spacing.xs },
  errorText: { color: Colors.expense, fontSize: FontSize.sm, flex: 1 },
  button: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.md,
    alignItems: "center",
    marginTop: Spacing.sm,
  },
  buttonDisabled: { backgroundColor: Colors.border },
  buttonLabel: {
    color: Colors.textOnDark,
    fontSize: FontSize.md,
    fontWeight: "600",
  },
});
