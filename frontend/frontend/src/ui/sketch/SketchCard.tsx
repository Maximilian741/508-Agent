/**
 * SketchCard - paper card with wobble border and hard-offset shadow.
 *
 * Designed to look like a card cut out of a designer notebook: slightly
 * asymmetric border-radius (the "wobble"), a thick ink border, and a
 * non-blurred 3px x 4px shadow that lands like a felt-tip mark. Optional
 * tilt of -0.4 or +0.4 degrees adds a hand-placed feel.
 *
 * Use `density="cozy"` for hero/spotlight cards and `"dense"` for tight
 * lists.
 */
import { ReactNode, useEffect } from "react";
import { Platform, StyleSheet, View, ViewStyle, StyleProp } from "react-native";

import { sketchPalette, ensureSketchFontsInjected } from "./fonts";

export type SketchDensity = "cozy" | "dense";

interface SketchCardProps {
  children: ReactNode;
  style?: StyleProp<ViewStyle>;
  /** -0.4, 0, or +0.4 degrees. Default 0. */
  tilt?: -0.4 | 0 | 0.4;
  /** When true, uses a dashed border (drop-zone variant). */
  dashed?: boolean;
  /** Hides the hard-offset shadow. */
  flat?: boolean;
  density?: SketchDensity;
  /** Background color override. Defaults to paper. */
  background?: string;
}

export function SketchCard({
  children,
  style,
  tilt = 0,
  dashed = false,
  flat = false,
  density = "cozy",
  background,
}: SketchCardProps) {
  useEffect(() => {
    ensureSketchFontsInjected();
  }, []);

  const padding = density === "cozy" ? 18 : 12;
  const transform = tilt !== 0 ? [{ rotate: tilt + "deg" }] : undefined;

  return (
    <View
      style={[
        styles.card,
        {
          backgroundColor: background ?? sketchPalette.paper,
          padding,
          transform,
          borderStyle: dashed ? "dashed" : "solid",
        },
        Platform.OS === "web" && !flat
          ? ({
              boxShadow: "3px 4px 0 " + sketchPalette.ink,
            } as any)
          : null,
        style,
      ]}
    >
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderWidth: 2,
    borderColor: sketchPalette.ink,
    // Wobble: asymmetric corners. RN does not support the slash form,
    // so we approximate with four distinct corners.
    borderTopLeftRadius: 6,
    borderTopRightRadius: 8,
    borderBottomRightRadius: 5,
    borderBottomLeftRadius: 7,
  },
});
