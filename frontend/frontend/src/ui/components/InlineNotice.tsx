import { StyleSheet, Text, View } from "react-native";
import { MaterialIcons } from "@expo/vector-icons";

import { useTheme } from "../useTheme";

interface InlineNoticeProps {
  title: string;
  message: string;
  tone?: "info" | "success" | "warning" | "danger";
}

export function InlineNotice({ title, message, tone = "info" }: InlineNoticeProps) {
  const theme = useTheme();
  const styles = createStyles(theme);
  const color = toneColor(theme, tone);

  return (
    <View style={[styles.container, { borderColor: color, backgroundColor: `${color}1A` }]}>
      <MaterialIcons name={iconName(tone)} size={18} color={color} />
      <View style={styles.textWrap}>
        <Text style={[styles.title, { color }]}>{title}</Text>
        <Text style={styles.message}>{message}</Text>
      </View>
    </View>
  );
}

const toneColor = (theme: ReturnType<typeof useTheme>, tone: InlineNoticeProps["tone"]) => {
  if (tone === "success") return theme.colors.success;
  if (tone === "warning") return theme.colors.warning;
  if (tone === "danger") return theme.colors.danger;
  return theme.colors.info;
};

const iconName = (tone: InlineNoticeProps["tone"]) => {
  if (tone === "success") return "check-circle";
  if (tone === "warning") return "warning";
  if (tone === "danger") return "error";
  return "info";
};

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    container: {
      flexDirection: "row",
      alignItems: "flex-start",
      gap: theme.spacing.sm,
      padding: theme.spacing.md,
      borderRadius: theme.radius.md,
      borderWidth: 1,
    },
    textWrap: {
      flex: 1,
      gap: theme.spacing.xs,
    },
    title: {
      ...theme.typography.body,
      fontWeight: "600",
    },
    message: {
      ...theme.typography.body,
      color: theme.colors.textMuted,
    },
  });
