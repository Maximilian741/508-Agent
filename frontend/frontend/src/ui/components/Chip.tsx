import { ReactNode } from "react";
import { StyleSheet, Text, View, ViewStyle } from "react-native";

import { useTheme } from "../useTheme";

interface ChipProps {
  label: string;
  tone?: "default" | "success" | "warning" | "danger" | "info";
  style?: ViewStyle;
  icon?: ReactNode;
}

export function Chip({ label, tone = "default", style, icon }: ChipProps) {
  const theme = useTheme();
  const styles = createStyles(theme);
  return (
    <View style={[styles.base, styles[`tone_${tone}`], style]}>
      {icon}
      <Text style={[styles.text, styles[`text_${tone}`]]}>{label}</Text>
    </View>
  );
}

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    base: {
      flexDirection: "row",
      alignItems: "center",
      gap: theme.spacing.xs,
      paddingHorizontal: theme.spacing.sm,
      paddingVertical: theme.spacing.xs,
      borderRadius: theme.radius.sm,
      borderWidth: 1,
    },
    text: {
      fontSize: 12,
      fontWeight: "600",
      color: theme.colors.text,
    },
    tone_default: {
      backgroundColor: theme.colors.surface2,
      borderColor: theme.colors.border,
    },
    tone_success: {
      backgroundColor: "rgba(34, 197, 94, 0.16)",
      borderColor: theme.colors.success,
    },
    tone_warning: {
      backgroundColor: "rgba(245, 158, 11, 0.16)",
      borderColor: theme.colors.warning,
    },
    tone_danger: {
      backgroundColor: "rgba(220, 38, 38, 0.16)",
      borderColor: theme.colors.danger,
    },
    tone_info: {
      backgroundColor: "rgba(14, 165, 233, 0.16)",
      borderColor: theme.colors.info,
    },
    text_default: {
      color: theme.colors.text,
    },
    text_success: {
      color: theme.colors.success,
    },
    text_warning: {
      color: theme.colors.warning,
    },
    text_danger: {
      color: theme.colors.danger,
    },
    text_info: {
      color: theme.colors.info,
    },
  });
