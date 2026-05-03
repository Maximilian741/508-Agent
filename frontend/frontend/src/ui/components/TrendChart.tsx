/**
 * Tiny SVG sparkline chart for audit-score history.
 *
 * No external dep, no canvas — just RN-Web SVG via react-native-svg... we
 * don't have that bundled, so we render via an HTML <svg> through Platform
 * 'web' shortcut.  On native we render a simple bar fallback.
 */

import React, { useMemo } from "react";
import { Platform, StyleSheet, Text, View } from "react-native";

import { useTheme } from "../useTheme";

export interface TrendChartProps {
  /** Series of [label, score] pairs, oldest to newest. */
  points: { label: string; score: number }[];
  /** Optional total height in px. */
  height?: number;
}

export function TrendChart({ points, height = 80 }: TrendChartProps) {
  const theme = useTheme();
  const path = useMemo(() => _buildPath(points, height), [points, height]);

  if (!points.length) {
    return (
      <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
        Not enough data to chart yet — run a few audits.
      </Text>
    );
  }

  if (Platform.OS !== "web") {
    return <_BarFallback points={points} />;
  }

  // Web SVG render path
  const width = 600;
  const last = points[points.length - 1];
  const first = points[0];
  const trend =
    points.length >= 2 ? (last.score >= first.score ? "↑" : "↓") : "→";

  return (
    <View style={{ gap: 6 }}>
      {/* @ts-ignore — raw SVG on web only */}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height={height}
        preserveAspectRatio="none"
        style={{ display: "block" }}
        role="img"
        aria-label={`Score trend across ${points.length} audits`}
      >
        <defs>
          {/* @ts-ignore */}
          <linearGradient id="trend-gradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={theme.colors.accent} stopOpacity={0.3} />
            <stop offset="100%" stopColor={theme.colors.accent} stopOpacity={0} />
          </linearGradient>
        </defs>
        {/* @ts-ignore */}
        <path d={path.fill} fill="url(#trend-gradient)" />
        {/* @ts-ignore */}
        <path d={path.line} fill="none" stroke={theme.colors.accent} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />
        {points.map((p, i) => {
          const x = _x(i, points.length, width);
          const y = _y(p.score, height);
          return (
            // @ts-ignore
            <circle key={i} cx={x} cy={y} r={3} fill={theme.colors.accent} />
          );
        })}
      </svg>
      <View style={styles.summary}>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
          {points.length} run{points.length === 1 ? "" : "s"} · {first.score.toFixed(0)} → {last.score.toFixed(0)} {trend}
        </Text>
      </View>
    </View>
  );
}

function _BarFallback({ points }: { points: { label: string; score: number }[] }) {
  const theme = useTheme();
  return (
    <View style={styles.barRow}>
      {points.map((p, i) => (
        <View
          key={i}
          style={{
            flex: 1,
            height: Math.max(4, (p.score / 100) * 60),
            backgroundColor: theme.colors.accent,
            borderRadius: 2,
            opacity: 0.4 + (i / points.length) * 0.6,
          }}
        />
      ))}
    </View>
  );
}

function _x(index: number, total: number, width: number): number {
  if (total <= 1) return width / 2;
  return (index / (total - 1)) * (width - 10) + 5;
}

function _y(score: number, height: number): number {
  // 0 = bottom (worst), 100 = top (best)
  const clamped = Math.max(0, Math.min(100, score));
  return height - (clamped / 100) * (height - 8) - 4;
}

function _buildPath(
  points: { label: string; score: number }[],
  height: number,
): { line: string; fill: string } {
  if (!points.length) return { line: "", fill: "" };
  const width = 600;
  const segments: string[] = [];
  points.forEach((p, i) => {
    const x = _x(i, points.length, width);
    const y = _y(p.score, height);
    segments.push(`${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`);
  });
  const line = segments.join(" ");
  // Close polygon to bottom for the fill area.
  const firstX = _x(0, points.length, width);
  const lastX = _x(points.length - 1, points.length, width);
  const fill = `${line} L ${lastX.toFixed(2)} ${height} L ${firstX.toFixed(2)} ${height} Z`;
  return { line, fill };
}

const styles = StyleSheet.create({
  summary: { flexDirection: "row", justifyContent: "flex-end" },
  barRow: { flexDirection: "row", alignItems: "flex-end", gap: 4, height: 60 },
});
