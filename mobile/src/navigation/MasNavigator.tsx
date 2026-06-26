/**
 * Phase 6f B14 — "Más" tab navigator.
 *
 * Flat stack navigator rooted at a hub screen (MasHub). Each module in
 * B10–B14 adds its screens here as the phase progresses.
 *
 * Current screens:
 *   MasHub      — module menu
 *   BillsList   — upcoming bill occurrences
 *   BillDetail  — bill detail + mark-paid + pause/archive
 *   DebtsList   — debt list with overview metrics
 *   DebtDetail  — debt detail + amortization + payoff calculator
 *   IncomesList — recurring incomes + CR-cycle nudge
 *   GoalsList   — goals list with status filter
 *   GoalDetail      — goal progress + forecast + contributions
 *   CategoriesScreen — categories list + create + archive
 *   MemoryScreen     — insights grouped + delete + export
 */
import { createNativeStackNavigator } from "@react-navigation/native-stack";

import { MasHubScreen } from "../screens/MasHubScreen";
import { AlertsScreen } from "../screens/AlertsScreen";
import { BillsScreen } from "../screens/BillsScreen";
import { BillDetailScreen } from "../screens/BillDetailScreen";
import { DebtsScreen } from "../screens/DebtsScreen";
import { DebtDetailScreen } from "../screens/DebtDetailScreen";
import { DebtCreateScreen } from "../screens/DebtCreateScreen";
import { IncomesScreen } from "../screens/IncomesScreen";
import { GoalsScreen } from "../screens/GoalsScreen";
import { GoalDetailScreen } from "../screens/GoalDetailScreen";
import { CategoriesScreen } from "../screens/CategoriesScreen";
import { MemoryScreen } from "../screens/MemoryScreen";
import { GmailIntroScreen } from "../screens/GmailIntroScreen";
import { GmailScreen } from "../screens/GmailScreen";
import { GmailSendersScreen } from "../screens/GmailSendersScreen";
import { GmailReviewScreen } from "../screens/GmailReviewScreen";
import { Colors, FontSize } from "../theme";
import type { RecurringBillResponse, BillOccurrenceResponse } from "../api/bills";
import type { DebtPrefill } from "../api/chat";

export type MasStackParamList = {
  MasHub: undefined;
  Alertas: undefined;
  BillsList: undefined;
  BillDetail: {
    bill: RecurringBillResponse;
    occurrence: BillOccurrenceResponse | null;
  };
  DebtsList: undefined;
  DebtDetail: { debtId: string };
  DebtCreate: { prefill?: DebtPrefill } | undefined;
  IncomesList: undefined;
  GoalsList: undefined;
  GoalDetail: { goalId: string };
  CategoriesScreen: undefined;
  MemoryScreen: undefined;
  GmailIntro: undefined;
  GmailHome: undefined;
  GmailSenders: undefined;
  GmailReview: undefined;
};

const Stack = createNativeStackNavigator<MasStackParamList>();

export function MasNavigator() {
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
        name="MasHub"
        component={MasHubScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="Alertas"
        component={AlertsScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="BillsList"
        component={BillsScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="BillDetail"
        component={BillDetailScreen}
        options={({ route }) => ({ title: route.params.bill.name })}
      />
      <Stack.Screen
        name="DebtsList"
        component={DebtsScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="DebtDetail"
        component={DebtDetailScreen}
        options={{ title: "Deuda" }}
      />
      <Stack.Screen
        name="DebtCreate"
        component={DebtCreateScreen}
        options={{ title: "Registrar préstamo", presentation: "modal" }}
      />
      <Stack.Screen
        name="IncomesList"
        component={IncomesScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="GoalsList"
        component={GoalsScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="GoalDetail"
        component={GoalDetailScreen}
        options={{ title: "Meta" }}
      />
      <Stack.Screen
        name="CategoriesScreen"
        component={CategoriesScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="MemoryScreen"
        component={MemoryScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="GmailIntro"
        component={GmailIntroScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="GmailHome"
        component={GmailScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="GmailSenders"
        component={GmailSendersScreen}
        options={{ headerShown: false }}
      />
      <Stack.Screen
        name="GmailReview"
        component={GmailReviewScreen}
        options={{ headerShown: false }}
      />
    </Stack.Navigator>
  );
}
