/**
 * SketchHighlight - inline span that lays a highlighter color block
 * behind a chunk of text. Mimics the wf-hl class from the wireframe css.
 *
 * Looks like a marker swiped under handwritten text. Default tone is
 * yellow.
 */
import { ReactNode } from "react";
import { StyleSheet, Text, TextStyle, View, ViewStyle, StyleProp } from "react-native";

import { highlighterColor, HighlighterTone, sketchPalette, sketchFontFamily } from "./fonts";

interface SketchHighlightProps {
  children: ReactNode;
  tone?: HighlighterTone;
  /** Render as inline-block sized to text. */
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
}

export function SketchHighlight({
  children,
  tone = "yellow",
  style,
  textStyle,
}: SketchHighlightProps) {
  const color = highlighterColor(tone);
  return (
    <View style={[styles.wrap, { backgroundColor: color }, style]}>
      <Text style={[styles.text, textStyle]}>{children}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    alignSelf: "flex-start",
    paddingHorizontal: 4,
    paddingVertical: 1,
    borderRadius: 2,
  },
  text: {
    color: sketchPalette.ink,
    fontFamily: sketchFontFamily.body,
    fontSize: 14,
    lineHeight: 19,
  },
});
