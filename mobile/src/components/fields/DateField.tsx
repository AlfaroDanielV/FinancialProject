/**
 * Phase 7f — shared date field: a read-only touchable that opens an inline
 * calendar sheet instead of asking the user to type "AAAA-MM-DD".
 *
 * Value contract stays the ISO date string the backend expects (`DATE`,
 * day-level only — granularity rule unchanged). `@react-native-community/
 * datetimepicker@8.4.4` is the SDK 54-bundled module, so it runs inside
 * Expo Go without an EAS build. Nesting a <Modal> inside an open sheet
 * follows the EnvelopePickerModal precedent.
 */
import { useState } from "react";
import {
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type TextStyle,
  type ViewStyle,
} from "react-native";
import DateTimePicker from "@react-native-community/datetimepicker";
import { Feather } from "@expo/vector-icons";

import { Colors, FontSize, Radius, Spacing } from "../../theme";

function toIsoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function fmtLong(iso: string): string {
  const d = new Date(iso + "T12:00:00");
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("es-CR", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

interface Props {
  /** ISO `YYYY-MM-DD` or "" when unset. */
  value: string;
  onChange: (iso: string) => void;
  placeholder?: string;
  /** Style for the touchable row (use the form's input style). */
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
  minimumDate?: Date;
  maximumDate?: Date;
  disabled?: boolean;
}

export function DateField({
  value,
  onChange,
  placeholder = "Elegir fecha",
  style,
  textStyle,
  minimumDate,
  maximumDate,
  disabled = false,
}: Props) {
  const [open, setOpen] = useState(false);
  const current = value ? new Date(value + "T12:00:00") : new Date();

  return (
    <>
      <Pressable
        onPress={() => !disabled && setOpen(true)}
        style={({ pressed }) => [
          styles.fieldRow,
          style,
          pressed && !disabled && { opacity: 0.7 },
          disabled && { opacity: 0.5 },
        ]}
      >
        <Text
          style={[
            styles.fieldText,
            textStyle,
            !value && { color: Colors.textMuted },
          ]}
        >
          {value ? fmtLong(value) : placeholder}
        </Text>
        <Feather name="calendar" size={16} color={Colors.textMuted} />
      </Pressable>

      <Modal
        transparent
        visible={open}
        animationType="fade"
        onRequestClose={() => setOpen(false)}
      >
        <Pressable style={styles.backdrop} onPress={() => setOpen(false)} />
        <View style={styles.sheetWrap} pointerEvents="box-none">
          <View style={styles.sheet}>
            <DateTimePicker
              value={Number.isNaN(current.getTime()) ? new Date() : current}
              mode="date"
              display={Platform.OS === "ios" ? "inline" : "default"}
              locale="es-CR"
              themeVariant="light"
              accentColor={Colors.accent}
              minimumDate={minimumDate}
              maximumDate={maximumDate}
              onChange={(event, selected) => {
                if (Platform.OS !== "ios") setOpen(false);
                if (event.type !== "dismissed" && selected) {
                  onChange(toIsoDate(selected));
                }
              }}
            />
            <Pressable
              onPress={() => setOpen(false)}
              style={({ pressed }) => [
                styles.doneBtn,
                pressed && { opacity: 0.85 },
              ]}
            >
              <Text style={styles.doneBtnText}>Listo</Text>
            </Pressable>
          </View>
        </View>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  fieldRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  fieldText: {
    fontSize: FontSize.md,
    color: Colors.textPrimary,
  },
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(0,0,0,0.35)",
  },
  sheetWrap: {
    flex: 1,
    justifyContent: "center",
    paddingHorizontal: Spacing.lg,
  },
  sheet: {
    backgroundColor: Colors.bgCard,
    borderRadius: Radius.lg,
    padding: Spacing.md,
    gap: Spacing.sm,
  },
  doneBtn: {
    alignSelf: "stretch",
    alignItems: "center",
    backgroundColor: Colors.accent,
    borderRadius: Radius.md,
    paddingVertical: Spacing.sm + 2,
  },
  doneBtnText: {
    color: Colors.textOnDark,
    fontSize: FontSize.md,
    fontWeight: "600",
  },
});
