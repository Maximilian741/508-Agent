import { ReactNode } from "react";
import { Pressable, StyleProp, StyleSheet, Text, TextStyle, View, ViewStyle } from "react-native";

import { alpha } from "../theme";
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
  /** Accepted for back-compat: every chip is a pill now. */
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
}: ChipProps) {
  const theme = useTheme();
  const styles = createStyles(theme);
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
          pressed ? { opacity: 0.85 } : null,
          style,
        ]}
      >
        {content}
      </Pressable>
    );
  }
  return <View style={[styles.base, styles[`tone_${tone}`], style]}>{content}</View>;
}

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    base: {
      flexDirection: "row",
      alignItems: "center",
      gap: 6,
      paddingHorizontal: 10,
      paddingVertical: 4,
      borderRadius: theme.radius.pill,
      borderWidth: theme.border.thin,
    },
    text: {
      fontSize: 12,
      fontWeight: "600",
      lineHeight: 16,
      color: theme.colors.text,
    },
    tone_default: {
      backgroundColor: theme.colors.surface2,
      borderColor: theme.colors.glassBorder,
    },
    tone_success: {
      backgroundColor: theme.colors.successSoft,
      borderColor: alpha(theme.colors.success, 0.4),
    },
    tone_warning: {
      backgroundColor: theme.colors.warningSoft,
      borderColor: alpha(theme.colors.warning, 0.4),
    },
    tone_danger: {
      backgroundColor: theme.colors.dangerSoft,
      borderColor: alpha(theme.colors.danger, 0.4),
    },
    tone_info: {
      backgroundColor: theme.colors.infoSoft,
      borderColor: alpha(theme.colors.info, 0.4),
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
