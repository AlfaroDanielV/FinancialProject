import { StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";

import { Colors, FontSize, Spacing } from "../theme";

interface PlaceholderScreenProps {
  title: string;
  upcomingBlock: string;
  description?: string;
}

export function PlaceholderScreen({
  title,
  upcomingBlock,
  description,
}: PlaceholderScreenProps) {
  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.container}>
        <Feather name="clock" size={32} color={Colors.textMuted} />
        <Text style={styles.title}>{title}</Text>
        <Text style={styles.subtitle}>Próximamente en {upcomingBlock}</Text>
        {description ? <Text style={styles.body}>{description}</Text> : null}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },
  container: {
    flex: 1,
    padding: Spacing.lg,
    justifyContent: "center",
    alignItems: "center",
    gap: Spacing.sm,
  },
  title: {
    color: Colors.textPrimary,
    fontSize: FontSize.lg,
    fontWeight: "600",
    marginTop: Spacing.sm,
  },
  subtitle: {
    color: Colors.accent,
    fontSize: FontSize.sm,
    fontWeight: "500",
  },
  body: {
    color: Colors.textSecondary,
    fontSize: FontSize.sm,
    lineHeight: 20,
    textAlign: "center",
    marginTop: Spacing.md,
    maxWidth: 320,
  },
});
