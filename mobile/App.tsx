import { NavigationContainer } from "@react-navigation/native";
import { QueryClientProvider } from "@tanstack/react-query";
import { useFonts } from "expo-font";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { ErrorBoundary } from "./src/components/ErrorBoundary";
import { useMagicLinkListener } from "./src/hooks/useMagicLinkListener";
import { AuthProvider, useAuth } from "./src/lib/AuthContext";
import { initObservability } from "./src/lib/observability";
import { queryClient } from "./src/lib/queryClient";
import { AppNavigator } from "./src/navigation/AppNavigator";
import { LockScreen } from "./src/screens/LockScreen";
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
  if (status === "locked") {
    return <LockScreen />;
  }
  return <AppNavigator />;
}

export default function App() {
  // Phase 7c UI 2.0 — Inter for display/money text. If loading fails (e.g. a
  // stripped asset in Expo Go) the app proceeds on system San Francisco; the
  // splash only waits, it never blocks.
  const [fontsLoaded, fontError] = useFonts({
    "Inter-Regular": require("./assets/fonts/Inter-Regular.ttf"),
    "Inter-Medium": require("./assets/fonts/Inter-Medium.ttf"),
    "Inter-SemiBold": require("./assets/fonts/Inter-SemiBold.ttf"),
    "Inter-Bold": require("./assets/fonts/Inter-Bold.ttf"),
  });

  return (
    <ErrorBoundary>
      <SafeAreaProvider>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <NavigationContainer>
              <StatusBar style="dark" />
              {!fontsLoaded && !fontError ? <SplashScreen /> : <AuthGate />}
            </NavigationContainer>
          </AuthProvider>
        </QueryClientProvider>
      </SafeAreaProvider>
    </ErrorBoundary>
  );
}
