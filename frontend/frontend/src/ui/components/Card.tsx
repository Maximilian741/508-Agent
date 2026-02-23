import { ReactNode } from "react";
import { StyleProp, StyleSheet, View, ViewStyle } from "react-native";

import { useTheme } from "../useTheme";

interface CardProps {
  children: ReactNode;
  style?: StyleProp<ViewStyle>;
}

export function Card({ children, style }: CardProps) {
  const theme = useTheme();
  const styles = createStyles(theme);
  return <View style={[styles.card, style]}>{children}</View>;
}

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    card: {
      backgroundColor: theme.colors.surface,
      borderRadius: theme.radius.lg,
      borderWidth: 1,
      borderColor: theme.colors.border,
      padding: theme.spacing.lg,
      shadowColor: theme.colors.shadow,
      shadowOffset: theme.shadows.subtle.shadowOffset,
      shadowOpacity: theme.shadows.subtle.shadowOpacity,
      shadowRadius: theme.shadows.subtle.shadowRadius,
      elevation: theme.shadows.subtle.elevation,
    },
  });
