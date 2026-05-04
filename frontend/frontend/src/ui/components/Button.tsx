/**
 * Button - mouse-tracking interactive control.
 *
 * On web the button hosts a radial gradient that follows the cursor on
 * hover (the "spotlight" effect) and a soft accent-tinted glow. Pressing
 * scales down to 0.97 with a snappy ease. Falls back to a clean static
 * style on native and on browsers that lack pointer events.
 *
 * Variants: primary (filled accent), secondary (raised surface), ghost
 * (outline only), danger (filled danger).
 */
import React, { ReactNode, useRef, useState } from "react";
import {
  ActivityIndicator,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
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

  // Local cursor coords for the spotlight gradient. Only updated on web.
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const [hovered, setHovered] = useState(false);
  const ref = useRef<any>(null);

  const onPointerMove = (e: any) => {
    if (Platform.OS !== "web") return;
    const node: any = ref.current;
    const host = node && (node.getBoundingClientRect ? node : node._node || node);
    if (!host || !host.getBoundingClientRect) return;
    const r = host.getBoundingClientRect();
    setCursor({ x: e.clientX - r.left, y: e.clientY - r.top });
  };
  const onPointerLeave = () => {
    setHovered(false);
    setCursor(null);
  };
  const onPointerEnter = () => {
    setHovered(true);
  };

  // Spotlight gradient layer for hover (web only). The center follows the cursor;
  // the tint depends on variant. The layer sits above the bg, below the label.
  const spotlightTint =
    variant === "primary" || variant === "danger"
      ? "rgba(255,255,255,0.28)"
      : theme.colors.accent + "33";
  const spotlightStyle: any =
    Platform.OS === "web" && hovered && cursor
      ? {
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          pointerEvents: "none",
          backgroundImage:
            "radial-gradient(140px circle at " +
            cursor.x +
            "px " +
            cursor.y +
            "px, " +
            spotlightTint +
            ", transparent 60%)",
          // smooth fade as cursor moves
          transition: "background-image 60ms linear",
        }
      : null;

  // Animated glow shadow on hover for primary / danger.
  const glowShadow =
    Platform.OS === "web" && hovered && (variant === "primary" || variant === "danger")
      ? ({
          // @ts-ignore - RN-Web maps boxShadow
          boxShadow:
            "0 0 0 1px " +
            theme.colors.accent +
            "55, 0 8px 24px " +
            theme.colors.accent +
            "33",
        } as any)
      : null;

  return (
    <Pressable
      ref={ref}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? title}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: isDisabled, busy: loading }}
      onPress={onPress}
      disabled={isDisabled}
      // @ts-ignore - RN-Web supports pointer events
      onHoverIn={onPointerEnter}
      // @ts-ignore
      onHoverOut={onPointerLeave}
      // @ts-ignore - we use onPointerMove only on web; ignored elsewhere
      onPointerMove={onPointerMove}
      style={({ pressed, focused }: any) => [
        styles.base,
        styles[variant],
        // Web: animate hover/transition smoothly
        Platform.OS === "web"
          ? ({
              // @ts-ignore
              transition:
                "transform 130ms cubic-bezier(0.2, 0.7, 0.3, 1), box-shadow 220ms ease, background-color 180ms ease, opacity 180ms ease",
            } as any)
          : null,
        hovered && !isDisabled ? styles[`hovered_${variant}`] : null,
        glowShadow,
        pressed && !isDisabled ? styles.pressed : null,
        focused ? styles.focused : null,
        isDisabled ? styles.disabled : null,
        style,
      ]}
    >
      {Platform.OS === "web" && spotlightStyle ? <View style={spotlightStyle} /> : null}
      {loading ? (
        <ActivityIndicator color={styles.text.color} />
      ) : (
        <>
          {icon}
          <Text style={[styles.text, styles[`text_${variant}`], styles.label]}>{title}</Text>
        </>
      )}
    </Pressable>
  );
}

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    base: {
      position: "relative",
      flexDirection: "row",
      alignItems: "center",
      justifyContent: "center",
      gap: theme.spacing.sm,
      paddingVertical: theme.spacing.sm,
      paddingHorizontal: theme.spacing.lg,
      borderRadius: theme.radius.md,
      overflow: "hidden",
    },
    label: {
      position: "relative",
      zIndex: 2,
    },
    primary: {
      backgroundColor: theme.colors.accent,
    },
    secondary: {
      backgroundColor: theme.colors.surface2,
      borderWidth: 1,
      borderColor: theme.colors.border,
    },
    ghost: {
      backgroundColor: "transparent",
      borderWidth: 1,
      borderColor: theme.colors.border,
    },
    danger: {
      backgroundColor: theme.colors.danger,
    },
    text: {
      ...theme.typography.body,
      color: theme.colors.text,
    },
    text_primary: { color: "#FFFFFF", fontWeight: "700" },
    text_secondary: { color: theme.colors.text, fontWeight: "700" },
    text_ghost: { color: theme.colors.text, fontWeight: "700" },
    text_danger: { color: "#FFFFFF", fontWeight: "700" },
    // Hover treatments per variant (web only; native has no real hover).
    hovered_primary: {
      // tiny lift
      transform: [{ translateY: -1 }],
    },
    hovered_secondary: {
      borderColor: theme.colors.accent,
      transform: [{ translateY: -1 }],
    },
    hovered_ghost: {
      borderColor: theme.colors.accent,
      backgroundColor: theme.colors.accent + "0F",
    },
    hovered_danger: {
      transform: [{ translateY: -1 }],
    },
    pressed: {
      opacity: 0.92,
      transform: [{ scale: 0.97 }],
    },
    focused: {
      // @ts-ignore - web outline shorthand
      outlineColor: theme.colors.accent,
      outlineWidth: 2,
      outlineStyle: "solid",
      outlineOffset: 2,
    } as any,
    disabled: {
      opacity: 0.5,
    },
  });
