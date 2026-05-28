import { NavigationContainer } from "@react-navigation/native";
import { QueryClientProvider } from "@tanstack/react-query";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";

import { useMagicLinkListener } from "./src/hooks/useMagicLinkListener";
import { AuthProvider, useAuth } from "./src/lib/AuthContext";
import { queryClient } from "./src/lib/queryClient";
import { AppNavigator } from "./src/navigation/AppNavigator";
import { LoginScreen } from "./src/screens/Login";
import { SplashScreen } from "./src/screens/SplashScreen";

function AuthGate() {
  const { status } = useAuth();
  // Phase 6f B3: device-code login is the primary native flow. The
  // magic-link listener stays mounted as a silent fallback so a future
  // bot deep-link (B15) or a manual `ledgercr://` paste auto-signs the
  // user in without surfacing a separate UI affordance.
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
  );
}
