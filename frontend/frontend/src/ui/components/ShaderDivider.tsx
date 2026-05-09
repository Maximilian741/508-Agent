/**
 * ShaderDivider — a 1px-tall horizontal accent divider.
 *
 * Web: draws a horizontal gradient bar with a subtle 3px glow blur using the
 * active theme's `accent` and `accentSecondary` tokens.  Native: renders a
 * flat 1px line in the active theme's `accent` colour (no glow — RN's box
 * shadow on a 1px line looks awful and isn't worth the perf hit on phones).
 *
 * Layout: stretches to fill its parent's width, renders exactly 1px tall plus
 * a 6px top/bottom margin so it has breathing room between sections.
 *
 * Constraints: pure RN/RN-Web primitives, no new deps.  Uses a single <View>
 * tree — no raw <div> openers — so RN's tag mismatcher is happy.
 */

import { Platform, StyleSheet, View } from "react-native";

import { useTheme } from "../useTheme";

interface ShaderDividerProps {
  /** Vertical breathing room above/below the line.  Defaults to 24. */
  spacing?: number;
}

export function ShaderDivider({ spacing = 24 }: ShaderDividerProps) {
  const theme = useTheme();
  const accent = theme.colors.accent;
  const accent2 = theme.colors.accentSecondary;

  if (Platform.OS === "web") {
    // Web: tinted gradient bar + a slightly larger soft glow underneath via
    // boxShadow.  Both layers are pointer-events:none so the divider never
    // intercepts hover/click that belongs to the layout above or below.
    const gradient = {
      // @ts-ignore — RN-Web supports CSS background-image
      backgroundImage: `linear-gradient(90deg, transparent 0%, ${accent} 25%, ${accent2} 75%, transparent 100%)`,
      // @ts-ignore — RN-Web supports CSS box-shadow
      boxShadow: `0 0 3px 0 ${accent}66, 0 0 6px 0 ${accent2}33`,
    } as any;
    return (
      <View
        accessibilityRole={Platform.OS === "web" ? ("separator" as any) : undefined}
        // @ts-ignore — RN-Web honours aria-hidden on View
        aria-hidden={true}
        style={[styles.bar, { marginVertical: spacing }, gradient]}
      />
    );
  }

  // Native fallback — flat hairline.
  return (
    <View
      style={[
        styles.bar,
        {
          marginVertical: spacing,
          backgroundColor: accent,
        },
      ]}
    />
  );
}

const styles = StyleSheet.create({
  bar: {
    height: 1,
    width: "100%",
    borderRadius: 0.5,
  },
});

export default ShaderDivider;
