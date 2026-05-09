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

const FOCUS_STYLE_ID = "508-focus-style";

function _injectFocusStyles() {
  if (Platform.OS !== "web") return;
  if (typeof document === "undefined") return;
  if (document.getElementById(FOCUS_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = FOCUS_STYLE_ID;
  style.innerHTML = `
    :focus-visible { outline: 2px solid #2D5BFF !important; outline-offset: 2px !important; border-radius: 6px; }
    :focus:not(:focus-visible) { outline: none !important; }
    [role="button"]:focus-visible, button:focus-visible { outline: 2px solid #2D5BFF !important; outline-offset: 2px !important; }
  `;
  document.head.appendChild(style);
}

export function Screen({ children, scroll = false, contentStyle, title }: ScreenProps) {
  const theme = useTheme();
  const styles = createStyles(theme);

  useEffect(() => {
    _injectFocusStyles();
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
      backgroundColor: theme.colors.bg,
    },
    content: {
      flex: 1,
      paddingHorizontal: theme.spacing.xl,
      paddingVertical: theme.spacing.lg,
      gap: theme.spacing.md,
    },
    scrollContent: {
      paddingHorizontal: theme.spacing.xl,
      paddingVertical: theme.spacing.lg,
      gap: theme.spacing.md,
    },
  });
