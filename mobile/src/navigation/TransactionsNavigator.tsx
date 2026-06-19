import { createNativeStackNavigator } from "@react-navigation/native-stack";

import { TransactionsScreen } from "../screens/TransactionsScreen";
import { TransactionDetailScreen } from "../screens/TransactionDetailScreen";
import { Colors, FontSize } from "../theme";
import type { TransactionResponse } from "../api/transactions";

export type TransactionsStackParamList = {
  // `filterNoAccount` lets the chat hand off "movimientos sin cuenta" with the
  // Sin cuenta filter pre-applied (consumed once, then cleared).
  TransactionsList: { filterNoAccount?: boolean } | undefined;
  TransactionDetail: { transaction: TransactionResponse };
};

const Stack = createNativeStackNavigator<TransactionsStackParamList>();

export function TransactionsNavigator() {
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
        name="TransactionsList"
        component={TransactionsScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="TransactionDetail"
        component={TransactionDetailScreen}
        options={{ title: "Movimiento" }}
      />
    </Stack.Navigator>
  );
}
