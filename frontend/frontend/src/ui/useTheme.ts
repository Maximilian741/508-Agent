import { useColorScheme } from "react-native";

import { Theme, darkColors, lightColors, radius, shadows, spacing, typography } from "./theme";

export function useTheme(): Theme {
  const scheme = useColorScheme();
  const colors = scheme === "dark" ? darkColors : lightColors;
  return {
    colors,
    spacing,
    radius,
    typography,
    shadows,
  };
}
