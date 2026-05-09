import { ReactNode } from "react";
import { StyleSheet, Text, View } from "react-native";
import { MaterialIcons } from "@expo/vector-icons";

import { useTheme } from "../useTheme";
import { Button } from "./Button";
import { PixelIcon, PixelGlyph } from "./PixelIcon";

interface EmptyStateProps {
  title: string;
  /** Pixel-art glyph for the canonical retro empty state. */
  icon?: PixelGlyph;
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
  /** Legacy: fall back to a MaterialIcons glyph if no PixelGlyph supplied. */
  materialIcon?: keyof typeof MaterialIcons.glyphMap;
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
      <View style={[styles.iconWrap, { borderColor: accent }]}>
        {icon ? (
          <PixelIcon name={icon} size={5} color={accent} />
        ) : materialIcon ? (
          <MaterialIcons name={materialIcon} size={28} color={accent} />
        ) : (
          <PixelIcon name="spark" size={5} color={accent} />
        )}
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
      padding: theme.spacing.xl,
      backgroundColor: theme.colors.surface,
      borderRadius: theme.radius.lg,
      borderWidth: 1,
      borderColor: theme.colors.border,
    },
    iconWrap: {
      width: 72,
      height: 72,
      borderRadius: 12,
      borderWidth: 2,
      alignItems: "center",
      justifyContent: "center",
      backgroundColor: theme.colors.bg,
    },
    title: {
      ...theme.typography.h2,
      ...theme.typography.pixel,
      color: theme.colors.text,
      textAlign: "center",
    },
    message: {
      ...theme.typography.body,
      color: theme.colors.textMuted,
      textAlign: "center",
      maxWidth: 360,
    },
    actionWrap: {
      marginTop: theme.spacing.xs,
    },
  });
