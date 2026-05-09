/**
 * SketchButton - hand-drawn button with wobble border + hard-offset shadow.
 *
 * ASCII-only label by spec. Three variants: primary (filled paper2),
 * solid (filled ink), ghost (transparent). Supports a tilt and disabled
 * state. On web the press uses transform translate to simulate the
 * shadow snapping shut.
 */
import { Platform, Pressable, StyleSheet, Text, View, ViewStyle, StyleProp } from "react-native";

import { sketchPalette, sketchFontFamily } from "./fonts";

export type SketchButtonVariant = "primary" | "solid" | "ghost";

interface SketchButtonProps {
  title: string;
  onPress: () => void;
  variant?: SketchButtonVariant;
  disabled?: boolean;
  tilt?: -0.4 | 0 | 0.4;
  style?: StyleProp<ViewStyle>;
  accessibilityLabel?: string;
}

export function SketchButton({
  title,
  onPress,
  variant = "primary",
  disabled = false,
  tilt = 0,
  style,
  accessibilityLabel,
}: SketchButtonProps) {
  const bg =
    variant === "solid"
      ? sketchPalette.ink
      : variant === "ghost"
      ? "transparent"
      : sketchPalette.paper2;
  const fg = variant === "solid" ? sketchPalette.paper : sketchPalette.ink;

  const transform = tilt !== 0 ? [{ rotate: tilt + "deg" }] : undefined;

  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? title}
      accessibilityState={{ disabled }}
      style={({ pressed, focused }: any) => [
        styles.base,
        {
          backgroundColor: bg,
          transform,
          opacity: disabled ? 0.5 : 1,
        },
        Platform.OS === "web" && !pressed
          ? ({
              boxShadow: "3px 4px 0 " + sketchPalette.ink,
            } as any)
          : null,
        Platform.OS === "web" && pressed
          ? ({
              transform: [
                ...(transform ?? []),
                { translateX: 2 },
                { translateY: 2 },
              ],
              boxShadow: "1px 1px 0 " + sketchPalette.ink,
            } as any)
          : null,
        focused
          ? ({
              outlineColor: sketchPalette.accent,
              outlineWidth: 2,
              outlineStyle: "solid",
              outlineOffset: 2,
            } as any)
          : null,
        style,
      ]}
    >
      <View pointerEvents="none">
        <Text style={[styles.label, { color: fg }]}>{title}</Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderWidth: 2,
    borderColor: sketchPalette.ink,
    // Wobble corners
    borderTopLeftRadius: 6,
    borderTopRightRadius: 8,
    borderBottomRightRadius: 5,
    borderBottomLeftRadius: 7,
    alignItems: "center",
    justifyContent: "center",
  },
  label: {
    fontFamily: sketchFontFamily.body,
    fontSize: 14,
    fontWeight: "700",
    letterSpacing: 0.2,
  },
});
