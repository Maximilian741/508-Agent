import { ReactNode } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, ViewStyle } from "react-native";

import { useTheme } from "../useTheme";

type Variant = "primary" | "secondary" | "ghost" | "danger";

interface ButtonProps {
  title: string;
  onPress: () => void;
  variant?: Variant;
  loading?: boolean;
  disabled?: boolean;
  style?: ViewStyle;
  icon?: ReactNode;
  /** Override the screen-reader label. Defaults to the visible title. */
  accessibilityLabel?: string;
  /** Optional one-line hint announced after the label. */
  accessibilityHint?: string;
}

export function Button({
  title,
  onPress,
  variant = "primary",
  loading = false,
  disabled = false,
  style,
  icon,
  accessibilityLabel,
  accessibilityHint,
}: ButtonProps) {
  const theme = useTheme();
  const styles = createStyles(theme);
  const isDisabled = disabled || loading;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? title}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: isDisabled, busy: loading }}
      onPress={onPress}
      disabled={isDisabled}
      style={({ pressed, focused }: any) => [
        styles.base,
        styles[variant],
        pressed && !isDisabled ? styles.pressed : null,
        focused ? styles.focused : null,
        isDisabled ? styles.disabled : null,
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={styles.text.color} />
      ) : (
        <>
          {icon}
          <Text style={[styles.text, styles[`text_${variant}`]]}>{title}</Text>
        </>
      )}
    </Pressable>
  );
}

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    base: {
      flexDirection: "row",
      alignItems: "center",
      justifyContent: "center",
      gap: theme.spacing.sm,
      paddingVertical: theme.spacing.sm,
      paddingHorizontal: theme.spacing.lg,
      borderRadius: theme.radius.md,
    },
    primary: {
      backgroundColor: theme.colors.accent,
    },
    secondary: {
      backgroundColor: theme.colors.surface2,
      borderWidth: 1,
      borderColor: theme.colors.border,
    },
    ghost: {
      backgroundColor: "transparent",
      borderWidth: 1,
      borderColor: theme.colors.border,
    },
    danger: {
      backgroundColor: theme.colors.danger,
    },
    text: {
      ...theme.typography.body,
      color: theme.colors.text,
    },
    text_primary: {
      color: "#FFFFFF",
      fontWeight: "600",
    },
    text_secondary: {
      color: theme.colors.text,
      fontWeight: "600",
    },
    text_ghost: {
      color: theme.colors.text,
      fontWeight: "600",
    },
    text_danger: {
      color: "#FFFFFF",
      fontWeight: "600",
    },
    pressed: {
      opacity: 0.85,
      transform: [{ scale: 0.98 }],
    },
    focused: {
      outlineColor: theme.colors.accent,
      outlineWidth: 2,
      outlineStyle: "solid",
      outlineOffset: 2,
    } as any,
    disabled: {
      opacity: 0.5,
    },
  });
