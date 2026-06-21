import { Stack } from "expo-router";
import { View } from "react-native";

import { AppNav } from "../src/ui/components/AppNav";
import { ErrorBoundary } from "../src/ui/components/ErrorBoundary";
import { OnboardingTour } from "../src/ui/components/OnboardingTour";
import { SystemCheckWizard } from "../src/ui/components/SystemCheckWizard";
import { SkipToContent } from "../src/ui/components/SkipToContent";
import { ToastHost } from "../src/ui/toast";
import { useTheme } from "../src/ui/useTheme";

/**
 * Root navigation stack.
 *
 * AppNav is the sticky top bar across every screen. ErrorBoundary catches
 * crashes. ToastHost renders notifications. OnboardingTour shows the
 * first-run walkthrough.
 *
 * The page sits on a flat warm background — no animated shader, no translucent
 * surfaces. Depth comes from solid panels, hairline borders, and a single
 * downward shadow, which reads as a typeset document rather than a generated
 * demo.
 */
export default function RootLayout() {
  const theme = useTheme();
  return (
    <ErrorBoundary>
      <View style={{ flex: 1, backgroundColor: theme.colors.bg }}>
        <SkipToContent />
        <AppNav />
        <Stack screenOptions={{ headerShown: false }} />
        <OnboardingTour />
        <SystemCheckWizard />
        <ToastHost />
      </View>
    </ErrorBoundary>
  );
}
