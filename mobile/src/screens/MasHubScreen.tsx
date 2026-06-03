/**
 * Phase 6f B11 — "Más" hub screen.
 *
 * Root screen of the Más tab. Shows tiles for every module in B10-B14.
 * Bills and Debts are live; B12-B14 show a coming-soon badge.
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
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";

import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";
import type { MasStackParamList } from "../navigation/MasNavigator";

type Props = {
  navigation: NativeStackNavigationProp<MasStackParamList, "MasHub">;
};

interface ModuleTile {
  key: string;
  title: string;
  subtitle: string;
  icon: React.ComponentProps<typeof Feather>["name"];
  screen?: keyof MasStackParamList;
  coming?: string;
}

const MODULES: ModuleTile[] = [
  {
    key: "bills",
    title: "Gastos fijos",
    subtitle: "Pagos recurrentes y próximos vencimientos",
    icon: "file-text",
    screen: "BillsList",
  },
  {
    key: "debts",
    title: "Deudas",
    subtitle: "Saldos, amortización y cancelación anticipada",
    icon: "trending-down",
    screen: "DebtsList",
  },
  {
    key: "incomes",
    title: "Ingresos",
    subtitle: "Salarios, aguinaldo y salario escolar",
    icon: "trending-up",
    screen: "IncomesList",
  },
  {
    key: "goals",
    title: "Metas",
    subtitle: "Ahorros y seguimiento de metas financieras",
    icon: "target",
    screen: "GoalsList",
  },
  {
    key: "categories",
    title: "Categorías",
    subtitle: "Categorías personalizadas para transacciones",
    icon: "tag",
    screen: "CategoriesScreen",
  },
  {
    key: "gmail",
    title: "Gmail",
    subtitle: "Conectar correo, remitentes por banco y revisar hallazgos",
    icon: "mail",
    screen: "GmailHome",
  },
  {
    key: "memory",
    title: "Memoria y privacidad",
    subtitle: "Insights del asistente y exportación de datos",
    icon: "cpu",
    screen: "MemoryScreen",
  },
];

export function MasHubScreen({ navigation }: Props) {
  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Más</Text>
        <Text style={styles.headerSub}>Módulos adicionales</Text>
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        {MODULES.map((mod) => (
          <Pressable
            key={mod.key}
            style={({ pressed }) => [
              styles.tile,
              mod.coming && styles.tileDimmed,
              pressed && !mod.coming && styles.tilePressed,
            ]}
            onPress={() => {
              if (mod.screen) navigation.navigate(mod.screen as never);
            }}
            disabled={Boolean(mod.coming)}
          >
            <View
              style={[
                styles.iconBox,
                mod.coming ? styles.iconBoxDimmed : styles.iconBoxActive,
              ]}
            >
              <Feather
                name={mod.icon}
                size={20}
                color={mod.coming ? Colors.textMuted : Colors.accent}
              />
            </View>
            <View style={styles.tileText}>
              <Text
                style={[styles.tileTitle, mod.coming && styles.tileTitleDimmed]}
              >
                {mod.title}
              </Text>
              <Text style={styles.tileSub}>{mod.subtitle}</Text>
            </View>
            <View style={styles.tileRight}>
              {mod.coming ? (
                <Text style={styles.comingBadge}>{mod.coming}</Text>
              ) : (
                <Feather name="chevron-right" size={18} color={Colors.textMuted} />
              )}
            </View>
          </Pressable>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },
  header: {
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.md,
    paddingBottom: Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    backgroundColor: Colors.bgCard,
  },
  headerTitle: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  headerSub: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    marginTop: 2,
  },
  content: {
    padding: Spacing.md,
    gap: Spacing.sm,
  },
  tile: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.md - 2,
    gap: Spacing.md,
    ...CardShadow,
  },
  tileDimmed: {
    opacity: 0.6,
  },
  tilePressed: {
    opacity: 0.78,
  },
  iconBox: {
    width: 40,
    height: 40,
    borderRadius: Radius.md,
    alignItems: "center",
    justifyContent: "center",
  },
  iconBoxActive: {
    backgroundColor: Colors.accentBg,
  },
  iconBoxDimmed: {
    backgroundColor: Colors.bgElevated,
  },
  tileText: {
    flex: 1,
    gap: 2,
  },
  tileTitle: {
    fontSize: FontSize.md,
    fontWeight: "600",
    color: Colors.textPrimary,
  },
  tileTitleDimmed: {
    color: Colors.textMuted,
  },
  tileSub: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    lineHeight: 18,
  },
  tileRight: {
    alignItems: "center",
    justifyContent: "center",
  },
  comingBadge: {
    fontSize: FontSize.xs,
    fontWeight: "700",
    color: Colors.textMuted,
    backgroundColor: Colors.bgElevated,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 2,
    overflow: "hidden",
  },
});
