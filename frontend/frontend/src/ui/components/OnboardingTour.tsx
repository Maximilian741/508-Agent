/**
 * HowItWorksWizard — the detailed first-run walkthrough + on-demand guide.
 *
 * Auto-opens once on first web visit (localStorage flag), and can be
 * re-opened from anywhere by calling `openHowItWorks()` (dispatches a window
 * event the mounted wizard listens for) — wired to "How it works" buttons on
 * the dashboard and audit screens, and to a replay control in Settings via
 * `clearTourCompleted()`.
 *
 * Portaled to <body> so the backdrop can never be trapped by a transformed
 * ancestor (the bug that collapsed fixed-position overlays to a thin strip).
 */
import React, { useEffect, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { useTheme } from "../useTheme";
import { Portal } from "./Portal";

const TOUR_COMPLETED_KEY = "508-tour-completed-v2";
const OPEN_EVENT = "508:open-how-it-works";

interface Step {
  badge: string;
  title: string;
  body: string;
  tip?: string;
}

const STEPS: Step[] = [
  {
    badge: "WELCOME",
    title: "What 508 Agent does",
    body:
      "You upload a document (PDF, Word, or PowerPoint). We check it against the accessibility rules a screen-reader user depends on (WCAG 2.1, Section 508, PDF/UA) and then write the approved fixes back into the file itself. You download a corrected copy. Analysis is always free; you only spend credits when you download a fixed file.",
    tip: "Nothing is changed without your approval, and your original file is never overwritten.",
  },
  {
    badge: "STEP 1 · UPLOAD",
    title: "Drop a document on the Audit screen",
    body:
      "Go to Audit and drag a file in (or click to browse). We parse it in a few seconds and give it a starting score out of 100. A low score is normal and expected. That's the work to be done, not a problem with your file.",
    tip: "First scan is on us. Batch lets you queue several files at once.",
  },
  {
    badge: "STEP 2 · REVIEW",
    title: "Walk the findings one at a time",
    body:
      "Each finding shows in plain English what's wrong, why it matters to a real user, the exact WCAG/508 citation, and what we propose to do. Use the keyboard: J / K to move, A to approve, R to reject, E to edit a suggestion. You're in control of every change.",
    tip: "Filter by severity (Errors / Warnings) or status (Pending / Approved) to focus.",
  },
  {
    badge: "STEP 3 · APPROVE",
    title: "Approve what you want fixed",
    body:
      "Some fixes are fully automatic (document title, language, list structure, alt text, table headers, untagged-PDF tagging). Others need your judgment (a contrast choice, a complex reading order) and are honestly marked for manual review instead of being silently 'fixed'. Approve the ones you want applied.",
    tip: "The score only ever counts fixes that genuinely end up in the downloaded file.",
  },
  {
    badge: "STEP 4 · DOWNLOAD",
    title: "Apply approved fixes & download",
    body:
      "Click 'Apply fixes – Download'. We confirm the credit cost, bake your approved changes into a fresh copy, and the corrected file downloads automatically. Credit costs: PDF 5, Word 3, PowerPoint 4, HTML 3.",
    tip: "If a credit-cost box appears, that's the confirm dialog: click the orange button to proceed.",
  },
  {
    badge: "STEP 5 · VERIFY",
    title: "Prove the fix worked",
    body:
      "After downloading, click 'Verify the fix' to re-audit the corrected file for free. You'll see the issue count drop and the score climb, the same before/after an auditor would check. Then issue a remediation certificate with a public verification link you can hand to anyone.",
    tip: "The certificate is generated from our server's own analysis. You can't fake the numbers.",
  },
  {
    badge: "YOU'RE SET",
    title: "Where everything lives",
    body:
      "Dashboard = your saved audits (search, re-open, or delete them there). Audit = run a new one. Batch = several at once. Contrast = a standalone colour checker. Help = FAQ and costs. Settings = themes, demo mode, and a button to replay this guide anytime.",
    tip: "Stuck on a specific finding? Every one has a 'Learn more' link to the official rule.",
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

/** Replay control (Settings calls this) — clears the seen-flag. */
export function clearTourCompleted(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(TOUR_COMPLETED_KEY);
  } catch {}
}

/** Open the guide on demand from any screen. */
export function openHowItWorks(): void {
  if (typeof window === "undefined") return;
  try {
    window.dispatchEvent(new Event(OPEN_EVENT));
  } catch {}
}

export function OnboardingTour() {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);

  // Auto-open once on first visit.
  useEffect(() => {
    if (Platform.OS !== "web") return;
    if (!isCompleted()) setOpen(true);
  }, []);

  // Listen for on-demand opens.
  useEffect(() => {
    if (Platform.OS !== "web") return;
    const handler = () => {
      setStep(0);
      setOpen(true);
    };
    window.addEventListener(OPEN_EVENT, handler);
    return () => window.removeEventListener(OPEN_EVENT, handler);
  }, []);

  // Keyboard: Esc closes, ← / → navigate.
  useEffect(() => {
    if (!open || Platform.OS !== "web") return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") finish();
      else if (e.key === "ArrowRight") setStep((s) => Math.min(STEPS.length - 1, s + 1));
      else if (e.key === "ArrowLeft") setStep((s) => Math.max(0, s - 1));
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open]);

  if (!open || Platform.OS !== "web") return null;

  const finish = () => {
    markCompleted();
    setOpen(false);
  };
  const advance = () => {
    if (step >= STEPS.length - 1) finish();
    else setStep((s) => s + 1);
  };
  const back = () => setStep((s) => Math.max(0, s - 1));
  const current = STEPS[step];
  const isLast = step >= STEPS.length - 1;

  return (
    <Portal>
      <Pressable
        onPress={finish}
        accessibilityLabel="How it works"
        // @ts-ignore web aria role
        accessibilityRole={"dialog" as any}
        style={[styles.dim, { backgroundColor: theme.colors.shadow }]}
      >
        <Pressable
          // Swallow backdrop taps inside the card.
          onPress={(e: any) => e?.stopPropagation && e.stopPropagation()}
          accessibilityLabel="Guide content"
          style={[styles.card, { borderRadius: theme.radius.md, backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}
        >
          <View style={styles.row}>
            <View style={[styles.badge, { borderRadius: theme.radius.pill, backgroundColor: theme.colors.surface2, borderColor: theme.colors.border }]}>
              <Text style={{ color: theme.colors.accent, fontSize: 10, fontWeight: "800", letterSpacing: 0.6 }}>
                {current.badge}
              </Text>
            </View>
            <Pressable accessibilityRole="button" onPress={finish} accessibilityLabel="Close guide">
              <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
                {isLast ? "Close" : "Skip"}
              </Text>
            </Pressable>
          </View>

          <Text
            accessibilityRole="header"
            style={[theme.typography.h1, { color: theme.colors.text, marginTop: 12, fontSize: 22 }]}
          >
            {current.title}
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 10, lineHeight: 21 }]}>
            {current.body}
          </Text>
          {current.tip ? (
            <View style={[styles.tip, { borderRadius: theme.radius.md, borderColor: theme.colors.accent, backgroundColor: theme.colors.surface2 }]}>
              <Text style={{ color: theme.colors.accent, fontSize: 12, fontWeight: "800" }}>TIP</Text>
              <Text style={[theme.typography.body, { color: theme.colors.text, flex: 1, fontSize: 13 }]}>
                {current.tip}
              </Text>
            </View>
          ) : null}

          {/* Progress dots */}
          <View style={styles.dots}>
            {STEPS.map((_, i) => (
              <View
                key={i}
                style={[
                  styles.dot,
                  {
                    backgroundColor: i === step ? theme.colors.accent : theme.colors.border,
                    width: i === step ? 22 : 8,
                  },
                ]}
              />
            ))}
          </View>

          <View style={styles.actions}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Previous step"
              onPress={back}
              disabled={step === 0}
              style={({ hovered }: any) => [
                styles.ghostBtn,
                { borderRadius: theme.radius.sm, borderColor: theme.colors.border, opacity: step === 0 ? 0.35 : 1 },
                hovered && step !== 0 ? { opacity: 0.8 } : null,
              ]}
            >
              <Text style={{ color: theme.colors.text, fontWeight: "700", fontSize: 13 }}>Back</Text>
            </Pressable>
            <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
              {step + 1} / {STEPS.length}
            </Text>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={isLast ? "Finish guide" : "Next step"}
              onPress={advance}
              style={({ hovered }: any) => [
                styles.primaryBtn,
                { borderRadius: theme.radius.sm, backgroundColor: theme.colors.accent },
                hovered ? { opacity: 0.92 } : null,
              ]}
            >
              <Text style={{ color: "#FFFFFF", fontWeight: "800", fontSize: 13 }}>
                {isLast ? "Got it" : "Next"}
              </Text>
            </Pressable>
          </View>
        </Pressable>
      </Pressable>
    </Portal>
  );
}

const styles = StyleSheet.create({
  dim: {
    position: (Platform.OS === "web" ? "fixed" : "absolute") as any,
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 10000,
    alignItems: "center",
    justifyContent: "center",
    padding: 16,
  },
  card: {
    width: "100%",
    maxWidth: 520,
    borderWidth: 1,
    padding: 22,
    // @ts-ignore web shadow
    boxShadow: "0 24px 60px rgba(0, 0, 0, 0.35)",
  },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  badge: { borderWidth: 1, paddingHorizontal: 10, paddingVertical: 4 },
  tip: {
    flexDirection: "row",
    gap: 8,
    alignItems: "flex-start",
    borderLeftWidth: 3,
    padding: 10,
    marginTop: 14,
  },
  dots: { flexDirection: "row", gap: 6, justifyContent: "center", marginTop: 18 },
  dot: { height: 8, borderRadius: 4 },
  actions: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    marginTop: 18,
  },
  ghostBtn: { borderWidth: 1, paddingVertical: 9, paddingHorizontal: 14 },
  primaryBtn: { paddingVertical: 10, paddingHorizontal: 18 },
});

export default OnboardingTour;
