/**
 * Card — themed surface.
 *
 *   - variant="content" (default): a rounded glass panel — translucent
 *     surface + backdrop blur on web, soft layered shadow.
 *   - variant="data": the same glass with a slightly tighter radius and no
 *     shadow, for tables, stat tiles and dense utility panels.
 *
 * The glass colour is >= 74% opaque, so a browser without backdrop-filter
 * still shows a solid-looking panel; the contrast script measures text on the
 * glass colour composited over the page, never on the blur. `featured` gives
 * the one primary panel on a screen an accent-tinted edge.
 */

import { ReactNode } from "react";
import { Platform, StyleProp, View, ViewStyle } from "react-native";

import { alpha, glassStyle } from "../theme";
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
  const isData = variant === "data";

  const shadowWeb = isData
    ? theme.shadows.flat.web
    : theme.isDark
      ? theme.shadows.near.webDark
      : theme.shadows.near.web;
  const featuredRing = featured ? `, 0 0 0 1px ${alpha(theme.colors.accent, 0.35)}` : "";

  return (
    <View
      style={[
        {
          // Dense data panels repeat in long lists: skip the blur there (the
          // translucent colour alone reads the same over the static page).
          ...glassStyle(theme.colors, isData ? 0 : 16),
          borderRadius: isData ? theme.radius.md : theme.radius.lg,
          borderWidth: theme.border.thin,
          borderColor: featured ? alpha(theme.colors.accent, 0.45) : theme.colors.glassBorder,
          paddingHorizontal: 22,
          paddingVertical: 20,
          ...(Platform.OS === "web"
            ? ({ boxShadow: shadowWeb === "none" && featured ? featuredRing.slice(2) : shadowWeb + featuredRing } as any)
            : isData
              ? {}
              : theme.shadows.near.rn),
        } as any,
        style,
      ]}
    >
      {children}
    </View>
  );
}
