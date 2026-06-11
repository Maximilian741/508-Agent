import { Stack } from "expo-router";
import { View } from "react-native";

import { AppNav } from "../src/ui/components/AppNav";
import { ErrorBoundary } from "../src/ui/components/ErrorBoundary";
import { OnboardingTour } from "../src/ui/components/OnboardingTour";
import { SystemCheckWizard } from "../src/ui/components/SystemCheckWizard";
import { ShaderCanvas } from "../src/ui/components/ShaderCanvas";
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
 * A subtle Maple Mist shader sits behind everything as a fixed background
 * layer. Cards and surfaces are slightly translucent so the mist peeks
 * through, giving the app a soft sense of depth without distracting from
 * content.
 */
export default function RootLayout() {
  const theme = useTheme();
  return (
    <ErrorBoundary>
      <View style={{ flex: 1, backgroundColor: theme.colors.bg }}>
        <View
          style={
            {
              position: "fixed",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              zIndex: 0,
              pointerEvents: "none",
            } as any
          }
        >
          <ShaderCanvas variant="maple" opacity={0.45} />
        </View>
        <View style={{ flex: 1, zIndex: 1 }}>
          <SkipToContent />
          <AppNav />
          <Stack screenOptions={{ headerShown: false }} />
          <OnboardingTour />
          <SystemCheckWizard />
          <ToastHost />
        </View>
      </View>
    </ErrorBoundary>
  );
}
