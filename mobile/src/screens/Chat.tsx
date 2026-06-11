import { useRef, useState } from "react";
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
import { useNavigation } from "@react-navigation/native";
import type { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  postChatImage,
  postChatMessage,
  resetChat,
  type AssignEnvelopePrefill,
  type CardPrefill,
  type ChatButton,
  type ChatUrlButton,
  type DebtPrefill,
} from "../api/chat";
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
};

let _nextId = 1;
function nextId() {
  return String(_nextId++);
}

export function ChatScreen() {
  const nav = useNavigation<NativeStackNavigationProp<ChatStackParamList, "Chat">>();
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

  const onBotReply = (data: Awaited<ReturnType<typeof postChatMessage>>) => {
    const screen = data.open_screen?.screen;
    const assignTxId =
      screen === "assign_envelope"
        ? (data.open_screen!.prefill as AssignEnvelopePrefill).transaction_id
        : undefined;
    setMessages((prev) => [
      ...prev,
      {
        id: nextId(),
        role: "bot",
        text: stripHtml(data.reply_text),
        buttons: data.buttons.length > 0 ? data.buttons : undefined,
        urlButtons: data.url_buttons.length > 0 ? data.url_buttons : undefined,
        assignTxId,
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

  const onError = () => {
    setMessages((prev) => [
      ...prev,
      { id: nextId(), role: "bot", text: "Hubo un error. Intentá de nuevo." },
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
        <Text style={styles.headerTitle}>Ledger CR</Text>
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
              chipsUsed={usedChips.has(item.id)}
              onTapButton={(label) => send(label, item.id)}
              onTapAssign={
                item.assignTxId
                  ? () => setAssigning({ messageId: item.id, txId: item.assignTxId! })
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
          ListEmptyComponent={<EmptyHint />}
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
            placeholder="Escribí un mensaje…"
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

function EmptyHint() {
  return (
    <View style={styles.emptyContainer}>
      <Feather name="message-circle" size={36} color={Colors.border} />
      <Text style={styles.emptyTitle}>Ledger CR</Text>
      <Text style={styles.emptyBody}>
        Preguntá lo que quieras.{"\n"}
        «¿Cuál es mi saldo?» · «¿Cuánto gasté esta semana?»{"\n"}
        «Registrá ₡5.000 en el súper.»
      </Text>
    </View>
  );
}

interface MessageBubbleProps {
  message: Message;
  chipsUsed: boolean;
  onTapButton: (label: string) => void;
  onTapAssign?: () => void;
}

function MessageBubble({
  message,
  chipsUsed,
  onTapButton,
  onTapAssign,
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

