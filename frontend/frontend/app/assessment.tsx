/**
 * Professional Assessment offering page.
 *
 * The self-serve product automates remediation; this page sells the
 * human-in-the-loop service the go-to-market analysis flagged as the
 * higher-margin, higher-willingness-to-pay motion (procurement / legal /
 * regulated buyers who need a defensible, expert-reviewed result and someone
 * accountable). It is intentionally HONEST: it exists precisely because pure
 * automation does not fully achieve conformance on complex documents — the
 * page says so rather than overclaiming.
 *
 * No money path here: the CTA opens a prefilled email to the sales address.
 * Charging happens off-platform (a scoped quote), so nothing depends on live
 * Stripe wiring.
 */
import { Linking, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { Hero } from "../src/ui/components/Hero";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

const SALES_EMAIL = "assessments@508-agent.app";
const PRICE_FROM = "$1,250";

const INCLUDED = [
  {
    title: "Expert manual review",
    body: "A qualified evaluator checks the things automation can't fully judge: reading order, complex/merged tables, meaningful (not just present) alt text, and form and link semantics in context.",
  },
  {
    title: "Full remediation",
    body: "We run your document set through the engine, then hand-correct everything that needs a human, so the delivered files genuinely meet WCAG 2.1 AA, Section 508 and PDF/UA.",
  },
  {
    title: "A defensible conformance report",
    body: "You receive a per-criterion VPAT/ACR reviewed and signed off by a person — the artifact procurement and legal ask for, not just an automated summary.",
  },
  {
    title: "A named contact, accountable",
    body: "One point of contact owns your engagement end to end, with a turnaround you can plan a deadline around.",
  },
];

const FOR_WHOM = [
  { title: "Government & procurement", body: "ADA Title II / Section 508 document libraries that need an auditable, defensible result." },
  { title: "Legal & regulated", body: "Documents where 'improved' isn't enough and you need someone to stand behind the conformance claim." },
  { title: "High-stakes sets", body: "Complex reports, forms and tables where automated-only remediation leaves real items for manual review." },
];

export default function AssessmentScreen() {
  const theme = useTheme();
  const router = useRouter();

  const onRequest = () => {
    const subject = encodeURIComponent("Professional assessment request");
    const body = encodeURIComponent(
      [
        "Hi — I'd like a quote for a professional accessibility assessment + remediation.",
        "",
        "Organization:",
        "Number of documents:",
        "Formats (PDF / Word / PowerPoint / HTML):",
        "Standards required (WCAG 2.1 AA / Section 508 / PDF/UA):",
        "Deadline:",
        "Anything else we should know:",
      ].join("\n"),
    );
    Linking.openURL(`mailto:${SALES_EMAIL}?subject=${subject}&body=${body}`);
  };

  return (
    <Screen scroll title="Professional assessment">
      <Hero
        eyebrow="PROFESSIONAL SERVICE"
        title="Need a human in the loop? We'll assess and remediate it for you."
        subtitle={`For procurement, legal and high-stakes document sets that need a defensible, expert-reviewed conformance result — not just an automated pass. Scoped quotes from ${PRICE_FROM} per document set.`}
      >
        <View style={styles.ctaRow}>
          <Button title="Request an assessment" onPress={onRequest} />
          <Button title="Or self-serve from $0.19/page" variant="ghost" href="/billing" />
        </View>
      </Hero>

      {/* Why a human ---------------------------------------------------------- */}
      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <Chip label="Honest by design" tone="info" />
          <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 18 }]}>
            Automation gets you most of the way. Sometimes you need the rest.
          </Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8, maxWidth: 660 }]}>
          Our self-serve product auto-fixes the bulk of accessibility issues — alt text, headings, titles, lists,
          tables, language, link text, form labels and contrast — and tells you exactly what it couldn't verify.
          For complex documents, and when you need a conformance result someone will stand behind, a qualified
          evaluator handles the judgment calls automation can't make. This service is that human pass.
        </Text>
      </Card>

      {/* What's included ----------------------------------------------------- */}
      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>What's included</Text>
        <View style={styles.grid}>
          {INCLUDED.map((it) => (
            <View
              key={it.title}
              style={[styles.cell, { borderRadius: theme.radius.md, borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 }]}
            >
              <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 16 }]}>{it.title}</Text>
              <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 4 }]}>{it.body}</Text>
            </View>
          ))}
        </View>
      </Card>

      {/* Who it's for -------------------------------------------------------- */}
      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Who it's for</Text>
        <View style={styles.grid}>
          {FOR_WHOM.map((it) => (
            <View
              key={it.title}
              style={[styles.cell, { borderRadius: theme.radius.md, borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 }]}
            >
              <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 16 }]}>{it.title}</Text>
              <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 4 }]}>{it.body}</Text>
            </View>
          ))}
        </View>
      </Card>

      {/* How it works -------------------------------------------------------- */}
      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>How it works</Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8, maxWidth: 660 }]}>
          Tell us your document count, formats, required standards and deadline. We send a fixed, scoped quote
          (from {PRICE_FROM} per set, based on volume and complexity), run the engine, hand-correct the rest, and
          deliver the remediated files plus a signed-off conformance report. No subscription required.
        </Text>
        <View style={[styles.ctaRow, { marginTop: 16 }]}>
          <Button title="Request an assessment" onPress={onRequest} />
        </View>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 10 }]}>
          Prefer to do it yourself? The self-serve product remediates documents automatically from {"$0.19"}/page —
          see <Text style={{ color: theme.colors.accent, fontWeight: "700" }} onPress={() => router.push("/billing" as any)}>pricing</Text>.
        </Text>
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  ctaRow: { flexDirection: "row", gap: 12, marginTop: 16, flexWrap: "wrap" },
  grid: { flexDirection: "row", gap: 12, flexWrap: "wrap", marginTop: 12 },
  cell: { flex: 1, minWidth: 240, borderWidth: 1, padding: 14 },
});
