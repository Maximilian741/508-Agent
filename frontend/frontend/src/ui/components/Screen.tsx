import { ReactNode, useEffect } from "react";
import { Platform, SafeAreaView, ScrollView, StyleSheet, View, ViewStyle } from "react-native";

import { useTheme } from "../useTheme";

interface ScreenProps {
  children: ReactNode;
  scroll?: boolean;
  contentStyle?: ViewStyle;
  /** Sets the document title in the browser tab. Web only. */
  title?: string;
}

/** Page content column. Wide enough for side-by-side panels, narrow enough to read. */
export const CONTENT_MAX_WIDTH = 1180;

export function Screen({ children, scroll = false, contentStyle, title }: ScreenProps) {
  const theme = useTheme();
  const styles = createStyles(theme);

  useEffect(() => {
    if (Platform.OS === "web" && title && typeof document !== "undefined") {
      document.title = `${title} · 508 Agent`;
    }
  }, [title]);

  if (scroll) {
    return (
      <SafeAreaView style={styles.safe}>
        <ScrollView
          // @ts-ignore — RN-Web maps nativeID to DOM id
          nativeID="content"
          contentContainerStyle={[styles.scrollContent, contentStyle]}
          // @ts-ignore — RN-Web supports a11y landmark
          accessibilityRole={Platform.OS === "web" ? ("main" as any) : undefined}
        >
          {children}
        </ScrollView>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.safe}>
      <View
        // @ts-ignore — RN-Web maps nativeID to DOM id
        nativeID="content"
        style={[styles.content, contentStyle]}
        // @ts-ignore
        accessibilityRole={Platform.OS === "web" ? ("main" as any) : undefined}
      >
        {children}
      </View>
    </SafeAreaView>
  );
}

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    safe: {
      flex: 1,
      // On web the root layout paints the page gradient; stay transparent so
      // it shows through. Native has no gradient, so paint the solid bg.
      backgroundColor: Platform.OS === "web" ? "transparent" : theme.colors.bg,
    },
    content: {
      flex: 1,
      width: "100%",
      maxWidth: CONTENT_MAX_WIDTH,
      alignSelf: "center",
      paddingHorizontal: theme.spacing.xl,
      paddingVertical: theme.spacing.xxl,
      gap: theme.spacing.xl,
    },
    scrollContent: {
      width: "100%",
      maxWidth: CONTENT_MAX_WIDTH,
      alignSelf: "center",
      paddingHorizontal: theme.spacing.xl,
      paddingTop: theme.spacing.xxl,
      paddingBottom: theme.spacing.huge,
      gap: theme.spacing.xl,
    },
  });
