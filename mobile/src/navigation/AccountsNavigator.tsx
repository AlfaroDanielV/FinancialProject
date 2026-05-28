import { createNativeStackNavigator } from "@react-navigation/native-stack";

import { AccountsScreen } from "../screens/AccountsScreen";
import { AccountDetailScreen } from "../screens/AccountDetailScreen";
import { AccountCreateScreen } from "../screens/AccountCreateScreen";
import { Colors, FontSize } from "../theme";

export type AccountsStackParamList = {
  AccountsList: undefined;
  AccountDetail: { accountId: string };
  AccountCreate: undefined;
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
    </Stack.Navigator>
  );
}
