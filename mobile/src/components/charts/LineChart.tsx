/**
 * Phase 7h — LineChart (react-native-svg).
 *
 * Multi-series line for monthly cash-flow (income green, expense brick).
 * Minimal, on-palette: muted baseline + month labels, no heavy axes. The
 * y-scale spans 0..max so bars/lines are comparable across series.
 */
import { View, Text, StyleSheet } from "react-native";
import Svg, { Polyline, Line, Circle } from "react-native-svg";

import { Colors, Fonts, FontSize, Spacing } from "../../theme";

export interface LineSeries {
  color: string;
  values: number[];
}

interface Props {
  labels: string[]; // x-axis tick labels (e.g. "ene", "feb")
  series: LineSeries[];
  height?: number;
}

const PAD_X = 8;
const PAD_TOP = 10;
const PAD_BOTTOM = 18;

export function LineChart({ labels, series, height = 160 }: Props) {
  const n = labels.length;
  // react-native-svg needs concrete pixel coords; we lay out on a fixed
  // viewBox and let the Svg scale to its container width.
  const width = 320;
  const max = Math.max(
    1,
    ...series.flatMap((s) => s.values.map((v) => Math.abs(v))),
  );
  const innerW = width - PAD_X * 2;
  const innerH = height - PAD_TOP - PAD_BOTTOM;

  const x = (i: number) => (n <= 1 ? PAD_X : PAD_X + (innerW * i) / (n - 1));
  const y = (v: number) => PAD_TOP + innerH * (1 - Math.abs(v) / max);

  return (
    <View>
      <Svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`}>
        {/* baseline */}
        <Line
          x1={PAD_X}
          y1={PAD_TOP + innerH}
          x2={width - PAD_X}
          y2={PAD_TOP + innerH}
          stroke={Colors.borderLight}
          strokeWidth={1}
        />
        {series.map((s, si) => {
          const pts = s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
          return (
            <Polyline
              key={si}
              points={pts}
              fill="none"
              stroke={s.color}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          );
        })}
        {series.map((s, si) =>
          s.values.map((v, i) => (
            <Circle key={`${si}-${i}`} cx={x(i)} cy={y(v)} r={2.5} fill={s.color} />
          )),
        )}
      </Svg>
      <View style={styles.labels}>
        {labels.map((l, i) => (
          <Text key={i} style={styles.label} numberOfLines={1}>
            {l}
          </Text>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  labels: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: PAD_X,
    marginTop: Spacing.xs,
  },
  label: {
    fontFamily: Fonts.regular,
    fontSize: FontSize.xs,
    color: Colors.textMuted,
  },
});
