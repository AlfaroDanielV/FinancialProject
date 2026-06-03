/**
 * Native Gmail hub — connect, scan (with real feedback), and links to
 * senders + review.
 *
 * Connect is poll-based: open the Google consent URL; the server handles the
 * OAuth callback while the browser is open, so on close we refetch status.
 *
 * Scan feedback: POST /gmail/scan guards (409 not connected / 400 no senders)
 * surface immediately; on success we poll GET /gmail/scan/status until the new
 * ingestion run finishes, showing a spinner then the result (N scanned / M
 * found / 0 / failed). This is the progress feedback that was missing.
 */
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import * as WebBrowser from "expo-web-browser";
import { Feather } from "@expo/vector-icons";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { AxiosError } from "axios";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchScanStatus, oauthStart, scanGmail } from "../api/gmail";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";
import type { MasStackParamList } from "../navigation/MasNavigator";

type Nav = NativeStackNavigationProp<MasStackParamList, "GmailHome">;

function detailOf(err: unknown, fallback: string): string {
  if (err instanceof AxiosError) {
    const d = (err.response?.data as { detail?: string } | undefined)?.detail;
    if (typeof d === "string") return d;
  }
  return fallback;
}

export function GmailScreen() {
  const nav = useNavigation<Nav>();
  const qc = useQueryClient();
  const [connecting, setConnecting] = useState(false);
  const [scanning, setScanning] = useState(false);
  const prevRunStart = useRef<string | null>(null);
  const scanStartedAtMs = useRef<number | null>(null);

  const statusQ = useQuery({
    queryKey: ["gmailScanStatus"],
    queryFn: fetchScanStatus,
    refetchInterval: scanning ? 2500 : false,
  });
  const status = statusQ.data;
  const connected = status?.connected ?? false;
  const sendersCount = status?.senders_count ?? 0;
  const run = status?.latest_run ?? null;

  // End the scan spinner on: disconnect (revoked token) → reconnect prompt;
  // a NEW finished run → result; or a safety timeout so it never hangs.
  useEffect(() => {
    if (!scanning || !status) return;

    // Disconnected mid-scan — the token died (often the GCP "Testing mode"
    // 7-day refresh-token expiry). Always surface this + offer reconnect.
    if (!status.connected) {
      setScanning(false);
      Alert.alert(
        "Gmail desconectado",
        "Se desconectó tu Gmail (el permiso venció o se revocó). Reconectá para escanear.",
      );
      return;
    }

    if (
      scanStartedAtMs.current &&
      Date.now() - scanStartedAtMs.current > 90_000
    ) {
      setScanning(false);
      Alert.alert("Tardando", "El escaneo está tardando. Volvé a revisar en un momento.");
      return;
    }

    const isNewRun = run != null && run.started_at !== prevRunStart.current;
    if (run && isNewRun && !run.running) {
      setScanning(false);
      if (run.has_errors) {
        Alert.alert("Escaneo con errores", "Algo falló revisando tu correo. Reintentá o revisá los remitentes.");
      } else if (run.transactions_created > 0) {
        Alert.alert(
          "Listo",
          `Revisé ${run.messages_scanned} correo(s). Encontré ${run.transactions_created} para revisar.`,
          [
            { text: "Después", style: "cancel" },
            { text: "Revisar", onPress: () => nav.navigate("GmailReview") },
          ],
        );
      } else if (run.messages_scanned === 0) {
        Alert.alert(
          "Sin correos",
          "No encontré correos de tus bancos en los últimos 30 días. Revisá que los remitentes sean correctos.",
        );
      } else {
        Alert.alert("Listo", `Revisé ${run.messages_scanned} correo(s). No encontré transacciones nuevas.`);
      }
    }
  }, [scanning, status, run, nav]);

  const scanMutation = useMutation({
    mutationFn: () => scanGmail(30),
    onMutate: () => {
      prevRunStart.current = run?.started_at ?? null;
      scanStartedAtMs.current = Date.now();
    },
    onSuccess: () => {
      setScanning(true);
      qc.invalidateQueries({ queryKey: ["gmailScanStatus"] });
    },
    onError: (err) =>
      Alert.alert("No pude escanear", detailOf(err, "Intentá de nuevo en un momento.")),
  });

  const onConnect = async () => {
    setConnecting(true);
    try {
      const { auth_url } = await oauthStart();
      await WebBrowser.openBrowserAsync(auth_url);
      await qc.invalidateQueries({ queryKey: ["gmailScanStatus"] });
    } catch {
      Alert.alert("Error", "No se pudo iniciar la conexión con Gmail.");
    } finally {
      setConnecting(false);
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Gmail</Text>
        <Text style={styles.headerSub}>
          Conectá tu correo para registrar gastos desde los avisos del banco.
        </Text>
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        {/* ── connection status ────────────────────────────────────────── */}
        <View style={styles.card}>
          <View style={styles.statusRow}>
            <Feather
              name={connected ? "check-circle" : "mail"}
              size={20}
              color={connected ? Colors.income : Colors.textMuted}
            />
            <Text style={styles.statusText}>
              {statusQ.isLoading
                ? "Cargando…"
                : connected
                  ? "Gmail conectado"
                  : status?.revoked
                    ? "Conexión revocada"
                    : "Gmail no conectado"}
            </Text>
            <Pressable
              onPress={() => qc.invalidateQueries({ queryKey: ["gmailScanStatus"] })}
              hitSlop={8}
            >
              <Feather name="refresh-cw" size={16} color={Colors.textMuted} />
            </Pressable>
          </View>

          {!connected && (
            <>
              <Pressable
                onPress={onConnect}
                disabled={connecting}
                style={({ pressed }) => [
                  styles.primaryBtn,
                  connecting && styles.btnDisabled,
                  pressed && { opacity: 0.85 },
                ]}
              >
                {connecting ? (
                  <ActivityIndicator color={Colors.textOnDark} />
                ) : (
                  <Text style={styles.primaryLabel}>
                    {status?.revoked ? "Reconectar Gmail" : "Conectar Gmail"}
                  </Text>
                )}
              </Pressable>
              <Text style={styles.hint}>
                Se abre el consentimiento de Google. Cuando termines, volvé acá y
                tocá actualizar.
              </Text>
            </>
          )}

          {/* Manual reconnect is always available — Gmail "Testing mode" tokens
              expire ~weekly, so a silent disconnect can happen anytime. */}
          {connected && (
            <Pressable onPress={onConnect} disabled={connecting} hitSlop={6}>
              <Text style={styles.reconnectLink}>
                {connecting ? "Abriendo…" : "Reconectar Gmail"}
              </Text>
            </Pressable>
          )}
        </View>

        {/* ── connected: senders + scan + review ───────────────────────── */}
        {connected && (
          <>
            {sendersCount === 0 ? (
              <View style={styles.warnCard}>
                <Feather name="alert-triangle" size={18} color={Colors.warning} />
                <Text style={styles.warnText}>
                  Agregá los remitentes de tu banco para poder escanear.
                </Text>
              </View>
            ) : (
              <Text style={styles.sendersLine}>
                {sendersCount} remitente(s) configurado(s)
              </Text>
            )}

            {scanning ? (
              <View style={styles.scanningCard}>
                <ActivityIndicator color={Colors.accent} />
                <Text style={styles.scanningText}>Escaneando tu correo…</Text>
              </View>
            ) : (
              sendersCount > 0 && (
                <Pressable
                  onPress={() => scanMutation.mutate()}
                  disabled={scanMutation.isPending}
                  style={({ pressed }) => [
                    styles.primaryBtn,
                    scanMutation.isPending && styles.btnDisabled,
                    pressed && { opacity: 0.85 },
                  ]}
                >
                  {scanMutation.isPending ? (
                    <ActivityIndicator color={Colors.textOnDark} />
                  ) : (
                    <Text style={styles.primaryLabel}>Escanear ahora</Text>
                  )}
                </Pressable>
              )
            )}

            {!scanning && run && run.finished_at && (
              <Text style={styles.lastRun}>
                Último escaneo: {run.messages_scanned} correo(s),{" "}
                {run.transactions_created} encontrado(s)
                {run.has_errors ? " · con errores" : ""}
              </Text>
            )}

            <NavTile
              icon="inbox"
              title="Revisar correos"
              subtitle="Confirmá o descartá lo que encontré"
              onPress={() => nav.navigate("GmailReview")}
            />
            <NavTile
              icon="at-sign"
              title="Remitentes por banco"
              subtitle="Agregá los correos de cada banco"
              onPress={() => nav.navigate("GmailSenders")}
            />
          </>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

function NavTile({
  icon,
  title,
  subtitle,
  onPress,
}: {
  icon: React.ComponentProps<typeof Feather>["name"];
  title: string;
  subtitle: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.tile, pressed && { opacity: 0.8 }]}
    >
      <View style={styles.iconBox}>
        <Feather name={icon} size={18} color={Colors.accent} />
      </View>
      <View style={styles.tileText}>
        <Text style={styles.tileTitle}>{title}</Text>
        <Text style={styles.tileSub}>{subtitle}</Text>
      </View>
      <Feather name="chevron-right" size={18} color={Colors.textMuted} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Colors.bg },
  header: {
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.md,
    paddingBottom: Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    backgroundColor: Colors.bgCard,
  },
  headerTitle: { fontSize: FontSize.lg, fontWeight: "700", color: Colors.textPrimary },
  headerSub: { fontSize: FontSize.sm, color: Colors.textMuted, marginTop: 2, lineHeight: 18 },
  content: { padding: Spacing.md, gap: Spacing.sm },
  card: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    gap: Spacing.sm,
    ...CardShadow,
  },
  statusRow: { flexDirection: "row", alignItems: "center", gap: Spacing.sm },
  statusText: { flex: 1, fontSize: FontSize.md, fontWeight: "600", color: Colors.textPrimary },
  primaryBtn: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.md - 2,
    alignItems: "center",
  },
  btnDisabled: { backgroundColor: Colors.border },
  primaryLabel: { fontSize: FontSize.md, fontWeight: "600", color: Colors.textOnDark },
  hint: { fontSize: FontSize.xs, color: Colors.textMuted, lineHeight: 17 },
  reconnectLink: {
    fontSize: FontSize.sm,
    color: Colors.accent,
    fontWeight: "600",
  },
  warnCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    backgroundColor: Colors.warningBg,
    borderRadius: Radius.md,
    padding: Spacing.md,
  },
  warnText: { flex: 1, fontSize: FontSize.sm, color: Colors.warning, lineHeight: 19 },
  sendersLine: { fontSize: FontSize.sm, color: Colors.textSecondary, paddingHorizontal: Spacing.xs },
  scanningCard: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
  },
  scanningText: { fontSize: FontSize.md, color: Colors.textPrimary, fontWeight: "600" },
  lastRun: { fontSize: FontSize.xs, color: Colors.textMuted, paddingHorizontal: Spacing.xs },
  tile: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.md - 2,
    gap: Spacing.md,
    ...CardShadow,
  },
  iconBox: {
    width: 40,
    height: 40,
    borderRadius: Radius.md,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: Colors.accentBg,
  },
  tileText: { flex: 1, gap: 2 },
  tileTitle: { fontSize: FontSize.md, fontWeight: "600", color: Colors.textPrimary },
  tileSub: { fontSize: FontSize.sm, color: Colors.textSecondary, lineHeight: 18 },
});
