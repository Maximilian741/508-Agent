import { Stack } from "expo-router";
import { View } from "react-native";

import { AppNav } from "../src/ui/components/AppNav";
import { ErrorBoundary } from "../src/ui/components/ErrorBoundary";
import { ToastHost } from "../src/ui/toast";
import { useTheme } from "../src/ui/useTheme";

/**
 * Root navigation stack.
 *
 * AppNav is the sticky top bar across every screen — gives the app a real
 * "this is one product, not seven loose pages" feel.  We hide the default
 * Expo Router header on every screen to avoid double headers.
 *
 * <ErrorBoundary /> wraps everything so a crash in any screen produces a
 * recoverable UI instead of a white page.
 *
 * <ToastHost /> is mounted last so notifications float above content.
 */
export default function RootLayout() {
  const theme = useTheme();
  return (
    <ErrorBoundary>
      <View style={{ flex: 1, backgroundColor: theme.colors.bg }}>
        <AppNav />
        <Stack screenOptions={{ headerShown: false }} />
        <ToastHost />
      </View>
    </ErrorBoundary>
  );
}
