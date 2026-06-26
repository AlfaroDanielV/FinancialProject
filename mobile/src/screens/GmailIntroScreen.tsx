/**
 * Phase 8 B3 — Gmail guided opt-in intro.
 *
 * Gmail is an optional power-up, NOT part of the first-run path (a real user
 * test broke when they stumbled onto sender config with no guidance). This
 * screen is the entry point for the Gmail feature: a numbered, plain-language
 * walkthrough of what it does, why, what to expect (shadow review), and the
 * ~7-day reconnect caveat. Only after "Continuar" does the user reach the
 * actual connect + sender configuration (GmailScreen / GmailHome).
 */
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";

import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";
import type { MasStackParamList } from "../navigation/MasNavigator";

type Nav = NativeStackNavigationProp<MasStackParamList, "GmailIntro">;

interface Step {
  title: string;
  body: string;
  icon: React.ComponentProps<typeof Feather>["name"];
}

const STEPS: Step[] = [
  {
    icon: "shield",
    title: "Qué hace",
    body: "Leemos solo los correos de los bancos que vos autorizás. Nada más de tu bandeja de entrada.",
  },
  {
    icon: "zap",
    title: "Para qué sirve",
    body: "Registramos tus movimientos automáticamente desde los avisos del banco, sin que tengás que anotarlos a mano.",
  },
  {
    icon: "check-circle",
    title: "Qué esperar",
    body: "Cada movimiento que encontremos queda en revisión. Vos lo confirmás antes de que cuente — no se registra nada sin tu visto bueno.",
  },
  {
    icon: "refresh-cw",
    title: "Reconexión cada ~7 días",
    body: "Por la configuración de Google (modo de prueba), el permiso se vence más o menos cada semana. Cuando pase, reconectás con un toque.",
  },
];

export function GmailIntroScreen() {
  const nav = useNavigation<Nav>();

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Conectar Gmail</Text>
        <Text style={styles.headerSub}>
          Una función opcional para capturar tus gastos automáticamente.
        </Text>
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        {STEPS.map((step, i) => (
          <View key={step.title} style={styles.stepRow}>
            <View style={styles.numberBox}>
              <Text style={styles.numberText}>{i + 1}</Text>
            </View>
            <View style={styles.stepText}>
              <View style={styles.stepTitleRow}>
                <Feather name={step.icon} size={15} color={Colors.accentSoft} />
                <Text style={styles.stepTitle}>{step.title}</Text>
              </View>
              <Text style={styles.stepBody}>{step.body}</Text>
            </View>
          </View>
        ))}

        <Pressable
          onPress={() => nav.replace("GmailHome")}
          style={({ pressed }) => [styles.primaryBtn, pressed && { opacity: 0.85 }]}
        >
          <Text style={styles.primaryLabel}>Continuar</Text>
        </Pressable>

        <Pressable onPress={() => nav.goBack()} hitSlop={8} style={styles.laterBtn}>
          <Text style={styles.laterLabel}>Quizás después</Text>
        </Pressable>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Colors.bg },
  header: {
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.md,
    paddingBottom: Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    backgroundColor: Colors.bgCard,
  },
  headerTitle: { fontSize: FontSize.lg, fontWeight: "700", color: Colors.textPrimary },
  headerSub: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    marginTop: 2,
    lineHeight: 18,
  },
  content: { padding: Spacing.md, gap: Spacing.md },
  stepRow: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: Spacing.md,
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    ...CardShadow,
  },
  numberBox: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: Colors.accentBg,
    alignItems: "center",
    justifyContent: "center",
  },
  numberText: { fontSize: FontSize.sm, fontWeight: "700", color: Colors.accent },
  stepText: { flex: 1, gap: Spacing.xs },
  stepTitleRow: { flexDirection: "row", alignItems: "center", gap: Spacing.sm },
  stepTitle: { fontSize: FontSize.md, fontWeight: "600", color: Colors.textPrimary },
  stepBody: { fontSize: FontSize.sm, color: Colors.textSecondary, lineHeight: 20 },
  primaryBtn: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.md - 2,
    alignItems: "center",
    marginTop: Spacing.xs,
  },
  primaryLabel: { fontSize: FontSize.md, fontWeight: "600", color: Colors.textOnDark },
  laterBtn: { alignItems: "center", paddingVertical: Spacing.sm },
  laterLabel: { fontSize: FontSize.sm, fontWeight: "600", color: Colors.textMuted },
});
