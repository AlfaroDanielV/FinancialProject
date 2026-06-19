/**
 * Phase 7h — DonutChart (react-native-svg).
 *
 * On-palette donut for category / class breakdowns. Each segment is a circle
 * stroke with a dash gap (the standard RN donut technique); the center shows
 * the total. Color = meaning only — callers pass segment colors from the
 * theme / ENVELOPE_CLASS_COLORS, never arbitrary hues.
 */
import { View, Text, StyleSheet } from "react-native";
import Svg, { Circle, G } from "react-native-svg";

import { Colors, Fonts, FontSize, Spacing } from "../../theme";
import { formatMoney } from "../../lib/format";

export interface DonutSegment {
  label: string;
  value: number;
  color: string;
}

interface Props {
  data: DonutSegment[];
  currency: string;
  size?: number;
  strokeWidth?: number;
}

export function DonutChart({ data, currency, size = 168, strokeWidth = 22 }: Props) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const total = data.reduce((s, d) => s + Math.max(0, d.value), 0);

  // Build cumulative offsets so segments sit end-to-end starting at 12 o'clock.
  let acc = 0;
  const arcs = total > 0
    ? data
        .filter((d) => d.value > 0)
        .map((d) => {
          const frac = d.value / total;
          const arc = {
            color: d.color,
            dash: frac * circumference,
            offset: acc * circumference,
          };
          acc += frac;
          return arc;
        })
    : [];

  return (
    <View style={styles.wrap}>
      <View style={{ width: size, height: size }}>
        <Svg width={size} height={size}>
          <G rotation={-90} origin={`${size / 2}, ${size / 2}`}>
            {/* track */}
            <Circle
              cx={size / 2}
              cy={size / 2}
              r={radius}
              stroke={Colors.bgElevated}
              strokeWidth={strokeWidth}
              fill="none"
            />
            {arcs.map((a, i) => (
              <Circle
                key={i}
                cx={size / 2}
                cy={size / 2}
                r={radius}
                stroke={a.color}
                strokeWidth={strokeWidth}
                fill="none"
                strokeDasharray={`${a.dash} ${circumference - a.dash}`}
                strokeDashoffset={-a.offset}
                strokeLinecap="butt"
              />
            ))}
          </G>
        </Svg>
        <View style={styles.center} pointerEvents="none">
          <Text style={styles.centerLabel}>TOTAL</Text>
          <Text style={styles.centerValue}>{formatMoney(total, currency)}</Text>
        </View>
      </View>

      <View style={styles.legend}>
        {data
          .filter((d) => d.value > 0)
          .map((d) => (
            <View key={d.label} style={styles.legendRow}>
              <View style={[styles.dot, { backgroundColor: d.color }]} />
              <Text style={styles.legendLabel} numberOfLines={1}>
                {d.label}
              </Text>
              <Text style={styles.legendValue}>{formatMoney(d.value, currency)}</Text>
            </View>
          ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { alignItems: "center", gap: Spacing.md },
  center: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center",
  },
  centerLabel: {
    fontFamily: Fonts.semibold,
    fontSize: FontSize.xs,
    color: Colors.textMuted,
    letterSpacing: 1,
  },
  centerValue: {
    fontFamily: Fonts.bold,
    fontSize: FontSize.md,
    color: Colors.textPrimary,
    fontVariant: ["tabular-nums"],
    marginTop: 2,
  },
  legend: { alignSelf: "stretch", gap: Spacing.xs },
  legendRow: { flexDirection: "row", alignItems: "center", gap: Spacing.sm },
  dot: { width: 10, height: 10, borderRadius: 5 },
  legendLabel: {
    flex: 1,
    fontFamily: Fonts.regular,
    fontSize: FontSize.sm,
    color: Colors.textPrimary,
  },
  legendValue: {
    fontFamily: Fonts.medium,
    fontSize: FontSize.sm,
    color: Colors.textSecondary,
    fontVariant: ["tabular-nums"],
  },
});
