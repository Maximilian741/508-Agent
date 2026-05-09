/**
 * SketchChip - rounded-pill chip with optional highlighter background.
 *
 * Used for tags, severities, and quick filters. The `highlighter` prop
 * paints the chip in a pastel marker color from the sketch palette
 * instead of the neutral paper background.
 */
import { ReactNode } from "react";
import { Pressable, StyleSheet, Text, View, ViewStyle, StyleProp } from "react-native";

import { highlighterColor, HighlighterTone, sketchPalette, sketchFontFamily } from "./fonts";

export type SketchChipTone = "default" | "ink" | "muted";

interface SketchChipProps {
  label: string;
  tone?: SketchChipTone;
  /** When set, paints the chip in a highlighter pastel. */
  highlighter?: HighlighterTone;
  icon?: ReactNode;
  onPress?: () => void;
  style?: StyleProp<ViewStyle>;
  accessibilityLabel?: string;
}

export function SketchChip({
  label,
  tone = "default",
  highlighter,
  icon,
  onPress,
  style,
  accessibilityLabel,
}: SketchChipProps) {
  const bg = highlighter ? highlighterColor(highlighter) : sketchPalette.paper;
  const textColor = tone === "muted" ? sketchPalette.pencil : sketchPalette.ink;

  const content = (
    <>
      {icon}
      <Text style={[styles.label, { color: textColor }]}>{label}</Text>
    </>
  );

  if (onPress) {
    return (
      <Pressable
        onPress={onPress}
        accessibilityRole="button"
        accessibilityLabel={accessibilityLabel ?? label}
        style={({ pressed }: any) => [
          styles.base,
          { backgroundColor: bg },
          pressed ? { opacity: 0.85 } : null,
          style,
        ]}
      >
        {content}
      </Pressable>
    );
  }
  return (
    <View style={[styles.base, { backgroundColor: bg }, style]}>{content}</View>
  );
}

const styles = StyleSheet.create({
  base: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    paddingHorizontal: 10,
    paddingVertical: 3,
    borderRadius: 999,
    borderWidth: 1.5,
    borderColor: sketchPalette.ink,
  },
  label: {
    fontSize: 12,
    fontFamily: sketchFontFamily.body,
    fontWeight: "700",
  },
});
