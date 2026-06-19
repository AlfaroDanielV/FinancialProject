/**
 * Phase 6f debt slice (D3) — wraps the Chat tab in a stack so the chat→form
 * handoff can push the debt-creation form. The chat reply carries an
 * `open_screen` directive (Intent.CREATE_DEBT); ChatScreen navigates to
 * DebtCreate with the LLM-extracted prefill.
 */
import { createNativeStackNavigator } from "@react-navigation/native-stack";

import { ChatScreen } from "../screens/Chat";
import { CardAccountCreateScreen } from "../screens/CardAccountCreateScreen";
import { DebtCreateScreen } from "../screens/DebtCreateScreen";
import type { CardPrefill, DebtPrefill } from "../api/chat";
import { Colors, FontSize } from "../theme";

export type ChatStackParamList = {
  // Phase 7h: `initialMessage` lets another screen (e.g. Analytics
  // "Explícame este gráfico") open the chat with a pre-filled, auto-sent
  // question.
  Chat: { initialMessage?: string } | undefined;
  DebtCreate: { prefill?: DebtPrefill } | undefined;
  CardCreate: { prefill?: CardPrefill } | undefined;
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
      <Stack.Screen
        name="CardCreate"
        component={CardAccountCreateScreen}
        options={{ title: "Registrar tarjeta", presentation: "modal" }}
      />
    </Stack.Navigator>
  );
}
