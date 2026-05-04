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
import { ShaderCanvas } from "../src/ui/components/ShaderCanvas";
import { useTheme } from "../src/ui/useTheme";

const STEPS = [
  {
    n: "1",
    title: "Drop a doc",
    body: "Upload a PDF, Word, or PowerPoint file. Nothing leaves your machine in self-hosted mode.",
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

export default function LandingScreen() {
  const theme = useTheme();
  const router = useRouter();
  return (
    <Screen scroll>
      <View style={styles.heroWrap}>
        <ShaderCanvas variant="ember" opacity={0.18} />
        <View style={[styles.hero, { borderColor: theme.colors.border }]}>
          <Text style={[styles.eyebrow, { color: theme.colors.accent }]}>508 AGENT</Text>
          <Text
            accessibilityRole="header"
            style={[theme.typography.title, { color: theme.colors.text }]}
          >
            Accessibility audits without the busywork.
          </Text>
          <Text
            style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8 }]}
          >
            508 Agent walks every WCAG 2.1, Section 508, and PDF/UA finding in
            your documents and proposes a fix you can approve, edit, or reject.
          </Text>
          <View style={styles.ctaRow}>
            <Button title="Try it free" onPress={() => router.push("/" as any)} />
            <Button
              title="Run your first audit"
              variant="ghost"
              onPress={() => router.push("/audit" as any)}
            />
          </View>
        </View>
      </View>

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
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Standards covered</Text>
        <View style={styles.chipRow}>
          <Pressable onPress={() => Linking.openURL("https://www.w3.org/TR/WCAG21/")}>
            <Chip label="WCAG 2.1" tone="info" />
          </Pressable>
          <Pressable onPress={() => Linking.openURL("https://www.access-board.gov/ict/")}>
            <Chip label="Section 508" tone="info" />
          </Pressable>
          <Pressable onPress={() => Linking.openURL("https://www.w3.org/TR/WCAG21-TECHS/pdf.html")}>
            <Chip label="PDF/UA" tone="info" />
          </Pressable>
        </View>
      </Card>

      <View style={styles.footer}>
        <FooterLink label="About" onPress={() => router.push("/about" as any)} />
        <FooterLink label="Help" onPress={() => router.push("/help" as any)} />
        <FooterLink label="Settings" onPress={() => router.push("/settings" as any)} />
      </View>
    </Screen>
  );
}

function FooterLink({ label, onPress }: { label: string; onPress: () => void }) {
  const theme = useTheme();
  return (
    <Pressable
      onPress={onPress}
      style={({ hovered }: any) => [hovered ? { opacity: 0.7 } : null]}
      accessibilityLabel={label}
    >
      <Text style={{ color: theme.colors.accent, fontSize: 13, fontWeight: "600" }}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  heroWrap: { position: "relative", borderRadius: 16, overflow: "hidden" },
  hero: { borderRadius: 16, borderWidth: 1, padding: 24 },
  eyebrow: { fontSize: 11, fontWeight: "800", letterSpacing: 1.5, marginBottom: 6 },
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
