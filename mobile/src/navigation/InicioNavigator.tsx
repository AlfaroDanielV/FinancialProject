/**
 * Phase 7h — wraps the Inicio tab in a stack so the home screen can push the
 * Analytics screen (tap the Sobres / budget-execution card → "Ver análisis").
 * Mirrors AccountsNavigator.
 */
import { createNativeStackNavigator } from "@react-navigation/native-stack";

import { DashboardScreen } from "../screens/Dashboard";
import { AnalyticsScreen } from "../screens/AnalyticsScreen";
import { Colors, FontSize } from "../theme";

export type InicioStackParamList = {
  DashboardHome: undefined;
  Analytics: undefined;
};

const Stack = createNativeStackNavigator<InicioStackParamList>();

export function InicioNavigator() {
  return (
    <Stack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: Colors.bgCard },
        headerTitleStyle: {
          color: Colors.textPrimary,
          fontSize: FontSize.md,
          fontWeight: "600",
        },
        headerTintColor: Colors.accent,
        headerBackButtonDisplayMode: "minimal",
        contentStyle: { backgroundColor: Colors.bg },
      }}
    >
      <Stack.Screen
        name="DashboardHome"
        component={DashboardScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="Analytics"
        component={AnalyticsScreen}
        options={{ headerShown: false }}
      />
    </Stack.Navigator>
  );
}
