/**
 * Category picker bottom sheet — single source of truth for category selection.
 *
 * Lists the caller's active categories from the Categorías screen
 * (user_categories), filtered by `kind` so an expense only offers expense/both
 * categories (and income only income/both). Calls `onSelect({ id, name } | null)`
 * and closes — it does NOT mutate anything itself, so it's reused by the
 * transaction edit modal and the recurring-bill form. To add a new category the
 * user goes to the Categorías screen (no free-text entry here).
 *
 * Mirrors EnvelopePickerModal's sheet pattern.
 */
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { Feather } from "@expo/vector-icons";
import { useQuery } from "@tanstack/react-query";

import {
  type CategoryKind,
  type CategoryResponse,
  fetchCategories,
} from "../api/categories";
import { Colors, FontSize, Radius, Spacing } from "../theme";

interface Props {
  visible: boolean;
  /** Filter the list to categories valid for this transaction sign. */
  kind: "income" | "expense" | null;
  currentCategoryId?: string | null;
  /** Show a "Sin categoría" row that returns null (clears). Default true. */
  allowClear?: boolean;
  onClose: () => void;
  onSelect: (category: { id: string; name: string } | null) => void;
}

function matchesKind(cat: CategoryResponse, kind: Props["kind"]): boolean {
  if (kind == null) return true;
  const allowed: CategoryKind[] = kind === "income" ? ["income", "both"] : ["expense", "both"];
  return allowed.includes(cat.kind);
}

export function CategoryPickerModal({
  visible,
  kind,
  currentCategoryId,
  allowClear = true,
  onClose,
  onSelect,
}: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["categories", "active"],
    queryFn: () => fetchCategories(false),
    enabled: visible,
  });

  const categories = (data ?? []).filter((c) => matchesKind(c, kind));

  return (
    <Modal visible={visible} animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.overlay}>
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.sheet}>
          <View style={styles.handle} />
          <Text style={styles.title}>Elegí una categoría</Text>

          {isLoading ? (
            <View style={styles.center}>
              <ActivityIndicator color={Colors.accent} />
            </View>
          ) : isError ? (
            <Text style={styles.muted}>No se pudieron cargar las categorías.</Text>
          ) : categories.length === 0 ? (
            <Text style={styles.muted}>
              No tenés categorías todavía. Creá una en la pestaña Categorías.
            </Text>
          ) : (
            <ScrollView
              style={styles.body}
              showsVerticalScrollIndicator={false}
              keyboardShouldPersistTaps="handled"
            >
              {allowClear && (
                <Pressable
                  onPress={() => onSelect(null)}
                  style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
                >
                  <View style={[styles.dot, { backgroundColor: Colors.border }]} />
                  <Text style={styles.rowName}>Sin categoría</Text>
                  {currentCategoryId == null && (
                    <Feather name="check" size={18} color={Colors.accent} />
                  )}
                </Pressable>
              )}

              {categories.map((cat) => (
                <Pressable
                  key={cat.id}
                  onPress={() => onSelect({ id: cat.id, name: cat.name })}
                  style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
                >
                  <View style={[styles.dot, { backgroundColor: cat.color }]} />
                  <Text style={styles.rowName}>{cat.name}</Text>
                  {currentCategoryId === cat.id && (
                    <Feather name="check" size={18} color={Colors.accent} />
                  )}
                </Pressable>
              ))}
            </ScrollView>
          )}

          <Pressable
            onPress={onClose}
            style={({ pressed }) => [styles.cancel, pressed && { opacity: 0.75 }]}
          >
            <Text style={styles.cancelText}>Cerrar</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, justifyContent: "flex-end" },
  backdrop: { ...StyleSheet.absoluteFillObject, backgroundColor: "rgba(0,0,0,0.35)" },
  sheet: {
    backgroundColor: Colors.bg,
    borderTopLeftRadius: Radius.lg,
    borderTopRightRadius: Radius.lg,
    paddingHorizontal: Spacing.md,
    paddingTop: Spacing.sm,
    paddingBottom: Spacing.xl,
    maxHeight: "82%",
  },
  handle: {
    alignSelf: "center",
    width: 36,
    height: 4,
    borderRadius: 2,
    backgroundColor: Colors.border,
    marginBottom: Spacing.sm,
  },
  title: {
    fontSize: FontSize.lg,
    fontWeight: "700",
    color: Colors.textPrimary,
    marginBottom: Spacing.sm,
  },
  body: { flexGrow: 0 },
  center: { paddingVertical: Spacing.xl, alignItems: "center" },
  muted: {
    color: Colors.textMuted,
    fontSize: FontSize.sm,
    paddingVertical: Spacing.lg,
    textAlign: "center",
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: Spacing.sm + 2,
    gap: Spacing.sm,
  },
  rowPressed: { opacity: 0.6 },
  rowName: { flex: 1, fontSize: FontSize.md, color: Colors.textPrimary },
  dot: { width: 10, height: 10, borderRadius: 5 },
  cancel: {
    marginTop: Spacing.md,
    borderWidth: 1,
    borderColor: Colors.border,
    borderRadius: Radius.md,
    paddingVertical: Spacing.md,
    alignItems: "center",
  },
  cancelText: { color: Colors.textSecondary, fontSize: FontSize.md, fontWeight: "500" },
});
