/**
 * Hero - top-of-page billboard with optional shader background.
 *
 * Pass `shader="nebula"` (or any variant) and the hero uses an animated
 * shader as its background instead of a flat gradient. Text gets a soft
 * shadow so it stays readable over the moving smoke.
 */
import React, { ReactNode } from "react";
import { Platform, StyleSheet, Text, View } from "react-native";

import { useTheme } from "../useTheme";
import { PixelFrame } from "./PixelFrame";
import { ShaderCanvas, ShaderVariant } from "./ShaderCanvas";

export interface HeroProps {
  eyebrow?: string;
  title: string;
  subtitle?: string;
  rightSlot?: ReactNode;
  children?: ReactNode;
  shader?: ShaderVariant;
  shaderOpacity?: number;
}

export function Hero({
  eyebrow,
  title,
  subtitle,
  rightSlot,
  children,
  shader,
  shaderOpacity = 0.55,
}: HeroProps) {
  const theme = useTheme();

  // Default flat gradient when no shader is requested - matches old behavior.
  const flatBg =
    Platform.OS === "web"
      ? ({ backgroundImage: "linear-gradient(135deg, " + theme.colors.accent + " 0%, #1E3A8A 100%)" } as any)
      : { backgroundColor: theme.colors.accent };

  return (
    <View
      // @ts-ignore - region landmark
      accessibilityRole={Platform.OS === "web" ? ("region" as any) : undefined}
      style={[styles.wrap, shader ? styles.shaderWrap : flatBg]}
    >
      {shader ? (
        <>
          {/* Dark base so light text stays readable on bright variants. */}
          <View style={styles.shaderBase} />
          <ShaderCanvas variant={shader} opacity={shaderOpacity} />
          <PixelFrame size={16} thickness={3} color={theme.colors.accent} inset={8} />
        </>
      ) : null}

      <View style={[styles.content, { gap: 6 }]}>
        {eyebrow ? <Text style={styles.eyebrow}>{eyebrow}</Text> : null}
        <Text
          style={styles.title}
          // Page title = the h1 of every screen. We sell heading structure;
          // the app must have it too.
          accessibilityRole="header"
          {...(Platform.OS === "web" ? ({ "aria-level": 1 } as any) : {})}
        >
          {title}
        </Text>
        {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
        {children}
      </View>
      {rightSlot ? <View style={[styles.right, { position: "relative", zIndex: 2 }]}>{rightSlot}</View> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: "relative",
    flexDirection: "row",
    alignItems: "center",
    gap: 16,
    padding: 28,
    borderRadius: 18,
    flexWrap: "wrap",
    overflow: "hidden",
    minHeight: 160,
  },
  shaderWrap: {
    backgroundColor: "#0B1020",
  },
  shaderBase: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "#0B1020",
  },
  content: {
    flex: 1,
    position: "relative",
    zIndex: 2,
  },
  eyebrow: {
    color: "#FFFFFF",
    opacity: 0.92,
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 1.4,
    textTransform: "uppercase",
    // @ts-ignore - web text shadow for legibility over shader
    textShadow: Platform.OS === "web" ? "0 1px 6px rgba(0,0,0,0.45)" : undefined,
  },
  title: {
    color: "#FFFFFF",
    fontFamily: Platform.select({
      ios: "Iowan Old Style, Charter, Georgia, serif",
      android: "serif",
      default: "'Iowan Old Style', 'Charter', 'Georgia', serif",
    }) as any,
    fontSize: 44,
    fontWeight: "700",
    letterSpacing: -0.9,
    lineHeight: 50,
    // @ts-ignore
    textShadow: Platform.OS === "web" ? "0 2px 14px rgba(0,0,0,0.55)" : undefined,
  },
  subtitle: {
    color: "#FFFFFF",
    opacity: 0.88,
    fontSize: 15,
    lineHeight: 22,
    maxWidth: 640,
    // @ts-ignore
    textShadow: Platform.OS === "web" ? "0 1px 8px rgba(0,0,0,0.45)" : undefined,
  },
  right: { marginLeft: "auto" },
});

export default Hero;
