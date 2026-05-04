/**
 * Card — themed surface with a fade-up reveal when first mounted.
 *
 * The reveal is subtle (~250ms, 8px translate) so it feels alive without
 * being distracting on a screen full of cards.  Disable with animate={false}
 * for cards that toggle visibility (e.g. inside an overlay) since you don't
 * want them re-animating every render.
 */

import { ReactNode, useEffect, useRef } from "react";
import { Animated, Platform, StyleProp, StyleSheet, ViewStyle } from "react-native";

import { useTheme } from "../useTheme";

interface CardProps {
  children: ReactNode;
  style?: StyleProp<ViewStyle>;
  animate?: boolean;
}

export function Card({ children, style, animate = true }: CardProps) {
  const theme = useTheme();
  const styles = createStyles(theme);
  const opacity = useRef(new Animated.Value(animate ? 0 : 1)).current;
  const translateY = useRef(new Animated.Value(animate ? 8 : 0)).current;

  useEffect(() => {
    if (!animate) return;
    Animated.parallel([
      Animated.timing(opacity, { toValue: 1, duration: 260, useNativeDriver: true }),
      Animated.timing(translateY, { toValue: 0, duration: 260, useNativeDriver: true }),
    ]).start();
  }, [animate, opacity, translateY]);

  return (
    <Animated.View style={[styles.card, { opacity, transform: [{ translateY }] }, style]}>
      {children}
    </Animated.View>
  );
}

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    card: {
      backgroundColor:
        Platform.OS === "web" ? theme.colors.surface + "EE" : theme.colors.surface,
      borderRadius: theme.radius.lg,
      borderWidth: 1,
      borderColor: theme.colors.border,
      paddingHorizontal: 20,
      paddingVertical: 18,
      // Hairline border carries the structure; shadow is ink-soft on dark only.
      ...(Platform.OS === "web"
        ? ({
            boxShadow:
              theme.colors.bg === "#150E08"
                ? "inset 0 1px 0 rgba(245, 239, 227, 0.04)"
                : "none",
          } as any)
        : {}),
    },
  });
