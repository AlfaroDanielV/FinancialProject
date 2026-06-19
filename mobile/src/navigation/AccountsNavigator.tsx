import { createNativeStackNavigator } from "@react-navigation/native-stack";

import { AccountsScreen } from "../screens/AccountsScreen";
import { AccountDetailScreen } from "../screens/AccountDetailScreen";
import { AccountCreateScreen } from "../screens/AccountCreateScreen";
import { CardAccountCreateScreen } from "../screens/CardAccountCreateScreen";
import { StatementReconcileScreen } from "../screens/StatementReconcileScreen";
import type { CardPrefill } from "../api/chat";
import { Colors, FontSize } from "../theme";

export type AccountsStackParamList = {
  AccountsList: undefined;
  AccountDetail: { accountId: string };
  AccountCreate: undefined;
  CardCreate: { prefill?: CardPrefill } | undefined;
  StatementReconcile: undefined;
};

const Stack = createNativeStackNavigator<AccountsStackParamList>();

export function AccountsNavigator() {
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
        name="AccountsList"
        component={AccountsScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="AccountDetail"
        component={AccountDetailScreen}
        options={{ title: "Detalle de cuenta" }}
      />
      <Stack.Screen
        name="AccountCreate"
        component={AccountCreateScreen}
        options={{ title: "Nueva cuenta", presentation: "modal" }}
      />
      <Stack.Screen
        name="CardCreate"
        component={CardAccountCreateScreen}
        options={{ title: "Registrar tarjeta", presentation: "modal" }}
      />
      <Stack.Screen
        name="StatementReconcile"
        component={StatementReconcileScreen}
        options={{ title: "Reconciliar con estado", presentation: "modal" }}
      />
    </Stack.Navigator>
  );
}
