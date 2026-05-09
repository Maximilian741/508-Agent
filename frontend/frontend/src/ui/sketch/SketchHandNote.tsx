/**
 * SketchHandNote - orange margin annotation in the wireframe accent color.
 *
 * Looks like a designer scribbled a quick aside next to a UI element:
 * Caveat-style script, a tiny tilt, a leading arrow glyph. The whole
 * note can be hidden globally via SketchHandNoteContext - useful for
 * a "hide annotations" toggle.
 *
 * The accent color (#c96442) is the only place the warm clay/orange is
 * supposed to show up in the sketch system, per spec.
 */
import { createContext, ReactNode, useContext } from "react";
import { StyleSheet, Text, TextStyle, View, ViewStyle, StyleProp } from "react-native";

import { sketchPalette, sketchFontFamily } from "./fonts";

interface HandNoteCtx {
  hidden: boolean;
}

const SketchHandNoteContext = createContext<HandNoteCtx>({ hidden: false });

export function SketchHandNoteProvider({
  hidden = false,
  children,
}: {
  hidden?: boolean;
  children: ReactNode;
}) {
  return (
    <SketchHandNoteContext.Provider value={{ hidden }}>
      {children}
    </SketchHandNoteContext.Provider>
  );
}

interface SketchHandNoteProps {
  children: ReactNode;
  /** Show a leading arrow before the text. */
  arrow?: "left" | "right" | "down" | "none";
  tilt?: -0.4 | 0 | 0.4;
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
}

export function SketchHandNote({
  children,
  arrow = "none",
  tilt = -0.4,
  style,
  textStyle,
}: SketchHandNoteProps) {
  const ctx = useContext(SketchHandNoteContext);
  if (ctx.hidden) return null;

  const arrowGlyph =
    arrow === "left"
      ? "<- "
      : arrow === "right"
      ? "-> "
      : arrow === "down"
      ? "v "
      : "";

  const transform = tilt !== 0 ? [{ rotate: tilt + "deg" }] : undefined;

  return (
    <View style={[styles.wrap, { transform }, style]}>
      <Text style={[styles.text, textStyle]}>
        {arrowGlyph}
        {children}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    paddingVertical: 2,
  },
  text: {
    color: sketchPalette.accent,
    fontFamily: sketchFontFamily.display,
    fontSize: 16,
    fontWeight: "600",
    lineHeight: 20,
  },
});
