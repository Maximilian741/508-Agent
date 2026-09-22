/**
 * Button — the app's one button.
 *
 * Variants: primary (filled accent), secondary (raised glass), ghost (outline
 * only), danger (filled danger). Hover lifts 1px; the primary adds a soft
 * accent bloom. The keyboard focus ring is a 2px accent outline at 2px offset
 * — WCAG-critical, kept exactly.
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

import { alpha } from "../theme";
import { useTheme } from "../useTheme";
import { linkProps } from "./linkProps";

type Variant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps {
  title: string;
  /** Optional when `href` is given (then it runs just before navigating). */
  onPress?: () => void;
  /** Navigate here. Renders a real <a href> on web so crawlers follow it and
   *  "open in new tab" works — see linkProps. */
  href?: string;
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
  href,
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
  const nav = href ? linkProps(href, onPress) : null;

  const hoverShadow =
    Platform.OS === "web" && hovered && !isDisabled
      ? variant === "primary"
        ? ({ boxShadow: `0 0 0 1px ${alpha(theme.colors.accent, 0.5)}, 0 8px 24px -8px ${alpha(theme.colors.accent, theme.isDark ? 0.55 : 0.45)}` } as any)
        : variant === "danger"
          ? ({ boxShadow: `0 8px 24px -8px ${alpha(theme.colors.danger, 0.5)}` } as any)
          : ({ boxShadow: theme.isDark ? theme.shadows.near.webDark : theme.shadows.near.web } as any)
      : null;

  return (
    <Pressable
      {...(nav?.href ? ({ href: nav.href } as any) : null)}
      accessibilityRole={nav ? "link" : "button"}
      accessibilityLabel={accessibilityLabel ?? title}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: isDisabled, busy: loading }}
      onPress={nav ? nav.onPress : onPress}
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
              transition: "transform 140ms ease, box-shadow 200ms ease, background-color 160ms ease, border-color 160ms ease, opacity 160ms ease",
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
        <ActivityIndicator color={variant === "primary" ? theme.colors.onAccent : variant === "danger" ? theme.colors.onDanger : theme.colors.text} />
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
      minHeight: 40,
      paddingVertical: 9,
      paddingHorizontal: 18,
      borderRadius: theme.radius.md,
      borderWidth: 1,
      borderColor: "transparent",
    },
    primary: { backgroundColor: theme.colors.accent, borderColor: theme.colors.accent },
    secondary: {
      backgroundColor: theme.colors.surface2,
      borderColor: theme.colors.glassBorder,
    },
    ghost: {
      backgroundColor: "transparent",
      borderColor: theme.colors.border,
    },
    danger: { backgroundColor: theme.colors.danger, borderColor: theme.colors.danger },
    text: {
      ...theme.typography.body,
      fontSize: 14,
      lineHeight: 20,
      color: theme.colors.text,
    },
    text_primary: { color: theme.colors.onAccent, fontWeight: "600" },
    text_secondary: { color: theme.colors.text, fontWeight: "600" },
    text_ghost: { color: theme.colors.text, fontWeight: "600" },
    text_danger: { color: theme.colors.onDanger, fontWeight: "600" },
    hovered_primary: { transform: [{ translateY: -1 }] },
    hovered_secondary: { borderColor: alpha(theme.colors.accent, 0.5), transform: [{ translateY: -1 }] },
    hovered_ghost: { borderColor: alpha(theme.colors.accent, 0.6), backgroundColor: theme.colors.accentSoft },
    hovered_danger: { transform: [{ translateY: -1 }] },
    pressed: { opacity: 0.94, transform: [{ scale: 0.98 }] },
    focused: {
      // @ts-ignore - web outline shorthand (WCAG focus ring)
      outlineColor: theme.colors.accent,
      outlineWidth: theme.focus.outlineWidth,
      outlineStyle: "solid",
      outlineOffset: theme.focus.outlineOffset,
    } as any,
    disabled: { opacity: 0.5 },
  });
