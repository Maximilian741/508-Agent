import { StyleSheet, View } from "react-native";

import { useTheme } from "../useTheme";

export function Divider() {
  const theme = useTheme();
  const styles = createStyles(theme);
  return <View style={styles.line} />;
}

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    line: {
      height: 1,
      backgroundColor: theme.colors.border,
      width: "100%",
    },
  });
