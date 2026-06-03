/**
 * Gmail sender whitelist — add comma-separated emails per bank, remove,
 * grouped-by-bank list. Enforces the 8-sender active cap (server-side too).
 */
import { useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addSenders,
  fetchSenders,
  removeSender,
  SENDER_CAP,
  type GmailSender,
} from "../api/gmail";
import { Colors, FontSize, Radius, Spacing } from "../theme";

interface BankGroup {
  bank: string;
  senders: GmailSender[];
}

export function GmailSendersScreen() {
  const qc = useQueryClient();
  const [bank, setBank] = useState("");
  const [emails, setEmails] = useState("");

  const sendersQ = useQuery({ queryKey: ["gmailSenders"], queryFn: fetchSenders });
  const senders = sendersQ.data ?? [];
  const atCap = senders.length >= SENDER_CAP;

  const groups = useMemo<BankGroup[]>(() => {
    const map = new Map<string, GmailSender[]>();
    for (const s of senders) {
      const key = s.bank_name?.trim() || "Sin banco";
      const arr = map.get(key) ?? [];
      arr.push(s);
      map.set(key, arr);
    }
    return [...map.entries()].map(([b, list]) => ({ bank: b, senders: list }));
  }, [senders]);

  const addMutation = useMutation({
    mutationFn: () => addSenders(bank.trim() || null, emails),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["gmailSenders"] });
      setEmails("");
      const parts: string[] = [];
      if (res.added.length) parts.push(`${res.added.length} agregado(s)`);
      if (res.skipped.length) parts.push(`${res.skipped.length} omitido(s)`);
      if (res.at_cap) parts.push(`límite de ${SENDER_CAP} alcanzado`);
      Alert.alert("Listo", parts.join(" · ") || "Sin cambios.");
    },
    onError: () => Alert.alert("Error", "No se pudieron agregar los remitentes."),
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => removeSender(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["gmailSenders"] }),
    onError: () => Alert.alert("Error", "No se pudo quitar el remitente."),
  });

  const onRemove = (s: GmailSender) =>
    Alert.alert("Quitar remitente", s.sender_email, [
      { text: "Cancelar", style: "cancel" },
      { text: "Quitar", style: "destructive", onPress: () => removeMutation.mutate(s.id) },
    ]);

  const canAdd = emails.trim().length > 0 && !addMutation.isPending && !atCap;

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Remitentes por banco</Text>
        <Text style={styles.headerSub}>
          {senders.length}/{SENDER_CAP} remitentes activos
        </Text>
      </View>

      <FlatList
        data={groups}
        keyExtractor={(g) => g.bank}
        contentContainerStyle={styles.content}
        ListHeaderComponent={
          <View style={styles.card}>
            <Text style={styles.label}>Banco (opcional)</Text>
            <TextInput
              style={styles.input}
              value={bank}
              onChangeText={setBank}
              placeholder="Ej: BAC, Promerica"
              placeholderTextColor={Colors.textMuted}
              maxLength={120}
            />
            <Text style={styles.label}>Correos (separados por coma)</Text>
            <TextInput
              style={[styles.input, styles.textarea]}
              value={emails}
              onChangeText={setEmails}
              placeholder="notificacion@bac.cr, alerts@bac.cr"
              placeholderTextColor={Colors.textMuted}
              multiline
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="email-address"
            />
            <Pressable
              onPress={() => addMutation.mutate()}
              disabled={!canAdd}
              style={({ pressed }) => [
                styles.addBtn,
                !canAdd && styles.btnDisabled,
                pressed && canAdd && { opacity: 0.85 },
              ]}
            >
              {addMutation.isPending ? (
                <ActivityIndicator color={Colors.textOnDark} />
              ) : (
                <Text style={styles.addLabel}>Agregar remitentes</Text>
              )}
            </Pressable>
            {atCap && (
              <Text style={styles.capHint}>
                Llegaste al límite de {SENDER_CAP}. Quitá uno para agregar otro.
              </Text>
            )}
          </View>
        }
        renderItem={({ item }) => (
          <View style={styles.group}>
            <Text style={styles.groupTitle}>{item.bank}</Text>
            {item.senders.map((s) => (
              <View key={s.id} style={styles.senderRow}>
                <Feather name="mail" size={14} color={Colors.textMuted} />
                <Text style={styles.senderEmail}>{s.sender_email}</Text>
                <Pressable onPress={() => onRemove(s)} hitSlop={8}>
                  <Feather name="x" size={16} color={Colors.expense} />
                </Pressable>
              </View>
            ))}
          </View>
        )}
        ListEmptyComponent={
          sendersQ.isLoading ? null : (
            <Text style={styles.empty}>
              Todavía no agregaste remitentes. Agregá los correos desde los que
              tu banco te manda avisos.
            </Text>
          )
        }
      />
    </SafeAreaView>
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
  headerSub: { fontSize: FontSize.sm, color: Colors.textMuted, marginTop: 2 },
  content: { padding: Spacing.md, gap: Spacing.sm },
  card: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    gap: Spacing.xs,
    marginBottom: Spacing.sm,
  },
  label: {
    fontSize: FontSize.sm,
    fontWeight: "600",
    color: Colors.textSecondary,
    marginTop: Spacing.xs,
  },
  input: {
    backgroundColor: Colors.bgInput,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    fontSize: FontSize.md,
    color: Colors.textPrimary,
  },
  textarea: { minHeight: 64, textAlignVertical: "top" },
  addBtn: {
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.sm + 4,
    alignItems: "center",
    marginTop: Spacing.sm,
  },
  btnDisabled: { backgroundColor: Colors.border },
  addLabel: { fontSize: FontSize.md, fontWeight: "600", color: Colors.textOnDark },
  capHint: { fontSize: FontSize.xs, color: Colors.warning, marginTop: Spacing.xs },
  group: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.md,
    gap: Spacing.sm,
  },
  groupTitle: { fontSize: FontSize.sm, fontWeight: "700", color: Colors.textSecondary },
  senderRow: { flexDirection: "row", alignItems: "center", gap: Spacing.sm },
  senderEmail: { flex: 1, fontSize: FontSize.sm, color: Colors.textPrimary },
  empty: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    textAlign: "center",
    lineHeight: 20,
    paddingHorizontal: Spacing.md,
    marginTop: Spacing.lg,
  },
});
