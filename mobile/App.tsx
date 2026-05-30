import { NavigationContainer } from "@react-navigation/native";
import { QueryClientProvider } from "@tanstack/react-query";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { ErrorBoundary } from "./src/components/ErrorBoundary";
import { useMagicLinkListener } from "./src/hooks/useMagicLinkListener";
import { AuthProvider, useAuth } from "./src/lib/AuthContext";
import { initObservability } from "./src/lib/observability";
import { queryClient } from "./src/lib/queryClient";
import { AppNavigator } from "./src/navigation/AppNavigator";
import { LoginScreen } from "./src/screens/Login";
import { SplashScreen } from "./src/screens/SplashScreen";

// Phase 6f B15 — scaffold init (console no-op until a DSN + dev build land).
initObservability();

function AuthGate() {
  const { status } = useAuth();
  // Phase 6f B3: device-code login is the primary native flow. The magic-link
  // listener stays mounted as a silent fallback so the bot's B15 `ledgercr://`
  // deep link (from `/login`) — or a manual paste — auto-signs the user in
  // without surfacing a separate UI affordance.
  useMagicLinkListener();

  if (status === "loading") {
    return <SplashScreen />;
  }
  if (status === "anonymous") {
    return <LoginScreen />;
  }
  return <AppNavigator />;
}

export default function App() {
  return (
    <ErrorBoundary>
      <SafeAreaProvider>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <NavigationContainer>
              <StatusBar style="light" />
              <AuthGate />
            </NavigationContainer>
          </AuthProvider>
        </QueryClientProvider>
      </SafeAreaProvider>
    </ErrorBoundary>
  );
}
