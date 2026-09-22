import { useEffect } from "react";
import { Stack } from "expo-router";
import { DefaultTheme, ThemeProvider } from "@react-navigation/native";
import { Platform, View } from "react-native";

import { AppNav } from "../src/ui/components/AppNav";
import { Seo } from "../src/ui/components/Seo";
import { ErrorBoundary } from "../src/ui/components/ErrorBoundary";
import { registerIconFontForStaticRender } from "../src/ui/components/Icon";
import { OnboardingTour } from "../src/ui/components/OnboardingTour";
import { SystemCheckWizard } from "../src/ui/components/SystemCheckWizard";
import { SkipToContent } from "../src/ui/components/SkipToContent";
import { ToastHost } from "../src/ui/toast";
import { useTheme } from "../src/ui/useTheme";

/**
 * Root navigation stack.
 *
 * AppNav is the top bar across every screen. ErrorBoundary catches crashes.
 * ToastHost renders notifications. OnboardingTour is the "How it works" guide
 * — mounted so any screen (Help, Dashboard, Audit) can open it, but it no
 * longer opens itself on a first visit.
 *
 * The page sits on the palette's gradient backdrop (web): deep ink with a
 * faint cool bloom. Surfaces above it are translucent glass.
 */
// Screens must be see-through so the root gradient shows behind every page;
// react-navigation otherwise paints each screen container its default grey.
const NAV_THEME = { ...DefaultTheme, colors: { ...DefaultTheme.colors, background: "transparent" } };

export default function RootLayout() {
  const theme = useTheme();
  // Static export only: put the icon font's @font-face + preload in the HTML.
  registerIconFontForStaticRender();

  useEffect(() => {
    if (Platform.OS !== "web" || typeof document === "undefined") return;
    const root = document.documentElement;
    // The focus ring (base CSS in app/+html.tsx) follows the live accent.
    root.style.setProperty("--ui-focus", theme.colors.accent);
    // Native form controls and scrollbars match the palette.
    root.style.colorScheme = theme.isDark ? "dark" : "light";
    document.body.style.backgroundColor = theme.colors.bg;
    document.body.style.color = theme.colors.text;
  }, [theme.colors.accent, theme.colors.bg, theme.colors.text, theme.isDark]);

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
      <View
        style={[
          { flex: 1, backgroundColor: theme.colors.bg },
          Platform.OS === "web" ? ({ backgroundImage: theme.colors.bgGradient } as any) : null,
        ]}
      >
        <SkipToContent />
        <AppNav />
        <ThemeProvider value={NAV_THEME}>
          <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: "transparent" } }} />
        </ThemeProvider>
        <OnboardingTour />
        <SystemCheckWizard />
        <ToastHost />
      </View>
    </ErrorBoundary>
  );
}
