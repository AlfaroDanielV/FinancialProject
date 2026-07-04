import { isAxiosError } from "axios";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import * as WebBrowser from "expo-web-browser";
import * as ImagePicker from "expo-image-picker";
import { Feather } from "@expo/vector-icons";
import { useNavigation, useRoute } from "@react-navigation/native";
import type { RouteProp } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getAdvisorySession,
  postChatImage,
  postChatMessage,
  resetChat,
  setAdvisorySession,
  type AssignEnvelopePrefill,
  type CardPrefill,
  type ChatButton,
  type ChatUrlButton,
  type DebtPrefill,
  type DuplicateWarningPrefill,
  type ReclassifyPrefill,
} from "../api/chat";
import { fetchOnboardingStatus } from "../api/onboarding";
import { actOnNudge, dismissNudge } from "../api/nudges";
import { deleteTransaction, updateTransaction } from "../api/transactions";
import { assignTransactionEnvelope } from "../api/envelopes";
import { EnvelopePickerModal } from "../components/EnvelopePickerModal";
import type { ChatStackParamList } from "../navigation/ChatNavigator";
import { Colors, FontSize, Radius, Spacing } from "../theme";

function stripHtml(text: string): string {
  return text.replace(/<[^>]+>/g, "");
}

type Message = {
  id: string;
  role: "user" | "bot";
  text: string;
  imageUri?: string;
  buttons?: ChatButton[];
  urlButtons?: ChatUrlButton[];
  // Envelope budgeting: set when the bot reply carried an `assign_envelope`
  // hint — renders an "Asignar a un sobre" chip for this transaction.
  assignTxId?: string;
  // Duplicate detection: set when the bot reply carried a `duplicate_warning`
  // hint — renders Eliminar / Conservar chips for the likely-duplicate row.
  duplicate?: { txId: string; nudgeId: string | null; merchant: string | null };
  // Reclassify gasto ↔ ingreso: set when the bot reply carried an
  // `assign_envelope` (→ offer "Era un ingreso") or `reclassify` (→ offer "Era
  // un gasto") hint. `magnitude` is the absolute amount; the chip overwrites the
  // row's amount with the signed magnitude for the target kind.
  reclassify?: { txId: string; toIncome: boolean; magnitude: number };
  // /menu reply: keep these chips repeatable (don't disable after one tap).
  menu?: boolean;
  // P10 B0.5 (R5): failure class of a handled-error reply — "understanding" |
  // "budget" | "transient" | "system" (server) or "network" (client-side
  // non-2xx). Undefined on a normal answer. Drives distinct styling.
  errorClass?: string;
};

let _nextId = 1;
function nextId() {
  return String(_nextId++);
}

export function ChatScreen() {
  const nav = useNavigation<NativeStackNavigationProp<ChatStackParamList, "Chat">>();
  const route = useRoute<RouteProp<ChatStackParamList, "Chat">>();
  const qc = useQueryClient();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [usedChips, setUsedChips] = useState<Set<string>>(new Set());
  // Envelope budgeting: the message id whose "Asignar a un sobre" chip opened
  // the picker, and the transaction it targets.
  const [assigning, setAssigning] = useState<{ messageId: string; txId: string } | null>(
    null,
  );
  const listRef = useRef<FlatList<Message>>(null);

  // P10 B2: "Modo asesor" header control — reflects the server-side session
  // state on load; hidden entirely while the feature flag is off.
  const { data: advisorySession } = useQuery({
    queryKey: ["advisorySession"],
    queryFn: getAdvisorySession,
    staleTime: 30 * 1000,
  });
  const advisoryMutation = useMutation({
    mutationFn: setAdvisorySession,
    onSuccess: (state) => {
      qc.setQueryData(["advisorySession"], state);
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "bot",
          text: state.active
            ? "Modo asesor activado. Contame qué querés planear — lo vemos con tus números reales. Las consultas puntuales siguen normal."
            : "Listo, salimos del modo asesor.",
        },
      ]);
    },
  });

  // Phase 8 B2: a not-yet-activated user gets a first-run guidance hint in the
  // empty state (no fabricated message is auto-sent).
  const { data: onboarding } = useQuery({
    queryKey: ["onboarding", "status"],
    queryFn: fetchOnboardingStatus,
    staleTime: 60 * 1000,
  });

  const onBotReply = (data: Awaited<ReturnType<typeof postChatMessage>>) => {
    const screen = data.open_screen?.screen;
    const assignTxId =
      screen === "assign_envelope"
        ? (data.open_screen!.prefill as AssignEnvelopePrefill).transaction_id
        : undefined;
    let duplicate: Message["duplicate"];
    if (screen === "duplicate_warning") {
      const p = data.open_screen!.prefill as DuplicateWarningPrefill;
      duplicate = {
        txId: p.transaction_id,
        nudgeId: p.nudge_id,
        merchant: p.matched_merchant ?? p.merchant,
      };
    }
    // Reclassify chip: an expense commit (assign_envelope) offers "Era un
    // ingreso"; an income commit (reclassify) offers "Era un gasto". Both
    // prefills share {transaction_id, amount}. Skip on a duplicate warning —
    // the user resolves keep/delete first.
    let reclassify: Message["reclassify"];
    if (screen === "assign_envelope" || screen === "reclassify") {
      const p = data.open_screen!.prefill as ReclassifyPrefill;
      reclassify = {
        txId: p.transaction_id,
        toIncome: screen === "assign_envelope",
        magnitude: Math.abs(Number(p.amount)),
      };
    }
    setMessages((prev) => [
      ...prev,
      {
        id: nextId(),
        role: "bot",
        text: stripHtml(data.reply_text),
        buttons: data.buttons.length > 0 ? data.buttons : undefined,
        urlButtons: data.url_buttons.length > 0 ? data.url_buttons : undefined,
        assignTxId,
        duplicate,
        reclassify,
        menu: screen === "menu",
        errorClass: data.error_class ?? undefined,
      },
    ]);
    // Phase 6f debt slice: the chat hands off to a native form instead of
    // committing. Show the reply bubble (above), then open the pre-filled form.
    if (screen === "debt_create") {
      nav.navigate("DebtCreate", {
        prefill: data.open_screen!.prefill as DebtPrefill,
      });
    }
    // Phase 7b: credit-card creation follows the same handoff pattern.
    if (screen === "card_create") {
      nav.navigate("CardCreate", {
        prefill: data.open_screen!.prefill as CardPrefill,
      });
    }
    // Movimientos sin cuenta: the agent listed unassigned movements; hand off to
    // the Movimientos tab with the "Sin cuenta" filter so the user can assign each
    // row to an account. Cross-tab navigate (mirrors Analytics → Chat).
    if (screen === "assign_account") {
      (
        nav as unknown as {
          navigate: (
            tab: "Movimientos",
            params: {
              screen: "TransactionsList";
              params: { filterNoAccount: true };
            },
          ) => void;
        }
      ).navigate("Movimientos", {
        screen: "TransactionsList",
        params: { filterNoAccount: true },
      });
    }
    // Native screen launchers from /menu (/cuentas, /movimientos, /sobres,
    // /gmail, /memoria). Cross-tab navigate to the matching screen.
    const launcher: Record<string, { tab: string; screen?: string }> = {
      gmail: { tab: "Mas", screen: "GmailHome" },
      accounts: { tab: "Cuentas" },
      transactions: { tab: "Movimientos", screen: "TransactionsList" },
      home: { tab: "Inicio" },
      memory: { tab: "Mas", screen: "MemoryScreen" },
    };
    if (screen && launcher[screen]) {
      const d = launcher[screen];
      (
        nav as unknown as {
          navigate: (tab: string, params?: { screen: string }) => void;
        }
      ).navigate(d.tab, d.screen ? { screen: d.screen } : undefined);
    }
  };

  const assignMutation = useMutation({
    mutationFn: ({ txId, envelopeId }: { txId: string; envelopeId: string | null }) =>
      assignTransactionEnvelope(txId, envelopeId),
    onSuccess: (_data, { envelopeId }) => {
      void qc.invalidateQueries({ queryKey: ["envelopes"] });
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "bot",
          text:
            envelopeId == null
              ? "Listo, le quité el sobre a ese gasto."
              : "Listo, asigné ese gasto al sobre.",
        },
      ]);
    },
    onError: () => {
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: "bot", text: "No pude asignar el sobre. Intentá de nuevo." },
      ]);
    },
  });

  // Duplicate warning: "Eliminar" deletes the dupe (via the nudge act =
  // hard delete, or directly if no nudge was raised); "Conservar" keeps it
  // (dismiss the nudge = clear the flag). Reuses the nudge act/dismiss path
  // so all surfaces resolve the same way.
  const duplicateMutation = useMutation({
    mutationFn: async ({
      action,
      txId,
      nudgeId,
    }: {
      action: "delete" | "keep";
      txId: string;
      nudgeId: string | null;
      messageId: string;
    }) => {
      if (action === "delete") {
        if (nudgeId) await actOnNudge(nudgeId);
        else await deleteTransaction(txId);
      } else if (nudgeId) {
        await dismissNudge(nudgeId);
      }
    },
    onSuccess: (_data, { action, messageId }) => {
      void qc.invalidateQueries({ queryKey: ["transactions"] });
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
      void qc.invalidateQueries({ queryKey: ["nudgeFeed"] });
      setUsedChips((prev) => new Set(prev).add(messageId));
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "bot",
          text:
            action === "delete"
              ? "Listo, eliminé el movimiento duplicado."
              : "Perfecto, lo dejé tal cual. No era un duplicado.",
        },
      ]);
    },
    onError: (e: unknown, { action }) => {
      const detail = (e as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "bot",
          text:
            typeof detail === "string"
              ? detail
              : action === "delete"
                ? "No pude eliminar el movimiento. Intentá de nuevo."
                : "No pude actualizar el movimiento. Intentá de nuevo.",
        },
      ]);
    },
  });

  // Reclassify gasto ↔ ingreso: overwrite the row's amount with the signed
  // magnitude for the target kind. Backend clears any envelope when it becomes
  // income. Reuses the existing PATCH /transactions/{id}.
  const reclassifyMutation = useMutation({
    mutationFn: ({
      txId,
      toIncome,
      magnitude,
    }: {
      txId: string;
      toIncome: boolean;
      magnitude: number;
      messageId: string;
    }) =>
      updateTransaction(txId, {
        amount: toIncome ? Math.abs(magnitude) : -Math.abs(magnitude),
      }),
    onSuccess: (_data, { toIncome, messageId }) => {
      void qc.invalidateQueries({ queryKey: ["transactions"] });
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
      void qc.invalidateQueries({ queryKey: ["envelopes"] });
      setUsedChips((prev) => new Set(prev).add(messageId));
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "bot",
          text: toIncome
            ? "Listo, lo cambié a ingreso."
            : "Listo, lo cambié a gasto.",
        },
      ]);
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "bot",
          text:
            typeof detail === "string"
              ? detail
              : "No pude reclasificar el movimiento. Intentá de nuevo.",
        },
      ]);
    },
  });

  // P10 B0.5 (R5): this banner fires ONLY on a non-2xx / network failure —
  // every server-handled failure now comes back as HTTP 200 with its own
  // Spanish copy + error_class. Two distinct client-side classes: a TIMEOUT
  // (the server is still working on a long analytical answer — 2026-07-04
  // TestFlight repro) vs a genuinely unreachable server.
  const onError = (err: unknown) => {
    const isTimeout = isAxiosError(err) && err.code === "ECONNABORTED";
    setMessages((prev) => [
      ...prev,
      {
        id: nextId(),
        role: "bot",
        text: isTimeout
          ? "Me estoy tardando más de la cuenta con esa consulta. Esperá un momento y probá de nuevo."
          : "No pude conectar con el servidor. Revisá tu conexión e intentá de nuevo.",
        errorClass: isTimeout ? "timeout" : "network",
      },
    ]);
  };

  const mutation = useMutation({
    mutationFn: postChatMessage,
    onSuccess: onBotReply,
    onError,
  });

  const imageMutation = useMutation({
    mutationFn: ({ uri, mediaType }: { uri: string; mediaType: string }) =>
      postChatImage(uri, mediaType),
    onSuccess: onBotReply,
    onError: () => {
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: "bot", text: "No pude leer el recibo. Intentá de nuevo." },
      ]);
    },
  });

  const resetMutation = useMutation({
    mutationFn: resetChat,
    onSettled: () => {
      // Clear the visible conversation regardless of the server result — the
      // local list is what the user sees as "the chat".
      setMessages([]);
      setUsedChips(new Set());
      setAssigning(null);
      setInput("");
    },
  });

  const isPending = mutation.isPending || imageMutation.isPending;

  const newConversation = () => {
    if (messages.length === 0 || resetMutation.isPending) return;
    Alert.alert(
      "Nueva conversación",
      "Se borra lo que ves acá y se reinicia el asistente. Tus movimientos guardados no se tocan.",
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: "Nueva conversación",
          style: "destructive",
          onPress: () => resetMutation.mutate(),
        },
      ],
    );
  };

  const send = (text: string, sourceMessageId?: string) => {
    const trimmed = text.trim();
    if (!trimmed || isPending) return;
    if (sourceMessageId) {
      setUsedChips((prev) => new Set(prev).add(sourceMessageId));
    }
    setMessages((prev) => [...prev, { id: nextId(), role: "user", text: trimmed }]);
    setInput("");
    mutation.mutate(trimmed);
  };

  // Phase 7h: a prefilled question handed in from another screen (e.g.
  // Analytics "Explícame este gráfico") is auto-sent once, then the param is
  // cleared so re-focus doesn't resend it.
  const autoSentRef = useRef(false);
  useEffect(() => {
    const initial = route.params?.initialMessage;
    if (initial && !autoSentRef.current) {
      autoSentRef.current = true;
      send(initial);
      nav.setParams({ initialMessage: undefined });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.params?.initialMessage]);

  const pickImage = async () => {
    if (isPending) return;
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      quality: 0.85,
      allowsEditing: false,
    });
    if (result.canceled || result.assets.length === 0) return;
    const asset = result.assets[0];
    const uri = asset.uri;
    const mediaType = asset.mimeType ?? "image/jpeg";
    setMessages((prev) => [
      ...prev,
      { id: nextId(), role: "user", text: "", imageUri: uri },
    ]);
    imageMutation.mutate({ uri, mediaType });
  };

  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom"]}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Ledger</Text>
        <View style={styles.headerActions}>
          {advisorySession?.enabled && (
            <Pressable
              onPress={() => advisoryMutation.mutate(!advisorySession.active)}
              disabled={advisoryMutation.isPending}
              hitSlop={8}
              style={({ pressed }) => [
                styles.advisoryBtn,
                advisorySession.active && styles.advisoryBtnActive,
                (pressed || advisoryMutation.isPending) && { opacity: 0.6 },
              ]}
            >
              <Feather
                name="compass"
                size={15}
                color={advisorySession.active ? Colors.bgCard : Colors.accent}
              />
              <Text
                style={[
                  styles.advisoryText,
                  advisorySession.active && styles.advisoryTextActive,
                ]}
              >
                Asesor
              </Text>
            </Pressable>
          )}
          <Pressable
            onPress={newConversation}
            disabled={messages.length === 0 || resetMutation.isPending}
            hitSlop={8}
            style={({ pressed }) => [
              styles.newChatBtn,
              (messages.length === 0 || resetMutation.isPending) && { opacity: 0.4 },
              pressed && { opacity: 0.6 },
            ]}
          >
            {resetMutation.isPending ? (
              <ActivityIndicator size="small" color={Colors.accent} />
            ) : (
              <>
                <Feather name="edit" size={15} color={Colors.accent} />
                <Text style={styles.newChatText}>Nueva</Text>
              </>
            )}
          </Pressable>
        </View>
      </View>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === "ios" ? "padding" : undefined}
      >
        <FlatList
          ref={listRef}
          data={messages}
          keyExtractor={(m) => m.id}
          renderItem={({ item }) => (
            <MessageBubble
              message={item}
              chipsUsed={item.menu ? false : usedChips.has(item.id)}
              onTapButton={(label) => send(label, item.menu ? undefined : item.id)}
              onTapAssign={
                item.assignTxId
                  ? () => setAssigning({ messageId: item.id, txId: item.assignTxId! })
                  : undefined
              }
              onTapDuplicate={
                item.duplicate
                  ? (action) =>
                      duplicateMutation.mutate({
                        action,
                        txId: item.duplicate!.txId,
                        nudgeId: item.duplicate!.nudgeId,
                        messageId: item.id,
                      })
                  : undefined
              }
              onTapReclassify={
                item.reclassify
                  ? () =>
                      reclassifyMutation.mutate({
                        txId: item.reclassify!.txId,
                        toIncome: item.reclassify!.toIncome,
                        magnitude: item.reclassify!.magnitude,
                        messageId: item.id,
                      })
                  : undefined
              }
            />
          )}
          contentContainerStyle={styles.listContent}
          onContentSizeChange={() =>
            listRef.current?.scrollToEnd({ animated: true })
          }
          keyboardDismissMode="interactive"
          keyboardShouldPersistTaps="handled"
          ListEmptyComponent={
            <EmptyHint firstRun={onboarding ? !onboarding.is_activated : false} />
          }
        />

        <View style={styles.inputBar}>
          <Pressable
            onPress={pickImage}
            disabled={isPending}
            style={({ pressed }) => [
              styles.iconButton,
              isPending && styles.iconButtonDisabled,
              pressed && { opacity: 0.6 },
            ]}
          >
            {/* Camera emoji is appropriate here per design guidelines */}
            <Text style={styles.cameraEmoji}>📷</Text>
          </Pressable>

          <TextInput
            value={input}
            onChangeText={setInput}
            placeholder="Escribí /menu para ver qué más puedo hacer"
            placeholderTextColor={Colors.textMuted}
            style={styles.textInput}
            multiline
            maxLength={4096}
            editable={!isPending}
            returnKeyType="send"
            blurOnSubmit={false}
            onSubmitEditing={() => send(input)}
          />

          <Pressable
            onPress={() => send(input)}
            disabled={!input.trim() || isPending}
            style={({ pressed }) => [
              styles.sendButton,
              (!input.trim() || isPending) && styles.sendDisabled,
              pressed && { opacity: 0.75 },
            ]}
          >
            {isPending ? (
              <ActivityIndicator color={Colors.textOnDark} size="small" />
            ) : (
              <Feather name="arrow-up" size={18} color={Colors.textOnDark} />
            )}
          </Pressable>
        </View>
      </KeyboardAvoidingView>

      <EnvelopePickerModal
        visible={assigning != null}
        onClose={() => setAssigning(null)}
        onSelect={(envelopeId) => {
          if (assigning) {
            setUsedChips((prev) => new Set(prev).add(assigning.messageId));
            assignMutation.mutate({ txId: assigning.txId, envelopeId });
          }
          setAssigning(null);
        }}
      />
    </SafeAreaView>
  );
}

function EmptyHint({ firstRun = false }: { firstRun?: boolean }) {
  return (
    <View style={styles.emptyContainer}>
      <Feather name="message-circle" size={36} color={Colors.border} />
      <Text style={styles.emptyTitle}>Ledger</Text>
      {firstRun ? (
        <Text style={styles.emptyBody}>
          Para arrancar, decime cuánto tenés en tu cuenta donde te cae el
          salario.{"\n"}
          Ej: «tengo 200 mil en el BAC».
        </Text>
      ) : (
        <Text style={styles.emptyBody}>
          Preguntá lo que quieras.{"\n"}
          «¿Cuál es mi saldo?» · «¿Cuánto gasté esta semana?»{"\n"}
          «Registrá ₡5.000 en el súper.»
        </Text>
      )}
    </View>
  );
}

interface MessageBubbleProps {
  message: Message;
  chipsUsed: boolean;
  onTapButton: (label: string) => void;
  onTapAssign?: () => void;
  onTapDuplicate?: (action: "delete" | "keep") => void;
  onTapReclassify?: () => void;
}

function MessageBubble({
  message,
  chipsUsed,
  onTapButton,
  onTapAssign,
  onTapDuplicate,
  onTapReclassify,
}: MessageBubbleProps) {
  const isUser = message.role === "user";

  return (
    <View style={isUser ? styles.rowUser : styles.rowBot}>
      {message.imageUri ? (
        <Image
          source={{ uri: message.imageUri }}
          style={styles.thumbnail}
          resizeMode="cover"
        />
      ) : (
        <View
          style={[
            styles.bubble,
            isUser ? styles.bubbleUser : styles.bubbleBot,
          ]}
        >
          <Text style={[styles.bubbleText, isUser && styles.bubbleTextUser]}>
            {message.text}
          </Text>
        </View>
      )}

      {message.buttons && message.buttons.length > 0 && (
        <View style={styles.actionRow}>
          {message.buttons.map((btn) => (
            <Pressable
              key={btn.callback_data}
              onPress={() => !chipsUsed && onTapButton(btn.label)}
              disabled={chipsUsed}
              style={({ pressed }) => [
                styles.chip,
                chipsUsed && styles.chipUsed,
                !chipsUsed && pressed && { opacity: 0.7 },
              ]}
            >
              <Text style={[styles.chipLabel, chipsUsed && styles.chipLabelUsed]}>
                {btn.label}
              </Text>
            </Pressable>
          ))}
        </View>
      )}

      {message.urlButtons && message.urlButtons.length > 0 && (
        <View style={styles.actionRow}>
          {message.urlButtons.map((btn) => (
            <Pressable
              key={btn.url}
              onPress={() => void WebBrowser.openBrowserAsync(btn.url)}
              style={({ pressed }) => [
                styles.urlChip,
                pressed && { opacity: 0.7 },
              ]}
            >
              <Feather name="external-link" size={12} color={Colors.accent} />
              <Text style={styles.urlChipLabel}>{btn.label}</Text>
            </Pressable>
          ))}
        </View>
      )}

      {message.assignTxId && onTapAssign && (
        <View style={styles.actionRow}>
          <Pressable
            onPress={() => !chipsUsed && onTapAssign()}
            disabled={chipsUsed}
            style={({ pressed }) => [
              styles.chip,
              chipsUsed && styles.chipUsed,
              !chipsUsed && pressed && { opacity: 0.7 },
            ]}
          >
            <Text style={[styles.chipLabel, chipsUsed && styles.chipLabelUsed]}>
              Asignar a un sobre
            </Text>
          </Pressable>
        </View>
      )}

      {message.reclassify && onTapReclassify && (
        <View style={styles.actionRow}>
          <Pressable
            onPress={() => !chipsUsed && onTapReclassify()}
            disabled={chipsUsed}
            style={({ pressed }) => [
              styles.chip,
              chipsUsed && styles.chipUsed,
              !chipsUsed && pressed && { opacity: 0.7 },
            ]}
          >
            <Text style={[styles.chipLabel, chipsUsed && styles.chipLabelUsed]}>
              {message.reclassify.toIncome ? "Era un ingreso" : "Era un gasto"}
            </Text>
          </Pressable>
        </View>
      )}

      {message.duplicate && onTapDuplicate && (
        <View style={styles.actionRow}>
          <Pressable
            onPress={() => !chipsUsed && onTapDuplicate("delete")}
            disabled={chipsUsed}
            style={({ pressed }) => [
              styles.chip,
              styles.chipDanger,
              chipsUsed && styles.chipUsed,
              !chipsUsed && pressed && { opacity: 0.7 },
            ]}
          >
            <Text
              style={[
                styles.chipLabel,
                styles.chipLabelDanger,
                chipsUsed && styles.chipLabelUsed,
              ]}
            >
              Eliminar
            </Text>
          </Pressable>
          <Pressable
            onPress={() => !chipsUsed && onTapDuplicate("keep")}
            disabled={chipsUsed}
            style={({ pressed }) => [
              styles.chip,
              chipsUsed && styles.chipUsed,
              !chipsUsed && pressed && { opacity: 0.7 },
            ]}
          >
            <Text style={[styles.chipLabel, chipsUsed && styles.chipLabelUsed]}>
              Conservar
            </Text>
          </Pressable>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: Colors.bg,
  },
  flex: {
    flex: 1,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    backgroundColor: Colors.bgCard,
  },
  headerTitle: {
    fontSize: FontSize.md,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  headerActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.sm,
  },
  advisoryBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 4,
  },
  advisoryBtnActive: {
    backgroundColor: Colors.accent,
  },
  advisoryText: {
    color: Colors.accent,
    fontSize: FontSize.sm,
    fontWeight: "600",
  },
  advisoryTextActive: {
    color: Colors.bgCard,
  },
  newChatBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    borderWidth: 1,
    borderColor: Colors.accent,
    borderRadius: Radius.sm,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 4,
  },
  newChatText: {
    color: Colors.accent,
    fontSize: FontSize.sm,
    fontWeight: "600",
  },
  listContent: {
    flexGrow: 1,
    justifyContent: "flex-end",
    padding: Spacing.md,
    paddingBottom: Spacing.sm,
  },

  // ── empty state ────────────────────────────────────────────────────────────
  emptyContainer: {
    flex: 1,
    justifyContent: "center",
    alignItems: "center",
    paddingHorizontal: Spacing.xl,
    gap: Spacing.sm,
  },
  emptyTitle: {
    color: Colors.textSecondary,
    fontSize: FontSize.lg,
    fontWeight: "600",
  },
  emptyBody: {
    color: Colors.textMuted,
    fontSize: FontSize.sm,
    lineHeight: 20,
    textAlign: "center",
  },

  // ── bubbles ────────────────────────────────────────────────────────────────
  rowUser: {
    alignItems: "flex-end",
    marginBottom: Spacing.sm,
  },
  rowBot: {
    alignItems: "flex-start",
    marginBottom: Spacing.sm,
  },
  bubble: {
    maxWidth: "80%",
    borderRadius: Radius.lg,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
  },
  bubbleUser: {
    backgroundColor: Colors.accent,
    borderBottomRightRadius: Radius.sm,
  },
  bubbleBot: {
    backgroundColor: Colors.bgCard,
    borderWidth: 1,
    borderColor: Colors.borderLight,
    borderBottomLeftRadius: Radius.sm,
  },
  bubbleText: {
    color: Colors.textPrimary,
    fontSize: FontSize.md,
    lineHeight: 21,
  },
  bubbleTextUser: {
    color: Colors.textOnDark,
  },
  thumbnail: {
    width: 180,
    height: 180,
    borderRadius: Radius.md,
    backgroundColor: Colors.bgCard,
  },

  // ── chips ──────────────────────────────────────────────────────────────────
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    marginTop: Spacing.xs + 2,
    gap: Spacing.sm,
  },
  chip: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.xs + 2,
    borderWidth: 1,
    borderColor: Colors.accent,
  },
  chipLabel: {
    color: Colors.accent,
    fontSize: FontSize.sm,
    fontWeight: "500",
  },
  chipUsed: {
    borderColor: Colors.borderLight,
    backgroundColor: Colors.bgElevated,
  },
  chipLabelUsed: {
    color: Colors.textMuted,
  },
  chipDanger: {
    borderColor: Colors.expense,
  },
  chipLabelDanger: {
    color: Colors.expense,
  },
  urlChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: Spacing.xs,
    borderRadius: Radius.lg,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.xs + 2,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bgCard,
  },
  urlChipLabel: {
    color: Colors.accent,
    fontSize: FontSize.sm,
  },

  // ── input bar ──────────────────────────────────────────────────────────────
  inputBar: {
    flexDirection: "row",
    alignItems: "flex-end",
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    borderTopWidth: 1,
    borderTopColor: Colors.borderLight,
    backgroundColor: Colors.bgCard,
    gap: Spacing.sm,
  },
  iconButton: {
    width: 38,
    height: 38,
    borderRadius: Radius.lg,
    backgroundColor: Colors.bgElevated,
    justifyContent: "center",
    alignItems: "center",
    borderWidth: 1,
    borderColor: Colors.border,
  },
  iconButtonDisabled: {
    opacity: 0.4,
  },
  cameraEmoji: {
    fontSize: 18,
  },
  textInput: {
    flex: 1,
    backgroundColor: Colors.bgInput,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    color: Colors.textPrimary,
    fontSize: FontSize.md,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    maxHeight: 120,
  },
  sendButton: {
    backgroundColor: Colors.accent,
    width: 38,
    height: 38,
    borderRadius: 19,
    justifyContent: "center",
    alignItems: "center",
  },
  sendDisabled: {
    backgroundColor: Colors.border,
  },
});

