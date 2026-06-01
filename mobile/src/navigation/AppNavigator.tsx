import { Feather } from "@expo/vector-icons";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";

import { ChatNavigator } from "./ChatNavigator";
import { DashboardScreen } from "../screens/Dashboard";
import { AccountsNavigator } from "./AccountsNavigator";
import { TransactionsNavigator } from "./TransactionsNavigator";
import { MasNavigator } from "./MasNavigator";
import { Colors, FontSize } from "../theme";

type RootTabParamList = {
  Inicio: undefined;
  Chat: undefined;
  Cuentas: undefined;
  Movimientos: undefined;
  Mas: undefined;
};

type FeatherName = React.ComponentProps<typeof Feather>["name"];

const TAB_ICONS: Record<string, FeatherName> = {
  Inicio: "home",
  Chat: "message-circle",
  Cuentas: "credit-card",
  Movimientos: "list",
  Mas: "more-horizontal",
};

const Tab = createBottomTabNavigator<RootTabParamList>();

export function AppNavigator() {
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        tabBarIcon: ({ color, size }) => (
          <Feather
            name={TAB_ICONS[route.name] ?? "circle"}
            size={size - 2}
            color={color}
          />
        ),
        headerStyle: {
          backgroundColor: Colors.bgCard,
          shadowColor: Colors.border,
          elevation: 1,
        },
        headerTitleStyle: {
          color: Colors.textPrimary,
          fontSize: FontSize.md,
          fontWeight: "600",
        },
        headerTintColor: Colors.textPrimary,
        tabBarStyle: {
          backgroundColor: Colors.bgCard,
          borderTopColor: Colors.border,
          borderTopWidth: 1,
        },
        tabBarActiveTintColor: Colors.accent,
        tabBarInactiveTintColor: Colors.textMuted,
        tabBarLabelStyle: {
          fontSize: FontSize.xs,
          fontWeight: "500",
          marginBottom: 2,
        },
      })}
    >
      <Tab.Screen
        name="Inicio"
        component={DashboardScreen}
        options={{ title: "Inicio", headerShown: false }}
      />
      <Tab.Screen
        name="Chat"
        component={ChatNavigator}
        options={{ title: "Chat", headerShown: false }}
      />
      <Tab.Screen
        name="Cuentas"
        component={AccountsNavigator}
        options={{ headerShown: false }}
      />
      <Tab.Screen
        name="Movimientos"
        component={TransactionsNavigator}
        options={{ headerShown: false }}
      />
      <Tab.Screen
        name="Mas"
        component={MasNavigator}
        options={{ headerShown: false, title: "Más" }}
      />
    </Tab.Navigator>
  );
}
