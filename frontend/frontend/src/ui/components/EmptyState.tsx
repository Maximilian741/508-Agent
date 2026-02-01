import { StyleSheet, Text, View } from "react-native";
import { MaterialIcons } from "@expo/vector-icons";

import { useTheme } from "../useTheme";
import { Button } from "./Button";

interface EmptyStateProps {
  title: string;
  message: string;
  icon?: keyof typeof MaterialIcons.glyphMap;
  actionLabel?: string;
  onAction?: () => void;
  tone?: "default" | "warning" | "danger" | "info";
}

export function EmptyState({
  title,
  message,
  icon = "info-outline",
  actionLabel,
  onAction,
  tone = "default",
}: EmptyStateProps) {
  const theme = useTheme();
  const styles = createStyles(theme);
  const accent = toneColor(theme, tone);

  return (
    <View style={styles.container}>
      <View style={[styles.iconWrap, { borderColor: accent }]}>
        <MaterialIcons name={icon} size={24} color={accent} />
      </View>
      <Text style={styles.title}>{title}</Text>
      <Text style={styles.message}>{message}</Text>
      {actionLabel && onAction && (
        <Button title={actionLabel} onPress={onAction} variant="secondary" />
      )}
    </View>
  );
}

const toneColor = (theme: ReturnType<typeof useTheme>, tone: EmptyStateProps["tone"]) => {
  if (tone === "warning") return theme.colors.warning;
  if (tone === "danger") return theme.colors.danger;
  if (tone === "info") return theme.colors.info;
  return theme.colors.accent;
};

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    container: {
      alignItems: "center",
      gap: theme.spacing.sm,
      padding: theme.spacing.lg,
      backgroundColor: theme.colors.surface,
      borderRadius: theme.radius.lg,
      borderWidth: 1,
      borderColor: theme.colors.border,
    },
    iconWrap: {
      width: 44,
      height: 44,
      borderRadius: 22,
      borderWidth: 1,
      alignItems: "center",
      justifyContent: "center",
    },
    title: {
      ...theme.typography.h2,
      color: theme.colors.text,
    },
    message: {
      ...theme.typography.body,
      color: theme.colors.textMuted,
      textAlign: "center",
    },
  });
