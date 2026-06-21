import { Theme, border, darkColors, focus, lightColors, radius, shadows, spacing, twilightColors, typography } from "./theme";
import { useAppStore } from "../store/useAppStore";

/**
 * Resolve the active theme. The three modes intentionally produce three
 * visually distinct palettes:
 *   - "system"  -> twilight (dusky violet, warm peach accents)
 *   - "light"   -> cream parchment
 *   - "dark"    -> deep walnut/umber
 *
 * "system" no longer mirrors the OS - users picked it expecting a different
 * look, not a clone of one of the other two. If a user wants their OS
 * preference reflected, they should pick Light or Dark explicitly.
 */
export function useTheme(): Theme {
  const themeMode = useAppStore((state) => state.themeMode);
  const colors =
    themeMode === "dark"
      ? darkColors
      : themeMode === "light"
        ? lightColors
        : twilightColors;
  return {
    colors,
    spacing,
    radius,
    border,
    focus,
    typography,
    shadows,
  };
}
