import { Stack } from "expo-router";
import { View } from "react-native";

import { AppNav } from "../src/ui/components/AppNav";
import { Seo } from "../src/ui/components/Seo";
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
      {/* Site-wide default title/description. Helmet-managed, so any route
          that renders its own <Seo> (landing, audit, pricing, …) REPLACES
          these in the static export instead of duplicating them. +html.tsx
          must not set these tags — a raw tag there does not dedupe. */}
      <Seo
        title="508 Agent — Automated document accessibility & remediation"
        description="Automatically find and fix WCAG 2.1, Section 508 & PDF/UA accessibility issues in PDF, Word, PowerPoint, and HTML files — including AI-written alt text — and get a conformance report. First audits free."
      />
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
