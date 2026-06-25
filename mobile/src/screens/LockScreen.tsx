import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";

import { useAuth } from "../lib/AuthContext";
import { Colors, FontSize, Radius, Spacing } from "../theme";

/**
 * Shown when a valid session exists but the app is locked behind the device
 * biometric / passcode. Auto-prompts on mount; on failure/cancel it stays put
 * with a retry (never signs out). "Cerrar sesión" is the escape hatch so a
 * biometric failure can't strand the user.
 */
export function LockScreen() {
  const { unlock, signOut } = useAuth();
  const [busy, setBusy] = useState(true);
  const [failed, setFailed] = useState(false);
  const triedRef = useRef(false);

  const attempt = async () => {
    setBusy(true);
    setFailed(false);
    const ok = await unlock();
    setBusy(false);
    if (!ok) setFailed(true);
  };

  useEffect(() => {
    if (triedRef.current) return;
    triedRef.current = true;
    void attempt();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <View style={styles.center}>
        <Feather name="lock" size={36} color={Colors.accent} />
        <Text style={styles.title}>Ledger CR</Text>
        <Text style={styles.subtitle}>
          Desbloqueá con Face ID para continuar.
        </Text>

        {busy ? (
          <ActivityIndicator
            color={Colors.accent}
            style={{ marginTop: Spacing.lg }}
          />
        ) : (
          <Pressable
            onPress={attempt}
            style={({ pressed }) => [styles.btn, pressed && { opacity: 0.75 }]}
          >
            <Text style={styles.btnText}>
              {failed ? "Reintentar" : "Desbloquear"}
            </Text>
          </Pressable>
        )}
      </View>

      <Pressable
        onPress={() => void signOut()}
        hitSlop={8}
        style={styles.logout}
      >
        <Text style={styles.logoutText}>Cerrar sesión</Text>
      </Pressable>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: Colors.bg,
    zIndex: 10,
  },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: Spacing.xl,
    gap: Spacing.sm,
  },
  title: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
    marginTop: Spacing.sm,
  },
  subtitle: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    textAlign: "center",
  },
  btn: {
    marginTop: Spacing.lg,
    backgroundColor: Colors.accent,
    borderRadius: Radius.lg,
    paddingHorizontal: Spacing.xl,
    paddingVertical: Spacing.sm + 4,
  },
  btnText: {
    color: Colors.textOnDark,
    fontSize: FontSize.md,
    fontWeight: "600",
  },
  logout: {
    alignItems: "center",
    paddingVertical: Spacing.lg,
  },
  logoutText: {
    color: Colors.textMuted,
    fontSize: FontSize.sm,
  },
});
