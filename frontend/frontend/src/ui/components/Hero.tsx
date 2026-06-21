/**
 * Hero — top-of-page billboard.
 *
 * A flat, warm typeset panel (surface2) with a hairline bottom rule — no
 * animated shader, no gradient, no white-text-over-smoke. The serif title and
 * ember eyebrow sit directly on the panel in palette colours so contrast is
 * honest in every theme. The `shader`/`shaderOpacity` props are accepted but
 * ignored (kept so existing call sites compile) — the shader system is retired.
 */
import React, { ReactNode } from "react";
import { Platform, StyleSheet, Text, View } from "react-native";

import { useTheme } from "../useTheme";

export interface HeroProps {
  eyebrow?: string;
  title: string;
  subtitle?: string;
  rightSlot?: ReactNode;
  children?: ReactNode;
  /** Retired — accepted for back-compat, no longer renders anything. */
  shader?: string;
  shaderOpacity?: number;
}

export function Hero({ eyebrow, title, subtitle, rightSlot, children }: HeroProps) {
  const theme = useTheme();

  return (
    <View
      // @ts-ignore - region landmark
      accessibilityRole={Platform.OS === "web" ? ("region" as any) : undefined}
      style={[
        styles.wrap,
        {
          backgroundColor: theme.colors.surface2,
          borderBottomWidth: theme.border.medium,
          borderBottomColor: theme.colors.accent,
        },
      ]}
    >
      <View style={[styles.content, { gap: 6 }]}>
        {eyebrow ? (
          <Text style={[styles.eyebrow, { color: theme.colors.accent }]}>{eyebrow}</Text>
        ) : null}
        <Text
          style={[styles.title, { color: theme.colors.text }]}
          // Page title = the h1 of every screen. We sell heading structure;
          // the app must have it too.
          accessibilityRole="header"
          {...(Platform.OS === "web" ? ({ "aria-level": 1 } as any) : {})}
        >
          {title}
        </Text>
        {subtitle ? (
          <Text style={[styles.subtitle, { color: theme.colors.textMuted }]}>{subtitle}</Text>
        ) : null}
        {children}
      </View>
      {rightSlot ? <View style={styles.right}>{rightSlot}</View> : null}
    </View>
  );
}

const serif = Platform.select({
  ios: "Iowan Old Style, Charter, Georgia, serif",
  android: "serif",
  default: "'Iowan Old Style', 'Charter', 'Georgia', serif",
}) as any;

const styles = StyleSheet.create({
  wrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 16,
    padding: 28,
    borderRadius: 0, // square top-of-page panel — part of the dossier look
    flexWrap: "wrap",
    minHeight: 150,
  },
  content: { flex: 1 },
  eyebrow: {
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 1.4,
    textTransform: "uppercase",
  },
  title: {
    fontFamily: serif,
    fontSize: 42,
    fontWeight: "700",
    letterSpacing: -0.2,
    lineHeight: 48,
  },
  subtitle: {
    fontSize: 15,
    lineHeight: 22,
    maxWidth: 640,
  },
  right: { marginLeft: "auto" },
});

export default Hero;
