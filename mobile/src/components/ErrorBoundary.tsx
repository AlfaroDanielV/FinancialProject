/**
 * Phase 6f B15 — top-level error boundary.
 *
 * Catches render-time crashes anywhere below it, routes them through the
 * observability scaffold (`captureError`), and shows a calm Spanish fallback
 * instead of a white screen. A "Reintentar" button resets the boundary so a
 * transient error doesn't trap the operator.
 */
import { Component, type ReactNode } from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";

import { captureError } from "../lib/observability";
import { Colors, FontSize, Radius, Spacing } from "../theme";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: unknown, info: { componentStack: string }): void {
    captureError(error, { componentStack: info.componentStack });
  }

  private reset = () => this.setState({ hasError: false });

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }
    return (
      <View style={styles.container}>
        <Text style={styles.title}>Algo salió mal</Text>
        <Text style={styles.body}>
          La app tuvo un problema inesperado. Podés reintentar; si sigue
          fallando, cerrá y abrí la app de nuevo.
        </Text>
        <TouchableOpacity style={styles.button} onPress={this.reset}>
          <Text style={styles.buttonText}>Reintentar</Text>
        </TouchableOpacity>
      </View>
    );
  }
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: Colors.bg,
    alignItems: "center",
    justifyContent: "center",
    padding: Spacing.xl,
  },
  title: {
    fontSize: FontSize.xl,
    fontWeight: "700",
    color: Colors.textPrimary,
    marginBottom: Spacing.md,
  },
  body: {
    fontSize: FontSize.md,
    color: Colors.textSecondary,
    textAlign: "center",
    marginBottom: Spacing.xl,
  },
  button: {
    backgroundColor: Colors.accent,
    paddingVertical: Spacing.md,
    paddingHorizontal: Spacing.xl,
    borderRadius: Radius.md,
  },
  buttonText: {
    color: Colors.bgCard,
    fontSize: FontSize.md,
    fontWeight: "600",
  },
});
