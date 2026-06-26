import { StatusBar } from "expo-status-bar";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Keyboard,
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

import { useAuth } from "../lib/AuthContext";
import { exchangeDeviceCode, ExchangeError } from "../lib/exchange";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";

const CODE_LEN = 6;
const VALID_CODE_RE = /^[A-HJ-NP-Z2-9]{6}$/;

function sanitize(input: string): string {
  return input
    .toUpperCase()
    .replace(/[^A-HJ-NP-Z2-9]/g, "")
    .slice(0, CODE_LEN);
}

export function LoginScreen() {
  const { signIn } = useAuth();
  const [code, setCode] = useState("");
  const [isExchanging, setIsExchanging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastSubmitted = useRef<string | null>(null);

  const submit = async (raw: string) => {
    const value = sanitize(raw);
    if (!VALID_CODE_RE.test(value) || isExchanging) return;
    if (lastSubmitted.current === value) return;
    lastSubmitted.current = value;

    Keyboard.dismiss();
    setIsExchanging(true);
    setError(null);
    try {
      const body = await exchangeDeviceCode(value);
      await signIn(body.token, body.expires_at);
    } catch (err) {
      const message =
        err instanceof ExchangeError ? err.message : "No pude completar el ingreso.";
      setError(message);
      lastSubmitted.current = null;
    } finally {
      setIsExchanging(false);
    }
  };

  useEffect(() => {
    if (code.length === CODE_LEN && VALID_CODE_RE.test(code)) {
      void submit(code);
    }
  }, [code]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <StatusBar style="dark" />
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <Pressable style={styles.flex} onPress={Keyboard.dismiss} accessible={false}>
          <ScrollView
            contentContainerStyle={styles.scroll}
            keyboardShouldPersistTaps="handled"
            keyboardDismissMode="interactive"
            showsVerticalScrollIndicator={false}
          >
            <View style={styles.wordmark}>
              <Text style={styles.wordmarkText}>Ledger</Text>
              <Text style={styles.wordmarkSub}>Finanzas personales</Text>
            </View>

            <View style={styles.card}>
              <View style={styles.cardHeader}>
                <Feather name="lock" size={16} color={Colors.textMuted} />
                <Text style={styles.cardTitle}>Ingresá con tu código de Telegram</Text>
              </View>

              <Text style={styles.instructions}>
                1. En Telegram, escribile{" "}
                <Text style={styles.mono}>/login</Text> al bot.{"\n"}
                2. El bot te responde con un código de 6 caracteres.{"\n"}
                3. Pegalo abajo — la app entra sola.
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
                onSubmitEditing={() => void submit(code)}
                blurOnSubmit
                maxLength={CODE_LEN}
                style={styles.codeInput}
                editable={!isExchanging}
                textAlign="center"
                selectTextOnFocus
              />

              <Text style={styles.hint}>
                Mayúsculas, sin 0/O ni 1/I/L. Caduca en 5 min.
              </Text>

              {error ? (
                <View style={styles.errorRow}>
                  <Feather name="alert-circle" size={13} color={Colors.expense} />
                  <Text style={styles.errorText}>{error}</Text>
                </View>
              ) : null}

              <Pressable
                onPress={() => void submit(code)}
                disabled={isExchanging || !VALID_CODE_RE.test(code)}
                style={({ pressed }) => [
                  styles.button,
                  (isExchanging || !VALID_CODE_RE.test(code)) && styles.buttonDisabled,
                  pressed && { opacity: 0.85 },
                ]}
              >
                {isExchanging ? (
                  <ActivityIndicator color={Colors.textOnDark} />
                ) : (
                  <Text style={styles.buttonLabel}>Ingresar</Text>
                )}
              </Pressable>
            </View>
          </ScrollView>
        </Pressable>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },
  flex: {
    flex: 1,
  },
  scroll: {
    flexGrow: 1,
    justifyContent: "center",
    alignItems: "center",
    padding: Spacing.lg,
  },
  wordmark: {
    alignItems: "center",
    marginBottom: Spacing.xl,
  },
  wordmarkText: {
    fontSize: FontSize.xl,
    fontWeight: "700",
    color: Colors.textPrimary,
    letterSpacing: 0.5,
  },
  wordmarkSub: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    marginTop: 2,
    letterSpacing: 0.5,
  },
  card: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    padding: Spacing.lg,
    width: "100%",
    maxWidth: 380,
    ...CardShadow,
  },
  cardHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    marginBottom: Spacing.md,
  },
  cardTitle: {
    fontSize: FontSize.md,
    fontWeight: "600",
    color: Colors.textPrimary,
  },
  instructions: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    lineHeight: 22,
    marginBottom: Spacing.lg,
  },
  mono: {
    fontFamily: "Menlo",
    fontSize: FontSize.sm,
    color: Colors.accent,
    backgroundColor: Colors.accentBg,
    paddingHorizontal: 4,
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
    marginTop: Spacing.sm,
    textAlign: "center",
    lineHeight: 16,
  },
  errorRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.xs,
    marginTop: Spacing.md,
  },
  errorText: {
    color: Colors.expense,
    fontSize: FontSize.sm,
    flex: 1,
  },
  button: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.md,
    alignItems: "center",
    marginTop: Spacing.lg,
  },
  buttonDisabled: {
    backgroundColor: Colors.border,
  },
  buttonLabel: {
    color: Colors.textOnDark,
    fontSize: FontSize.md,
    fontWeight: "600",
  },
});
