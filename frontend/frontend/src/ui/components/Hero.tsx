/**
 * Hero — the top-of-page panel on every screen.
 *
 * A rounded glass panel with a large, tight sans headline. Pass `shader` to
 * put the reactive ShaderBackground behind it (landing + home use it); pass
 * `shaderIntensity` (0..1) to make the field brighten while something is
 * happening — e.g. while a document is being processed.
 *
 * The shader always renders under its palette scrim, and the contrast script
 * proves every text token (and the accent eyebrow pill) passes AA over ANY
 * pixel the shader can emit, so text colours here are the normal palette
 * tokens. Keep a shader hero to text, the eyebrow and buttons: tinted status
 * chips are only certified on surfaces, so they belong below the hero.
 */
import React, { ReactNode } from "react";
import { Platform, StyleSheet, Text, View } from "react-native";

import { glassStyle } from "../theme";
import { useTheme } from "../useTheme";
import { ShaderBackground } from "./ShaderBackground";

export interface HeroProps {
  eyebrow?: string;
  title: string;
  subtitle?: string;
  rightSlot?: ReactNode;
  children?: ReactNode;
  /**
   * Render the reactive shader behind the hero. Reserved for the two front
   * doors (landing + home) so it stays special; every other page gets glass.
   */
  shader?: boolean;
  /** 0..1 — shader liveliness; raise it while work is in progress. */
  shaderIntensity?: number;
  /** Accepted for back-compat; ignored. */
  shaderOpacity?: number;
}

export function Hero({ eyebrow, title, subtitle, rightSlot, children, shader, shaderIntensity = 0 }: HeroProps) {
  const theme = useTheme();
  const withShader = Platform.OS === "web" && !!shader;
  const shadow = theme.isDark ? theme.shadows.near.webDark : theme.shadows.near.web;

  return (
    <View
      // @ts-ignore - region landmark
      accessibilityRole={Platform.OS === "web" ? ("region" as any) : undefined}
      accessibilityLabel={Platform.OS === "web" ? title : undefined}
      style={[
        styles.wrap,
        withShader ? styles.wrapShader : null,
        Platform.OS === "web"
          ? ({ paddingHorizontal: withShader ? "clamp(22px, 4vw, 40px)" : "clamp(20px, 3vw, 28px)" } as any)
          : null,
        {
          borderRadius: theme.radius.xl,
          borderColor: theme.colors.glassBorder,
          ...(withShader ? { backgroundColor: theme.colors.shaderBase } : glassStyle(theme.colors)),
          ...(Platform.OS === "web" ? ({ boxShadow: shadow } as any) : theme.shadows.near.rn),
        } as any,
      ]}
    >
      {withShader ? <ShaderBackground intensity={shaderIntensity} /> : null}
      {!withShader && Platform.OS === "web" ? (
        // A faint top sheen so the glass reads as a lit surface.
        <View
          pointerEvents="none"
          style={[
            StyleSheet.absoluteFill,
            {
              // @ts-ignore web-only
              backgroundImage: `linear-gradient(180deg, ${theme.isDark ? "rgba(255,255,255,0.035)" : "rgba(255,255,255,0.7)"} 0%, transparent 60%)`,
            } as any,
          ]}
        />
      ) : null}
      <View style={[styles.content, { gap: 10 }]}>
        {eyebrow ? (
          <View
            style={[
              styles.eyebrow,
              {
                borderRadius: theme.radius.pill,
                borderColor: theme.colors.glassBorder,
                backgroundColor: theme.colors.accentSoft,
              },
            ]}
          >
            <View style={[styles.eyebrowDot, { backgroundColor: theme.colors.accent }]} />
            <Text style={[theme.typography.eyebrow, { color: theme.colors.accent }]}>{eyebrow}</Text>
          </View>
        ) : null}
        <Text
          style={[
            withShader ? theme.typography.display : [theme.typography.title, styles.title],
            // Scale the headline with the viewport on web (CSS, not a JS
            // width measurement, which reads 0 in some embedded views).
            Platform.OS === "web"
              ? ({ fontSize: withShader ? "clamp(34px, 5.4vw, 48px)" : "clamp(26px, 4vw, 34px)", lineHeight: "1.14" } as any)
              : null,
            { color: theme.colors.text },
          ]}
          // Page title = the h1 of every screen. We sell heading structure;
          // the app must have it too.
          accessibilityRole="header"
          {...(Platform.OS === "web" ? ({ "aria-level": 1 } as any) : {})}
        >
          {title}
        </Text>
        {subtitle ? (
          <Text style={[styles.subtitle, withShader ? styles.subtitleLarge : null, { color: theme.colors.textMuted }]}>
            {subtitle}
          </Text>
        ) : null}
        {children}
      </View>
      {rightSlot ? <View style={styles.right}>{rightSlot}</View> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    position: "relative",
    overflow: "hidden",
    flexDirection: "row",
    alignItems: "center",
    gap: 16,
    paddingHorizontal: 28,
    paddingVertical: 28,
    borderWidth: 1,
    flexWrap: "wrap",
    minHeight: 140,
  },
  wrapShader: {
    paddingHorizontal: 36,
    paddingVertical: 48,
    minHeight: 280,
  },
  content: { flex: 1, minWidth: 260, zIndex: 1 },
  eyebrow: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderWidth: 1,
  },
  eyebrowDot: { width: 6, height: 6, borderRadius: 3 },
  title: {
    fontSize: 34,
    fontWeight: "700",
    letterSpacing: -0.9,
    lineHeight: 40,
  },
  subtitle: {
    fontSize: 15,
    lineHeight: 23,
    maxWidth: 680,
  },
  subtitleLarge: {
    fontSize: 17,
    lineHeight: 26,
  },
  right: { marginLeft: "auto", zIndex: 1 },
});

export default Hero;
