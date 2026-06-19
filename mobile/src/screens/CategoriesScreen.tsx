/**
 * Phase 6f B14 — Categories management screen.
 *
 * List of user categories with:
 *   - Archived toggle (show/hide archived rows)
 *   - Inline "Nueva categoría" form (name + kind + color preset)
 *   - Per-row archive / restore action (disabled for default categories)
 */
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { Feather } from "@expo/vector-icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  archiveCategory,
  createCategory,
  fetchCategories,
  KIND_LABELS,
  PRESET_COLORS,
  restoreCategory,
  type CategoryKind,
  type CategoryResponse,
} from "../api/categories";
import { CardShadow, Colors, FontSize, Radius, Spacing } from "../theme";

// ── create form ───────────────────────────────────────────────────────────────

function CreateForm({ onDismiss }: { onDismiss: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState<CategoryKind>("expense");
  const [color, setColor] = useState(PRESET_COLORS[0]);
  const [error, setError] = useState("");
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => createCategory({ name: name.trim(), kind, color }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["categories"] });
      setName("");
      setError("");
      onDismiss();
    },
    onError: (err: any) => {
      const detail = err?.response?.data?.detail;
      setError(detail ?? "No se pudo crear la categoría.");
    },
  });

  function submit() {
    if (!name.trim()) {
      setError("El nombre es requerido.");
      return;
    }
    setError("");
    mutation.mutate();
  }

  return (
    <View style={styles.createForm}>
      <Text style={styles.createFormTitle}>Nueva categoría</Text>

      <TextInput
        style={styles.input}
        value={name}
        onChangeText={setName}
        placeholder="Nombre"
        placeholderTextColor={Colors.textMuted}
        returnKeyType="done"
        maxLength={100}
      />

      {/* kind pills */}
      <View style={styles.kindRow}>
        {(["expense", "income", "both"] as CategoryKind[]).map((k) => (
          <Pressable
            key={k}
            style={[styles.kindPill, kind === k && styles.kindPillActive]}
            onPress={() => setKind(k)}
          >
            <Text
              style={[
                styles.kindPillText,
                kind === k && styles.kindPillTextActive,
              ]}
            >
              {KIND_LABELS[k]}
            </Text>
          </Pressable>
        ))}
      </View>

      {/* color swatches */}
      <View style={styles.colorRow}>
        {PRESET_COLORS.map((c) => (
          <Pressable
            key={c}
            style={[
              styles.swatch,
              { backgroundColor: c },
              color === c && styles.swatchSelected,
            ]}
            onPress={() => setColor(c)}
          >
            {color === c ? (
              <Feather name="check" size={12} color="#FFF" />
            ) : null}
          </Pressable>
        ))}
      </View>

      {error ? <Text style={styles.errorText}>{error}</Text> : null}

      <View style={styles.createFormActions}>
        <Pressable style={styles.cancelBtn} onPress={onDismiss}>
          <Text style={styles.cancelBtnText}>Cancelar</Text>
        </Pressable>
        <Pressable
          style={[styles.saveBtn, mutation.isPending && { opacity: 0.6 }]}
          onPress={submit}
          disabled={mutation.isPending}
        >
          {mutation.isPending ? (
            <ActivityIndicator size="small" color={Colors.textOnDark} />
          ) : (
            <Text style={styles.saveBtnText}>Crear</Text>
          )}
        </Pressable>
      </View>
    </View>
  );
}

// ── category row ──────────────────────────────────────────────────────────────

function CategoryRow({
  category,
  onArchive,
  onRestore,
  isPending,
}: {
  category: CategoryResponse;
  onArchive: (id: string) => void;
  onRestore: (id: string) => void;
  isPending: boolean;
}) {
  return (
    <View style={[styles.row, category.archived && styles.rowArchived]}>
      <View style={[styles.colorDot, { backgroundColor: category.color }]} />
      <View style={styles.rowMid}>
        <Text
          style={[styles.rowName, category.archived && styles.rowNameMuted]}
          numberOfLines={1}
        >
          {category.name}
        </Text>
        <Text style={styles.rowMeta}>
          {KIND_LABELS[category.kind as CategoryKind] ?? category.kind}
          {category.is_default ? "  •  Default" : ""}
          {category.transaction_count > 0
            ? `  •  ${category.transaction_count} tx`
            : ""}
        </Text>
      </View>

      {isPending ? (
        <ActivityIndicator size="small" color={Colors.textMuted} />
      ) : category.archived ? (
        <Pressable
          style={styles.actionChip}
          onPress={() => onRestore(category.id)}
        >
          <Feather name="refresh-cw" size={12} color={Colors.income} />
          <Text style={[styles.actionChipText, { color: Colors.income }]}>
            Restaurar
          </Text>
        </Pressable>
      ) : category.is_default ? (
        <View style={[styles.actionChip, styles.actionChipDisabled]}>
          <Text style={[styles.actionChipText, { color: Colors.textMuted }]}>
            Default
          </Text>
        </View>
      ) : (
        <Pressable
          style={styles.actionChip}
          onPress={() => onArchive(category.id)}
        >
          <Feather name="archive" size={12} color={Colors.textMuted} />
          <Text style={[styles.actionChipText, { color: Colors.textMuted }]}>
            Archivar
          </Text>
        </Pressable>
      )}
    </View>
  );
}

// ── main screen ───────────────────────────────────────────────────────────────

export function CategoriesScreen() {
  const [showArchived, setShowArchived] = useState(false);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const categoriesQuery = useQuery({
    queryKey: ["categories", showArchived],
    queryFn: () => fetchCategories(showArchived),
  });

  const categories = categoriesQuery.data ?? [];
  const isLoading = categoriesQuery.isLoading && !refreshing;

  async function handleArchive(id: string) {
    setPendingId(id);
    try {
      await archiveCategory(id);
      queryClient.invalidateQueries({ queryKey: ["categories"] });
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      Alert.alert("Error", detail ?? "No se pudo archivar la categoría.");
    } finally {
      setPendingId(null);
    }
  }

  async function handleRestore(id: string) {
    setPendingId(id);
    try {
      await restoreCategory(id);
      queryClient.invalidateQueries({ queryKey: ["categories"] });
    } catch {
      Alert.alert("Error", "No se pudo restaurar la categoría.");
    } finally {
      setPendingId(null);
    }
  }

  async function onRefresh() {
    setRefreshing(true);
    await categoriesQuery.refetch();
    setRefreshing(false);
  }

  return (
    <SafeAreaView style={styles.safe} edges={["top"]}>
      <View style={styles.header}>
        <View>
          <Text style={styles.headerTitle}>Categorías</Text>
          <Text style={styles.headerSub}>Personalizadas y predefinidas</Text>
        </View>
        <View style={styles.headerActions}>
          <Pressable
            style={({ pressed }) => [
              styles.toggleBtn,
              showArchived && styles.toggleBtnActive,
              pressed && { opacity: 0.7 },
            ]}
            onPress={() => setShowArchived((v) => !v)}
          >
            <Feather
              name="archive"
              size={13}
              color={showArchived ? Colors.accent : Colors.textMuted}
            />
          </Pressable>
          <Pressable
            style={({ pressed }) => [
              styles.addBtn,
              pressed && { opacity: 0.7 },
            ]}
            onPress={() => setShowCreateForm((v) => !v)}
          >
            <Feather
              name={showCreateForm ? "x" : "plus"}
              size={16}
              color={Colors.textOnDark}
            />
          </Pressable>
        </View>
      </View>

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={Colors.accent} />
        </View>
      ) : (
        <FlatList
          data={categories}
          keyExtractor={(c) => c.id}
          contentContainerStyle={
            categories.length === 0 ? styles.emptyContainer : styles.listContent
          }
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              tintColor={Colors.accent}
            />
          }
          ListHeaderComponent={
            showCreateForm ? (
              <CreateForm onDismiss={() => setShowCreateForm(false)} />
            ) : null
          }
          ListEmptyComponent={
            <View style={styles.emptyCard}>
              <Feather name="tag" size={32} color={Colors.accentSoft} />
              <Text style={styles.emptyTitle}>Sin categorías</Text>
              <Text style={styles.emptySub}>
                Las categorías se crean automáticamente al primer uso.
              </Text>
            </View>
          }
          renderItem={({ item }) => (
            <CategoryRow
              category={item}
              onArchive={handleArchive}
              onRestore={handleRestore}
              isPending={pendingId === item.id}
            />
          )}
        />
      )}
    </SafeAreaView>
  );
}

// ── styles ────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: Colors.bg },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.md,
    paddingBottom: Spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: Colors.borderLight,
    backgroundColor: Colors.bgCard,
  },
  headerTitle: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  headerSub: {
    fontSize: FontSize.sm,
    color: Colors.textMuted,
    marginTop: 2,
  },
  headerActions: { flexDirection: "row", alignItems: "center", gap: Spacing.sm },
  toggleBtn: {
    padding: 8,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  toggleBtnActive: {
    borderColor: Colors.accent,
    backgroundColor: Colors.accentBg,
  },
  addBtn: {
    width: 34,
    height: 34,
    borderRadius: Radius.md,
    backgroundColor: Colors.accent,
    alignItems: "center",
    justifyContent: "center",
  },
  center: { flex: 1, alignItems: "center", justifyContent: "center" },
  listContent: { padding: Spacing.md, gap: Spacing.sm },
  emptyContainer: { flex: 1, justifyContent: "center", padding: Spacing.md },
  emptyCard: {
    alignItems: "center",
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    padding: Spacing.xl,
    gap: Spacing.sm,
    ...CardShadow,
  },
  emptyTitle: {
    fontSize: FontSize.md,
    fontWeight: "600",
    color: Colors.textPrimary,
    marginTop: Spacing.sm,
  },
  emptySub: {
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    textAlign: "center",
    lineHeight: 20,
  },
  // create form
  createForm: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.accentSoft,
    padding: Spacing.md,
    gap: Spacing.sm,
    marginBottom: Spacing.sm,
    ...CardShadow,
  },
  createFormTitle: {
    fontSize: FontSize.sm,
    fontWeight: "700",
    color: Colors.textPrimary,
  },
  input: {
    backgroundColor: Colors.bgInput,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 8,
    fontSize: FontSize.md,
    color: Colors.textPrimary,
  },
  kindRow: { flexDirection: "row", gap: Spacing.sm },
  kindPill: {
    paddingHorizontal: Spacing.md,
    paddingVertical: 5,
    borderRadius: Radius.lg,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bgElevated,
  },
  kindPillActive: { borderColor: Colors.accent, backgroundColor: Colors.accentBg },
  kindPillText: {
    fontSize: FontSize.sm,
    fontWeight: "500",
    color: Colors.textSecondary,
  },
  kindPillTextActive: { color: Colors.accent, fontWeight: "700" },
  colorRow: { flexDirection: "row", gap: Spacing.sm },
  swatch: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
  },
  swatchSelected: {
    borderWidth: 2,
    borderColor: Colors.textPrimary,
  },
  errorText: { fontSize: FontSize.xs, color: Colors.expense },
  createFormActions: { flexDirection: "row", gap: Spacing.sm, justifyContent: "flex-end" },
  cancelBtn: {
    paddingHorizontal: Spacing.md,
    paddingVertical: 7,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
  },
  cancelBtnText: { fontSize: FontSize.sm, color: Colors.textSecondary },
  saveBtn: {
    paddingHorizontal: Spacing.md,
    paddingVertical: 7,
    borderRadius: Radius.sm,
    backgroundColor: Colors.accent,
    minWidth: 72,
    alignItems: "center",
  },
  saveBtnText: { fontSize: FontSize.sm, fontWeight: "700", color: Colors.textOnDark },
  // row
  row: {
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.md,
    borderWidth: 1,
    borderColor: Colors.border,
    paddingHorizontal: Spacing.md,
    paddingVertical: Spacing.sm + 2,
    gap: Spacing.sm,
    ...CardShadow,
  },
  rowArchived: { opacity: 0.6, borderStyle: "dashed" },
  colorDot: { width: 12, height: 12, borderRadius: 6 },
  rowMid: { flex: 1, gap: 2 },
  rowName: { fontSize: FontSize.md, fontWeight: "600", color: Colors.textPrimary },
  rowNameMuted: { color: Colors.textMuted },
  rowMeta: { fontSize: FontSize.xs, color: Colors.textMuted },
  actionChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: Spacing.sm,
    paddingVertical: 4,
    borderRadius: Radius.sm,
    borderWidth: 1,
    borderColor: Colors.border,
    backgroundColor: Colors.bgElevated,
  },
  actionChipDisabled: { opacity: 0.5 },
  actionChipText: { fontSize: FontSize.xs, fontWeight: "600" },
});
