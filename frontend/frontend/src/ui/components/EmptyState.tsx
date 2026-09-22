import { ReactNode } from "react";
import { StyleSheet, Text, View } from "react-native";

import { alpha, glassStyle } from "../theme";
import { useTheme } from "../useTheme";
import { Button } from "./Button";
import { Icon, IconName } from "./Icon";

interface EmptyStateProps {
  title: string;
  /** Line icon (any Feather name or an Icon alias such as "doc"). */
  icon?: IconName;
  /** Body copy (preferred). */
  body?: string;
  /** Legacy alias for `body`. */
  message?: string;
  /** Custom node rendered below the body (preferred). */
  action?: ReactNode;
  /** Legacy: render a button with this label. */
  actionLabel?: string;
  /** Legacy: callback for the legacy button. */
  onAction?: () => void;
  /** Legacy name for `icon` (older call sites passed MaterialIcons names). */
  materialIcon?: IconName;
  tone?: "default" | "warning" | "danger" | "info";
}

export function EmptyState({
  title,
  icon,
  body,
  message,
  action,
  actionLabel,
  onAction,
  materialIcon,
  tone = "default",
}: EmptyStateProps) {
  const theme = useTheme();
  const styles = createStyles(theme);
  const accent = toneColor(theme, tone);
  const copy = body ?? message;

  return (
    <View style={styles.container}>
      <View style={[styles.iconWrap, { borderColor: alpha(accent, 0.35), backgroundColor: alpha(accent, 0.1) }]}>
        <Icon name={icon ?? materialIcon ?? "inbox"} size={26} color={accent} />
      </View>
      <Text style={styles.title}>{title}</Text>
      {copy ? <Text style={styles.message}>{copy}</Text> : null}
      {action ? (
        <View style={styles.actionWrap}>{action}</View>
      ) : actionLabel && onAction ? (
        <Button title={actionLabel} onPress={onAction} variant="secondary" />
      ) : null}
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
      justifyContent: "center",
      gap: theme.spacing.md,
      paddingHorizontal: theme.spacing.xxl,
      paddingVertical: theme.spacing.xxxl,
      ...(glassStyle(theme.colors) as any),
      borderRadius: theme.radius.lg,
      borderWidth: 1,
      borderColor: theme.colors.glassBorder,
    },
    iconWrap: {
      width: 56,
      height: 56,
      borderRadius: theme.radius.lg,
      borderWidth: 1,
      alignItems: "center",
      justifyContent: "center",
    },
    title: {
      ...theme.typography.h2,
      color: theme.colors.text,
      textAlign: "center",
    },
    message: {
      ...theme.typography.body,
      color: theme.colors.textMuted,
      textAlign: "center",
      maxWidth: 380,
    },
    actionWrap: {
      marginTop: theme.spacing.xs,
    },
  });
