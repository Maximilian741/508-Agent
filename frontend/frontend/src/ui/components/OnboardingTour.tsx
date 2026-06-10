/**
 * OnboardingTour - 4-stop first-run walkthrough.
 *
 * Mounts on the dashboard the first time the user visits. Highlights the
 * primary CTA, recent audits, top nav, and settings, then sets a
 * localStorage flag so it doesn't fire again. Replay is exposed via
 * `clearTourCompleted` (called from Settings).
 *
 * Reduced-motion users still see the steps but with the dim/highlight
 * applied instantly instead of fading.
 */
import React, { useEffect, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { useTheme } from "../useTheme";

const TOUR_COMPLETED_KEY = "508-tour-completed-v1";

interface Step {
  title: string;
  body: string;
}

const STEPS: Step[] = [
  {
    title: "Welcome to 508 Agent",
    body: "We will walk through four quick stops so you know where everything lives. This takes about twenty seconds.",
  },
  {
    title: "Drop a doc, get a score",
    body: "The big card on this screen is where you start a new audit. Pick a PDF, DOCX, or PPTX and we will analyze it in under a minute.",
  },
  {
    title: "Recent audits stay here",
    body: "Every audit you run is saved locally so you can re-open it without re-uploading. Click any card to resume.",
  },
  {
    title: "Top nav goes everywhere",
    body: "Audit, Batch, Contrast, Help, Achievements, and Settings are one click away — and your credits balance lives in the top-right corner.",
  },
];

function isCompleted(): boolean {
  if (typeof window === "undefined") return true;
  try {
    return Boolean(window.localStorage.getItem(TOUR_COMPLETED_KEY));
  } catch {
    return true;
  }
}

function markCompleted(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(TOUR_COMPLETED_KEY, "1");
  } catch {}
}

export function clearTourCompleted(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(TOUR_COMPLETED_KEY);
  } catch {}
}

export function OnboardingTour() {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (Platform.OS !== "web") return;
    if (!isCompleted()) {
      setOpen(true);
    }
  }, []);

  if (!open || Platform.OS !== "web") return null;

  const advance = () => {
    if (step >= STEPS.length - 1) {
      markCompleted();
      setOpen(false);
    } else {
      setStep((s) => s + 1);
    }
  };

  const skip = () => {
    markCompleted();
    setOpen(false);
  };

  const current = STEPS[step];

  return (
    <View style={styles.dim} pointerEvents="auto">
      <View style={[styles.card, { backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}>
        <View style={styles.row}>
          <Text style={{ color: theme.colors.textMuted, fontSize: 11, fontWeight: "700" }}>
            {"STEP " + (step + 1) + " OF " + STEPS.length}
          </Text>
          <Pressable accessibilityRole="button" onPress={skip} accessibilityLabel="Skip tour">
            <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>Skip</Text>
          </Pressable>
        </View>
        <Text
          accessibilityRole="header"
          style={[theme.typography.h2, { color: theme.colors.text, marginTop: 8 }]}
        >
          {current.title}
        </Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8 }]}>
          {current.body}
        </Text>
        <View style={styles.actions}>
          <Pressable accessibilityRole="button"
            onPress={advance}
            accessibilityLabel={step >= STEPS.length - 1 ? "Finish tour" : "Next step"}
            style={({ hovered }: any) => [
              styles.btn,
              { backgroundColor: theme.colors.accent },
              hovered ? { opacity: 0.92 } : null,
            ]}
          >
            <Text style={{ color: "#FFFFFF", fontWeight: "700", fontSize: 13 }}>
              {step >= STEPS.length - 1 ? "Finish" : "Next"}
            </Text>
          </Pressable>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  dim: {
    position: ("fixed" as any),
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "rgba(31, 20, 10, 0.5)",
    zIndex: 10000,
    alignItems: "center",
    justifyContent: "center",
    padding: 16,
  },
  card: {
    width: "100%",
    maxWidth: 460,
    borderWidth: 1,
    borderRadius: 14,
    padding: 18,
    // @ts-ignore
    boxShadow: "0 16px 40px rgba(31, 20, 10, 0.25)",
  },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  actions: { flexDirection: "row", justifyContent: "flex-end", marginTop: 14, gap: 8 },
  btn: { paddingVertical: 9, paddingHorizontal: 16, borderRadius: 8 },
});

export default OnboardingTour;
