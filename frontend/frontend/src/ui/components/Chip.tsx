import { ReactNode } from "react";
import { Pressable, StyleProp, StyleSheet, Text, TextStyle, View, ViewStyle } from "react-native";

import { useTheme } from "../useTheme";

interface ChipProps {
  label: string;
  tone?: "default" | "success" | "warning" | "danger" | "info";
  style?: StyleProp<ViewStyle>;
  icon?: ReactNode;
  textStyle?: StyleProp<TextStyle>;
  /** When set, the chip becomes a Pressable button. */
  onPress?: () => void;
  accessibilityLabel?: string;
  /** Genuine pill shape (rare exception). Default false → right-angled tag. */
  rounded?: boolean;
}

export function Chip({
  label,
  tone = "default",
  style,
  icon,
  textStyle,
  onPress,
  accessibilityLabel,
  rounded = false,
}: ChipProps) {
  const theme = useTheme();
  const styles = createStyles(theme);
  const shape = rounded ? { borderRadius: theme.radius.pill } : null;
  const content = (
    <>
      {icon}
      <Text style={[styles.text, styles[`text_${tone}`], textStyle]}>{label}</Text>
    </>
  );
  if (onPress) {
    return (
      <Pressable
        onPress={onPress}
        accessibilityRole="button"
        accessibilityLabel={accessibilityLabel ?? label}
        style={({ pressed }: any) => [
          styles.base,
          styles[`tone_${tone}`],
          shape,
          pressed ? { opacity: 0.85 } : null,
          style,
        ]}
      >
        {content}
      </Pressable>
    );
  }
  return <View style={[styles.base, styles[`tone_${tone}`], shape, style]}>{content}</View>;
}

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    base: {
      flexDirection: "row",
      alignItems: "center",
      gap: theme.spacing.xs,
      paddingHorizontal: theme.spacing.sm,
      paddingVertical: theme.spacing.xs,
      borderRadius: theme.radius.none,
      borderWidth: theme.border.thin,
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
      backgroundColor: theme.colors.successSoft,
      borderColor: theme.colors.success,
    },
    tone_warning: {
      backgroundColor: theme.colors.warningSoft,
      borderColor: theme.colors.warning,
    },
    tone_danger: {
      backgroundColor: theme.colors.dangerSoft,
      borderColor: theme.colors.danger,
    },
    tone_info: {
      backgroundColor: theme.colors.infoSoft,
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
