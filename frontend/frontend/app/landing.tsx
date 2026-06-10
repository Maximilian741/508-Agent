/**
 * Landing - public welcome screen.
 *
 * Lightweight rebuild after a tooling truncation. Hero with CTA into the
 * audit flow, three-step explainer, standards strip, footer.
 */
import { Linking, Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { Screen } from "../src/ui/components/Screen";
import { Hero } from "../src/ui/components/Hero";
import { useTheme } from "../src/ui/useTheme";

const STEPS = [
  {
    n: "1",
    title: "Drop a doc",
    body: "Upload a PDF, Word, or PowerPoint file. Analysis is free, hosted files are deleted after 24 hours, and we never train AI on your documents.",
  },
  {
    n: "2",
    title: "Review findings",
    body: "Walk every WCAG, Section 508, and PDF/UA finding one at a time, in plain language.",
  },
  {
    n: "3",
    title: "Approve and ship",
    body: "Approve the AI suggestions you trust, edit the rest, and download a remediated file.",
  },
];

const DIFFERENTIATORS = [
  {
    title: "Fixes, not just findings",
    body: "Most checkers stop at a list of problems. 508 Agent writes the approved fixes back into your file — alt text, titles, language, link text, table headers, and typed “- item” lines converted into real Word and PowerPoint lists.",
  },
  {
    title: "Rebuilds PDF structure",
    body: "Untagged PDF? The engine reconstructs a real PDF/UA structure tree from the page content — headings, lists, tables, figures, and header/footer artifacts.",
  },
  {
    title: "Catches scanned PDFs",
    body: "A scanned document looks fine and reads as nothing. We flag image-only PDFs as a hard error with OCR guidance instead of waving them through.",
  },
  {
    title: "Honest scoring & verifiable certificates",
    body: "The score only counts fixes that genuinely persist into the file, and every certificate has a public verification link auditors can check themselves.",
  },
];

export default function LandingScreen() {
  const theme = useTheme();
  const router = useRouter();
  return (
    <Screen scroll>
      <Hero
        shader="ember"
        eyebrow="508 AGENT"
        title="Accessibility audits without the busywork"
        subtitle="508 Agent walks every WCAG 2.1, Section 508, and PDF/UA finding in your documents and proposes a fix you can approve, edit, or reject."
      >
        <View style={styles.ctaRow}>
          <Button title="Try it free" onPress={() => router.push("/" as any)} />
          <Button
            title="Run your first audit"
            variant="ghost"
            onPress={() => router.push("/audit" as any)}
          />
        </View>
      </Hero>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Three steps.</Text>
        <View style={styles.steps}>
          {STEPS.map((s) => (
            <View
              key={s.n}
              style={[
                styles.step,
                { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 },
              ]}
            >
              <View style={[styles.stepDot, { backgroundColor: theme.colors.accent }]}>
                <Text style={styles.stepDotText}>{s.n}</Text>
              </View>
              <Text
                style={[theme.typography.h2, { color: theme.colors.text, fontSize: 16 }]}
              >
                {s.title}
              </Text>
              <Text
                style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 4 }]}
              >
                {s.body}
              </Text>
            </View>
          ))}
        </View>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>What makes this different</Text>
        <View style={styles.steps}>
          {DIFFERENTIATORS.map((d) => (
            <View
              key={d.title}
              style={[
                styles.step,
                { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 },
              ]}
            >
              <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 16 }]}>
                {d.title}
              </Text>
              <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 4 }]}>
                {d.body}
              </Text>
            </View>
          ))}
        </View>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Standards covered</Text>
        <View style={styles.chipRow}>
          <Pressable accessibilityRole="button" accessibilityLabel="Open the WCAG 2.1 specification (external link)" onPress={() => Linking.openURL("https://www.w3.org/TR/WCAG21/")}>
            <Chip label="WCAG 2.1" tone="info" />
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Open the Section 508 standards (external link)" onPress={() => Linking.openURL("https://www.access-board.gov/ict/")}>
            <Chip label="Section 508" tone="info" />
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Open the PDF accessibility techniques (external link)" onPress={() => Linking.openURL("https://www.w3.org/TR/WCAG21-TECHS/pdf.html")}>
            <Chip label="PDF/UA" tone="info" />
          </Pressable>
        </View>
      </Card>

      <View style={styles.footer}>
        <FooterLink label="Pricing" onPress={() => router.push("/billing" as any)} />
        <FooterLink label="About" onPress={() => router.push("/about" as any)} />
        <FooterLink label="Help" onPress={() => router.push("/help" as any)} />
        <FooterLink label="Terms" onPress={() => router.push("/terms" as any)} />
        <FooterLink label="Privacy" onPress={() => router.push("/privacy" as any)} />
      </View>
    </Screen>
  );
}

function FooterLink({ label, onPress }: { label: string; onPress: () => void }) {
  const theme = useTheme();
  return (
    <Pressable accessibilityRole="button"
      onPress={onPress}
      style={({ hovered, focused }: any) => [
        hovered ? { opacity: 0.7 } : null,
        focused ? ({ outlineColor: theme.colors.accent, outlineWidth: 2, outlineStyle: "solid", outlineOffset: 2 } as any) : null,
      ]}
      accessibilityLabel={label}
    >
      <Text style={{ color: theme.colors.accent, fontSize: 13, fontWeight: "600" }}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  ctaRow: { flexDirection: "row", gap: 12, marginTop: 16, flexWrap: "wrap" },
  steps: { flexDirection: "row", gap: 12, flexWrap: "wrap", marginTop: 12 },
  step: { flex: 1, minWidth: 220, borderWidth: 1, borderRadius: 12, padding: 14 },
  stepDot: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 8,
  },
  stepDotText: { color: "#FFFFFF", fontWeight: "800", fontSize: 14 },
  chipRow: { flexDirection: "row", gap: 8, flexWrap: "wrap", marginTop: 8 },
  footer: {
    flexDirection: "row",
    gap: 18,
    flexWrap: "wrap",
    paddingTop: 12,
    paddingBottom: 24,
    justifyContent: "center",
  },
});
