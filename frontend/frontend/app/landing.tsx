/**
 * Landing - public welcome screen.
 *
 * Lightweight rebuild after a tooling truncation. Hero with CTA into the
 * audit flow, three-step explainer, standards strip, footer.
 */
import { Linking, Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { CATALOG_ENTRIES } from "../src/domain/issueCatalog";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { Screen } from "../src/ui/components/Screen";
import { Hero } from "../src/ui/components/Hero";
import { ScreenReaderPreview } from "../src/ui/components/ScreenReaderPreview";
import { useTheme } from "../src/ui/useTheme";

/** Real number of checks, derived from the catalog so it can never drift. */
const ISSUE_COUNT = CATALOG_ENTRIES.length;

const STEPS = [
  {
    n: "1",
    title: "Drop a doc",
    body: "Upload a PDF, Word, PowerPoint, or HTML file. Analysis is free, hosted files are deleted after 24 hours, and we never train AI on your documents.",
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
    title: "One click, including the alt text",
    body: "Upload, click “Fix everything,” download an accessible file. We write the alt text for your images with AI and repair headings, titles, lists, tables, language and link text. You don't write or decide anything.",
  },
  {
    title: "Procurement-grade conformance reports",
    body: "One click produces a VPAT-style Accessibility Conformance Report (a verdict on every WCAG 2.1 A/AA criterion) that you can hand straight to legal or procurement.",
  },
  {
    title: "Built for agencies & teams",
    body: "Process a whole folder at once, download every fixed file as a ZIP, and generate one consolidated conformance report, all under your own logo and brand colours.",
  },
  {
    title: "Rebuilds PDF structure",
    body: "Untagged PDF? The engine reconstructs a real PDF/UA structure tree from the page content: headings, lists, tables, figures, and header/footer artifacts.",
  },
  {
    title: "Verifiable certificates & a developer API",
    body: "Every remediation summary has a public verification link auditors can check. Need automation? Scan documents programmatically from your CI pipeline with an API key.",
  },
  {
    title: "Honest by design",
    body: "The score only counts fixes that genuinely persist into the file, and the report marks criteria we don't auto-check as “needs manual review.” No overclaiming. The kind of report an auditor trusts.",
  },
];

const AUDIENCES = [
  { title: "Government & public sector", body: "Meet ADA Title II / Section 508 across your document libraries before the deadline." },
  { title: "Agencies & consultants", body: "Remediate client documents in bulk and deliver branded, verifiable reports." },
  { title: "Enterprise & regulated industries", body: "Keep finance, HR and customer PDFs compliant and audit-ready." },
  { title: "Universities & education", body: "Make course materials, syllabi and handouts accessible at scale." },
];

export default function LandingScreen() {
  const theme = useTheme();
  const router = useRouter();
  return (
    <Screen scroll>
      <Hero
        eyebrow="508 AGENT"
        title="Make every document accessible, and prove it."
        subtitle="508 Agent finds every WCAG 2.1, Section 508 & PDF/UA issue in your PDF, Word, PowerPoint, and HTML files, fixes them automatically (including writing the alt text for your images), and hands you a conformance report. Your first audits are free."
      >
        <View style={styles.ctaRow}>
          <Button title="Start free (25 credits)" onPress={() => router.push("/audit" as any)} />
          <Button
            title="See pricing"
            variant="ghost"
            onPress={() => router.push("/pricing" as any)}
          />
        </View>
      </Hero>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <Chip label="ADA Title II" tone="warning" />
          <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 18 }]}>
            The compliance deadline is already here
          </Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8 }]}>
          Under the DOJ's ADA Title II rule, state &amp; local government web content and
          documents must meet WCAG 2.1 Level AA by{" "}
          <Text style={{ color: theme.colors.text, fontWeight: "700" }}>April 24, 2026</Text>{" "}
          for larger entities (populations of 50,000+) and{" "}
          <Text style={{ color: theme.colors.text, fontWeight: "700" }}>April 26, 2027</Text>{" "}
          for smaller entities and special districts. Most organizations have thousands of
          untagged PDFs and documents to remediate, and at manual rates of $5–25 per page, that's
          a wall. 508 Agent fixes them in bulk, automatically.
        </Text>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Estimate how much you would save versus manual remediation"
          onPress={() => router.push("/savings" as any)}
          style={({ hovered }: any) => [{ marginTop: 12, alignSelf: "flex-start" }, hovered ? { opacity: 0.7 } : null]}
        >
          <Text style={{ color: theme.colors.accent, fontWeight: "700", fontSize: 14 }}>
            Calculate your savings vs. manual remediation
          </Text>
        </Pressable>
      </Card>

      {/* The single most persuasive thing we can show: the SAME content as a
          sighted reader sees it and as a screen reader announces it. The damage
          is invisible to whoever published the file — this makes it visible. */}
      <Card variant="data">
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
          Your document looks fine. Here's what it sounds like.
        </Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8, marginBottom: 18 }]}>
          Accessibility problems are invisible to the person who published the file — the page
          looks perfect. These are five of the {ISSUE_COUNT} issues 508 Agent checks for, shown as
          a sighted reader sees them and as assistive technology actually reads them out.
        </Text>
        <ScreenReaderPreview />
        <View style={{ marginTop: 20 }}>
          <Button title="Scan my document free" onPress={() => router.push("/audit" as any)} />
        </View>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Three steps.</Text>
        <View style={styles.steps}>
          {STEPS.map((s) => (
            <View
              key={s.n}
              style={[
                styles.step,
                { borderRadius: theme.radius.md, borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 },
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
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Everything you need to ship accessible documents</Text>
        <View style={styles.steps}>
          {DIFFERENTIATORS.map((d) => (
            <View
              key={d.title}
              style={[
                styles.step,
                { borderRadius: theme.radius.md, borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 },
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
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Who it's for</Text>
        <View style={styles.steps}>
          {AUDIENCES.map((a) => (
            <View
              key={a.title}
              style={[styles.step, { borderRadius: theme.radius.md, borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 }]}
            >
              <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 16 }]}>{a.title}</Text>
              <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 4 }]}>{a.body}</Text>
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

      <Card>
        <View style={{ alignItems: "center", paddingVertical: 8 }}>
          <Text style={[theme.typography.h2, { color: theme.colors.text, textAlign: "center" }]}>
            Start free. Your first audits are on us
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 6, textAlign: "center", maxWidth: 520 }]}>
            25 free credits, no credit card. Pay-as-you-go credit packs or a monthly plan when
            you're ready to scale. Fix one document or ten thousand.
          </Text>
          <View style={[styles.ctaRow, { justifyContent: "center" }]}>
            <Button title="Start free" onPress={() => router.push("/audit" as any)} />
            <Button title="See pricing" variant="ghost" onPress={() => router.push("/pricing" as any)} />
          </View>
        </View>
      </Card>

      <View style={styles.footer}>
        <FooterLink label="Pricing" onPress={() => router.push("/pricing" as any)} />
        <FooterLink label="Savings calculator" onPress={() => router.push("/savings" as any)} />
        <FooterLink label="FAQ" onPress={() => router.push("/faq" as any)} />
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
  step: { flex: 1, minWidth: 220, borderWidth: 1, padding: 14 },
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
