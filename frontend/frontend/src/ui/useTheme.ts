import { useColorScheme } from "react-native";

import { Theme, darkColors, lightColors, radius, shadows, spacing, typography } from "./theme";
import { useAppStore } from "../store/useAppStore";

export function useTheme(): Theme {
  const scheme = useColorScheme();
  const themeMode = useAppStore((state) => state.themeMode);
  const resolved = themeMode === "system" ? (scheme === "dark" ? "dark" : "light") : themeMode;
  const colors = resolved === "dark" ? darkColors : lightColors;
  return {
    colors,
    spacing,
    radius,
    typography,
    shadows,
  };
}
