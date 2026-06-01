/**
 * Phase 6f debt slice (D3) — wraps the Chat tab in a stack so the chat→form
 * handoff can push the debt-creation form. The chat reply carries an
 * `open_screen` directive (Intent.CREATE_DEBT); ChatScreen navigates to
 * DebtCreate with the LLM-extracted prefill.
 */
import { createNativeStackNavigator } from "@react-navigation/native-stack";

import { ChatScreen } from "../screens/Chat";
import { DebtCreateScreen } from "../screens/DebtCreateScreen";
import type { DebtPrefill } from "../api/chat";
import { Colors, FontSize } from "../theme";

export type ChatStackParamList = {
  Chat: undefined;
  DebtCreate: { prefill: DebtPrefill };
};

const Stack = createNativeStackNavigator<ChatStackParamList>();

export function ChatNavigator() {
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
      <Stack.Screen name="Chat" component={ChatScreen} options={{ headerShown: false }} />
      <Stack.Screen
        name="DebtCreate"
        component={DebtCreateScreen}
        options={{ title: "Registrar préstamo", presentation: "modal" }}
      />
    </Stack.Navigator>
  );
}
