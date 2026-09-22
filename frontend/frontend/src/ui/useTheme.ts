import { Theme, auroraColors, border, darkColors, focus, lightColors, radius, shadows, spacing, typography } from "./theme";
import { useAppStore } from "../store/useAppStore";

/**
 * Resolve the active theme. The three modes produce three distinct palettes:
 *   - "system"  -> Aurora (the default: deep ink with a cool bloom)
 *   - "light"   -> Light (paper-white, deep teal accent)
 *   - "dark"    -> Graphite (neutral near-black, maximum contrast)
 *
 * "system" deliberately does not mirror the OS: the product is dark-first,
 * and users who want light pick it explicitly.
 */
export function useTheme(): Theme {
  const themeMode = useAppStore((state) => state.themeMode);
  const colors =
    themeMode === "dark"
      ? darkColors
      : themeMode === "light"
        ? lightColors
        : auroraColors;
  return {
    colors,
    isDark: themeMode !== "light",
    spacing,
    radius,
    border,
    focus,
    typography,
    shadows,
  };
}
