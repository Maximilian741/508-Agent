/**
 * Hero — top-of-page billboard.
 *
 * Renders a gradient banner on web (CSS linear-gradient) and a flat themed
 * surface on native.  Used on the home screen to make the app feel like a
 * real product instead of a stack of cards.
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
}

export function Hero({ eyebrow, title, subtitle, rightSlot, children }: HeroProps) {
  const theme = useTheme();
  const gradient =
    Platform.OS === "web"
      ? // @ts-ignore RN-Web style key
        ({ backgroundImage: `linear-gradient(135deg, ${theme.colors.accent} 0%, #1E3A8A 100%)` } as any)
      : { backgroundColor: theme.colors.accent };

  return (
    <View
      // @ts-ignore — region landmark
      accessibilityRole={Platform.OS === "web" ? ("region" as any) : undefined}
      style={[styles.wrap, gradient]}
    >
      <View style={{ flex: 1, gap: 6 }}>
        {eyebrow ? (
          <Text style={styles.eyebrow}>{eyebrow}</Text>
        ) : null}
        <Text style={styles.title}>{title}</Text>
        {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
        {children}
      </View>
      {rightSlot ? <View style={styles.right}>{rightSlot}</View> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flexDirection: "row",
    alignItems: "center",
    gap: 16,
    padding: 24,
    borderRadius: 16,
    flexWrap: "wrap",
  },
  eyebrow: {
    color: "#FFFFFF",
    opacity: 0.85,
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 1.4,
    textTransform: "uppercase",
  },
  title: {
    color: "#FFFFFF",
    fontSize: 28,
    fontWeight: "800",
    letterSpacing: -0.4,
  },
  subtitle: {
    color: "#FFFFFF",
    opacity: 0.85,
    fontSize: 15,
    lineHeight: 22,
    maxWidth: 600,
  },
  right: { marginLeft: "auto" },
});
