/**
 * Card — themed surface.
 *
 * Two deliberate personalities (the authored "two-radius" signature):
 *   - variant="data" (default for tables, panels, stat tiles, anything
 *     utility): right-angled (0 radius), hairline border, NO shadow — the
 *     border does the separating. Reads like a typeset document panel.
 *   - variant="content": a single small 4px radius with a close, downward
 *     contact shadow — for prose / marketing cards.
 *
 * No mount animation and an OPAQUE background on every platform: the old
 * fade-up + translucent glass were template tells. `featured` adds a 2px ember
 * left-rule (not a glow) to mark the one primary panel on a screen.
 */

import { ReactNode } from "react";
import { Platform, StyleProp, StyleSheet, View, ViewStyle } from "react-native";

import { useTheme } from "../useTheme";

interface CardProps {
  children: ReactNode;
  style?: StyleProp<ViewStyle>;
  variant?: "data" | "content";
  featured?: boolean;
  /** Accepted for back-compat; ignored (cards no longer animate on mount). */
  animate?: boolean;
}

export function Card({ children, style, variant = "content", featured = false }: CardProps) {
  const theme = useTheme();
  const isDark = theme.colors.bg === "#150E08";
  const isData = variant === "data";

  const shadowWeb = isData
    ? theme.shadows.flat.web
    : isDark
      ? theme.shadows.near.webDark
      : theme.shadows.near.web;

  return (
    <View
      style={[
        {
          backgroundColor: theme.colors.surface,
          borderRadius: isData ? theme.radius.none : theme.radius.md,
          borderWidth: theme.border.thin,
          borderColor: theme.colors.border,
          paddingHorizontal: 20,
          paddingVertical: 18,
          ...(featured
            ? { borderLeftWidth: theme.border.medium, borderLeftColor: theme.colors.accent }
            : {}),
          ...(Platform.OS === "web"
            ? ({ boxShadow: shadowWeb } as any)
            : isData
              ? {}
              : theme.shadows.near.rn),
        },
        style,
      ]}
    >
      {children}
    </View>
  );
}
