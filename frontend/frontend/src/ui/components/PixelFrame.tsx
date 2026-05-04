/**
 * PixelFrame - 8-bit corner-bracket overlay.
 *
 * Drops chunky corner brackets at each of the 4 corners of its parent.
 * Use as the FIRST child inside a `position: relative` container. The
 * brackets are absolute-positioned and pointer-events: none so they never
 * block UI underneath. Looks like a HUD targeting reticle from arcade
 * games.
 */
import React from "react";
import { View } from "react-native";

export interface PixelFrameProps {
  size?: number; // length of each bracket leg in px
  thickness?: number; // bracket leg thickness in px
  color?: string;
  inset?: number;
}

export function PixelFrame({
  size = 14,
  thickness = 3,
  color = "#F59E4A",
  inset = 6,
}: PixelFrameProps) {
  const corner = (top: boolean, left: boolean) => {
    return (
      <View
        pointerEvents="none"
        style={{
          position: "absolute",
          top: top ? inset : undefined,
          bottom: !top ? inset : undefined,
          left: left ? inset : undefined,
          right: !left ? inset : undefined,
          width: size,
          height: size,
        }}
      >
        {/* Horizontal leg */}
        <View
          style={{
            position: "absolute",
            top: top ? 0 : size - thickness,
            left: 0,
            width: size,
            height: thickness,
            backgroundColor: color,
          }}
        />
        {/* Vertical leg */}
        <View
          style={{
            position: "absolute",
            top: 0,
            left: left ? 0 : size - thickness,
            width: thickness,
            height: size,
            backgroundColor: color,
          }}
        />
      </View>
    );
  };

  return (
    <View
      pointerEvents="none"
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        zIndex: 3,
      }}
    >
      {corner(true, true)}
      {corner(true, false)}
      {corner(false, true)}
      {corner(false, false)}
    </View>
  );
}

export default PixelFrame;
