/**
 * PixelLogo - chunky 8-bit-style brand mark.
 *
 * Renders "508" as a 3x5 pixel grid per glyph using small SVG rects on web
 * and View blocks on native. Each "pixel" can be a fixed size; the whole
 * logo scales with the size prop. Uses the accent color for the lit pixels
 * and a darker tint for the unlit grid behind them so the mark feels like a
 * tiny LCD readout, similar to the Claude Code boot mark.
 */
import React from "react";
import { Platform, View } from "react-native";

export interface PixelLogoProps {
  size?: number; // pixel size of each block in CSS px
  color?: string; // lit pixel color
  dimColor?: string; // unlit grid color
  showGrid?: boolean; // show the unlit pixels faintly
}

// 3 columns wide, 5 rows tall. 1 = lit, 0 = unlit.
// "5" "0" "8" digits in a chunky 5x3 font.
const FIVE = [
  [1, 1, 1],
  [1, 0, 0],
  [1, 1, 1],
  [0, 0, 1],
  [1, 1, 1],
];
const ZERO = [
  [1, 1, 1],
  [1, 0, 1],
  [1, 0, 1],
  [1, 0, 1],
  [1, 1, 1],
];
const EIGHT = [
  [1, 1, 1],
  [1, 0, 1],
  [1, 1, 1],
  [1, 0, 1],
  [1, 1, 1],
];

const GLYPH_GAP_COLS = 1; // 1 blank column between glyphs

function buildBitmap(): number[][] {
  const rows = 5;
  const out: number[][] = [];
  for (let r = 0; r < rows; r++) {
    const row: number[] = [];
    [FIVE, ZERO, EIGHT].forEach((g, gi) => {
      row.push(...g[r]);
      if (gi < 2) for (let i = 0; i < GLYPH_GAP_COLS; i++) row.push(0);
    });
    out.push(row);
  }
  return out;
}

const BITMAP = buildBitmap();
const COLS = BITMAP[0].length; // 3+1+3+1+3 = 11
const ROWS = BITMAP.length;    // 5

export function PixelLogo({
  size = 3,
  color = "#F59E4A",
  dimColor = "rgba(255, 255, 255, 0.06)",
  showGrid = true,
}: PixelLogoProps) {
  const width = COLS * size;
  const height = ROWS * size;

  if (Platform.OS === "web") {
    // SVG looks crispest. No anti-aliasing on the rects so each "pixel" is
    // a clean square.
    return (
      <View style={{ width, height }}>
        {/* @ts-ignore - svg renders fine on RN-Web */}
        <svg
          width={width}
          height={height}
          viewBox={"0 0 " + COLS + " " + ROWS}
          shapeRendering="crispEdges"
          xmlns="http://www.w3.org/2000/svg"
        >
          {showGrid
            ? BITMAP.flatMap((row, r) =>
                row.map((cell, c) =>
                  cell === 0 ? (
                    <rect
                      key={"d-" + r + "-" + c}
                      x={c}
                      y={r}
                      width={1}
                      height={1}
                      fill={dimColor}
                    />
                  ) : null,
                ),
              )
            : null}
          {BITMAP.flatMap((row, r) =>
            row.map((cell, c) =>
              cell === 1 ? (
                <rect
                  key={"l-" + r + "-" + c}
                  x={c}
                  y={r}
                  width={1}
                  height={1}
                  fill={color}
                />
              ) : null,
            ),
          )}
        </svg>
      </View>
    );
  }

  // Native fallback: render a flex-row of tiny Views.
  return (
    <View style={{ width, height }}>
      {BITMAP.map((row, r) => (
        <View key={"r-" + r} style={{ flexDirection: "row", height: size }}>
          {row.map((cell, c) => (
            <View
              key={"c-" + c}
              style={{
                width: size,
                height: size,
                backgroundColor: cell === 1 ? color : showGrid ? dimColor : "transparent",
              }}
            />
          ))}
        </View>
      ))}
    </View>
  );
}

export default PixelLogo;
