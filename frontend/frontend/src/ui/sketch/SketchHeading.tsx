/**
 * SketchHeading - Caveat-styled large heading.
 *
 * Three sizes (display / title / subtitle) all using the cursive Caveat
 * font with a slight downward letter-spacing. Optional `eyebrow` prop
 * renders a small Architects Daughter uppercase label above the title.
 */
import { ReactNode } from "react";
import { StyleSheet, Text, TextStyle, View, ViewStyle, StyleProp } from "react-native";

import { sketchPalette, sketchFontFamily } from "./fonts";

export type SketchHeadingSize = "display" | "title" | "subtitle";

interface SketchHeadingProps {
  children: ReactNode;
  size?: SketchHeadingSize;
  eyebrow?: string;
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
}

const SIZES: Record<SketchHeadingSize, { fontSize: number; lineHeight: number }> = {
  display: { fontSize: 44, lineHeight: 48 },
  title: { fontSize: 30, lineHeight: 34 },
  subtitle: { fontSize: 22, lineHeight: 26 },
};

export function SketchHeading({
  children,
  size = "title",
  eyebrow,
  style,
  textStyle,
}: SketchHeadingProps) {
  const dim = SIZES[size];
  return (
    <View style={style}>
      {eyebrow ? <Text style={styles.eyebrow}>{eyebrow}</Text> : null}
      <Text
        style={[
          styles.heading,
          { fontSize: dim.fontSize, lineHeight: dim.lineHeight },
          textStyle,
        ]}
        accessibilityRole="header"
      >
        {children}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  eyebrow: {
    fontFamily: sketchFontFamily.label,
    fontSize: 11,
    color: sketchPalette.pencil,
    textTransform: "uppercase",
    letterSpacing: 0.04 * 11,
    marginBottom: 4,
  },
  heading: {
    fontFamily: sketchFontFamily.display,
    color: sketchPalette.ink,
    fontWeight: "600",
  },
});
