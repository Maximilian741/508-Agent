/**
 * Button — clean static control.
 *
 * No cursor-tracking spotlight and no accent glow (both were "fancy generated
 * app" tells). Hover gives a 1px lift (+ a close contact shadow on filled
 * variants); press settles back with a small scale. The keyboard focus ring is
 * a 2px ember outline at 2px offset — WCAG-critical, kept exactly.
 *
 * Variants: primary (filled accent), secondary (raised surface), ghost
 * (outline only), danger (filled danger).
 */
import React, { ReactNode, useState } from "react";
import {
  ActivityIndicator,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  ViewStyle,
} from "react-native";

import { useTheme } from "../useTheme";

type Variant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps {
  title: string;
  onPress: () => void;
  variant?: Variant;
  loading?: boolean;
  disabled?: boolean;
  style?: ViewStyle;
  icon?: ReactNode;
  accessibilityLabel?: string;
  accessibilityHint?: string;
}

export function Button({
  title,
  onPress,
  variant = "primary",
  loading = false,
  disabled = false,
  style,
  icon,
  accessibilityLabel,
  accessibilityHint,
}: ButtonProps) {
  const theme = useTheme();
  const styles = createStyles(theme);
  const isDisabled = disabled || loading;
  const [hovered, setHovered] = useState(false);
  const isDark = theme.colors.bg === "#150E08";
  const filled = variant === "primary" || variant === "danger";

  const hoverShadow =
    Platform.OS === "web" && hovered && !isDisabled && filled
      ? ({ boxShadow: isDark ? theme.shadows.near.webDark : theme.shadows.near.web } as any)
      : null;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? title}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: isDisabled, busy: loading }}
      onPress={onPress}
      disabled={isDisabled}
      // @ts-ignore - RN-Web hover events
      onHoverIn={() => setHovered(true)}
      // @ts-ignore
      onHoverOut={() => setHovered(false)}
      style={({ pressed, focused }: any) => [
        styles.base,
        styles[variant],
        Platform.OS === "web"
          ? ({
              // @ts-ignore
              transition: "transform 120ms ease, box-shadow 160ms ease, background-color 160ms ease, border-color 160ms ease, opacity 160ms ease",
            } as any)
          : null,
        hovered && !isDisabled ? styles[`hovered_${variant}`] : null,
        hoverShadow,
        pressed && !isDisabled ? styles.pressed : null,
        focused ? styles.focused : null,
        isDisabled ? styles.disabled : null,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={styles.text.color} />
      ) : (
        <>
          {icon}
          <Text style={[styles.text, styles[`text_${variant}`]]}>{title}</Text>
        </>
      )}
    </Pressable>
  );
}

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    base: {
      flexDirection: "row",
      alignItems: "center",
      justifyContent: "center",
      gap: theme.spacing.sm,
      paddingVertical: theme.spacing.sm,
      paddingHorizontal: theme.spacing.lg,
      borderRadius: theme.radius.sm,
    },
    primary: { backgroundColor: theme.colors.accent },
    secondary: {
      backgroundColor: theme.colors.surface2,
      borderWidth: theme.border.thin,
      borderColor: theme.colors.border,
    },
    ghost: {
      backgroundColor: "transparent",
      borderWidth: theme.border.thin,
      borderColor: theme.colors.border,
    },
    danger: { backgroundColor: theme.colors.danger },
    text: {
      ...theme.typography.body,
      color: theme.colors.text,
    },
    text_primary: { color: theme.colors.onAccent, fontWeight: "700" },
    text_secondary: { color: theme.colors.text, fontWeight: "700" },
    text_ghost: { color: theme.colors.text, fontWeight: "700" },
    text_danger: { color: theme.colors.onDanger, fontWeight: "700" },
    hovered_primary: { transform: [{ translateY: -1 }] },
    hovered_secondary: { borderColor: theme.colors.accent, transform: [{ translateY: -1 }] },
    hovered_ghost: { borderColor: theme.colors.accent, backgroundColor: theme.colors.accent + "0F" },
    hovered_danger: { transform: [{ translateY: -1 }] },
    pressed: { opacity: 0.92, transform: [{ scale: 0.97 }] },
    focused: {
      // @ts-ignore - web outline shorthand (WCAG focus ring)
      outlineColor: theme.colors.accent,
      outlineWidth: theme.focus.outlineWidth,
      outlineStyle: "solid",
      outlineOffset: theme.focus.outlineOffset,
    } as any,
    disabled: { opacity: 0.5 },
  });
