/**
 * Public pricing page — transparent, honest, conversion-focused.
 *
 * The existing /billing screen is the authenticated purchase flow; this is the
 * public-facing page that lets a buyer self-qualify BEFORE signing up. Its
 * differentiator (per the go-to-market analysis) is transparency: a real
 * "from $X per page" figure derived from the actual credit math, shown against
 * the $5–25/page manual-remediation market. All numbers come from
 * src/domain/pricing.ts (which mirrors the backend), so nothing here is a
 * hand-typed marketing claim. Honest by design: we sell remediation + scan,
 * not a formal conformance determination.
 */
import { StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { Hero } from "../src/ui/components/Hero";
import { Pressable } from "react-native";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";
import {
  CERT_CREDITS,
  CREDIT_PACKS,
  FORMAT_CREDITS,
  FORMAT_LABELS,
  FREE_CREDITS,
  FormatKey,
  MANUAL_PER_PAGE,
  SUB_PLANS,
  fmtUsd,
  headlineFromPerPage,
  pricePerDoc,
  priceRange,
} from "../src/domain/pricing";

const FORMATS: FormatKey[] = ["pdf", "docx", "pptx", "html"];

export default function PricingScreen() {
  const theme = useTheme();
  const router = useRouter();
  const headline = headlineFromPerPage();
  const studio = CREDIT_PACKS.find((p) => p.key === "studio")!;

  return (
    <Screen scroll title="Pricing">
      <Hero
        eyebrow="PRICING"
        title="Honest, transparent pricing."
        subtitle={`From ${fmtUsd(headline)} per page — and you only pay for fixes that actually persist into your file. Manual remediation runs ${fmtUsd(
          MANUAL_PER_PAGE.low,
        )}–${fmtUsd(MANUAL_PER_PAGE.high)} per page. Your first ${FREE_CREDITS} credits are free, no card.`}
      >
        <View style={styles.ctaRow}>
          <Button title={`Start free (${FREE_CREDITS} credits)`} onPress={() => router.push("/audit" as any)} />
          <Button title="Buy credits" variant="ghost" onPress={() => router.push("/billing" as any)} />
        </View>
      </Hero>

      {/* Value vs. manual ----------------------------------------------------- */}
      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <Chip label="10–25× cheaper" tone="success" />
          <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 18 }]}>
            Automated, so it costs a fraction of manual
          </Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8, maxWidth: 640 }]}>
          Agencies charge {fmtUsd(MANUAL_PER_PAGE.low)}–{fmtUsd(MANUAL_PER_PAGE.high)} per page to remediate a
          document by hand. 508 Agent does the same WCAG 2.1 AA, Section 508 and PDF/UA fixes automatically — alt
          text, headings, titles, lists, tables, language, link text, form labels and contrast — for{" "}
          <Text style={{ color: theme.colors.text, fontWeight: "700" }}>{fmtUsd(headline)}–{fmtUsd(pricePerDoc("pdf", CREDIT_PACKS[0]))} per PDF</Text>{" "}
          depending on volume. Analysis is always free.
        </Text>
        <Pressable
          // No accessibilityLabel — the visible text is already the label, and
          // paraphrasing it in aria breaks WCAG 2.5.3 (Label in Name).
          accessibilityRole="button"
          onPress={() => router.push("/savings" as any)}
          style={({ hovered }: any) => [{ marginTop: 12, alignSelf: "flex-start" }, hovered ? { opacity: 0.7 } : null]}
        >
          <Text style={{ color: theme.colors.accent, fontWeight: "700", fontSize: 14 }}>
            Calculate your savings vs. manual remediation
          </Text>
        </Pressable>
      </Card>

      {/* Per-document price table -------------------------------------------- */}
      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>What a document costs</Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 6, maxWidth: 640 }]}>
          You spend credits per remediated file (PDFs cost the most because they're the heaviest to fix). The
          effective price drops with bulk packs. Re-running a file you didn't change is free.
        </Text>
        <View style={[styles.table, { borderColor: theme.colors.border, borderRadius: theme.radius.md }]}>
          <View style={[styles.row, styles.headRow, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 }]}>
            <Text style={[styles.cell, styles.headCell, { color: theme.colors.textMuted, flex: 2 }]}>Format</Text>
            <Text style={[styles.cell, styles.headCell, { color: theme.colors.textMuted }]}>Credits</Text>
            <Text style={[styles.cell, styles.headCell, { color: theme.colors.textMuted }]}>From / page</Text>
            <Text style={[styles.cell, styles.headCell, { color: theme.colors.textMuted }]}>Single</Text>
          </View>
          {FORMATS.map((f) => {
            const range = priceRange(f);
            return (
              <View key={f} style={[styles.row, { borderColor: theme.colors.border }]}>
                <Text style={[styles.cell, { color: theme.colors.text, flex: 2, fontWeight: "600" }]}>{FORMAT_LABELS[f]}</Text>
                <Text style={[styles.cell, { color: theme.colors.textMuted }]}>{FORMAT_CREDITS[f]}</Text>
                <Text style={[styles.cell, { color: theme.colors.text, fontWeight: "700" }]}>{fmtUsd(range.low)}</Text>
                <Text style={[styles.cell, { color: theme.colors.textMuted }]}>{fmtUsd(range.high)}</Text>
              </View>
            );
          })}
        </View>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 8 }]}>
          "From / page" is the bulk ({studio.name}) rate; "Single" is the {CREDIT_PACKS[0].name}-pack rate. A
          conformance certificate is {CERT_CREDITS} credits (free on any subscription).
        </Text>
      </Card>

      {/* Credit packs -------------------------------------------------------- */}
      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Pay as you go</Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 6 }]}>
          One-time credit packs. Credits never expire. No subscription required.
        </Text>
        <View style={styles.cards}>
          {CREDIT_PACKS.map((p) => (
            <View
              key={p.key}
              style={[
                styles.tier,
                {
                  borderRadius: theme.radius.md,
                  borderColor: p.highlight ? theme.colors.accent : theme.colors.border,
                  borderWidth: p.highlight ? 2 : 1,
                  backgroundColor: theme.colors.surface2,
                },
              ]}
            >
              {p.highlight ? (
                <View style={{ alignSelf: "flex-start", marginBottom: 6 }}>
                  <Chip label="Best value" tone="info" />
                </View>
              ) : null}
              <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 16 }]}>{p.name}</Text>
              <Text style={[theme.typography.h1, { color: theme.colors.text, fontSize: 26, marginTop: 4 }]}>
                {fmtUsd(p.priceCents / 100)}
              </Text>
              <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 2 }]}>
                {p.credits.toLocaleString()} credits · ~{Math.floor(p.credits / FORMAT_CREDITS.pdf)} PDFs
              </Text>
              <Text style={[theme.typography.caption, { color: theme.colors.text, marginTop: 6, fontWeight: "700" }]}>
                {fmtUsd(pricePerDoc("pdf", p))} / PDF
              </Text>
              <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 6 }]}>{p.blurb}</Text>
            </View>
          ))}
        </View>
      </Card>

      {/* Subscriptions ------------------------------------------------------- */}
      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Subscriptions</Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 6 }]}>
          A monthly or annual credit allowance with shared seats and conformance certificates included. Best for
          teams and recurring volume. Annual is two months free.
        </Text>
        <View style={styles.cards}>
          {SUB_PLANS.map((s) => (
            <View
              key={s.key}
              style={[styles.tier, { borderRadius: theme.radius.md, borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 }]}
            >
              <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 16 }]}>{s.name}</Text>
              <Text style={[theme.typography.h1, { color: theme.colors.text, fontSize: 26, marginTop: 4 }]}>
                {fmtUsd(s.monthlyCents / 100)}
                <Text style={[theme.typography.body, { color: theme.colors.textMuted, fontSize: 14 }]}> /mo</Text>
              </Text>
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 2 }]}>
                or {fmtUsd(s.annualCents / 100)}/yr (2 months free)
              </Text>
              <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8 }]}>
                {s.creditsPerMonth.toLocaleString()} credits / month
              </Text>
              <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
                {s.seats} seats · shared wallet
              </Text>
              <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
                Conformance certificates included
              </Text>
              <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>Batch / folder processing</Text>
            </View>
          ))}
        </View>
        <View style={[styles.ctaRow, { marginTop: 16 }]}>
          <Button title="Start free" onPress={() => router.push("/audit" as any)} />
          <Button title="Compare plans & buy" variant="ghost" onPress={() => router.push("/billing" as any)} />
        </View>
      </Card>

      {/* Honesty note -------------------------------------------------------- */}
      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <Chip label="Honest by design" tone="info" />
          <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 16 }]}>
            What you're paying for
          </Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8, maxWidth: 660 }]}>
          508 Agent provides automated remediation plus a scan, and a remediation summary you can verify — not a
          formal third-party conformance determination. The score only counts fixes that genuinely persist into
          your file, and the report marks anything we don't auto-check as "needs manual review." Complex documents
          may still need a human pass; we'll tell you exactly which items those are.
        </Text>
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  ctaRow: { flexDirection: "row", gap: 12, marginTop: 16, flexWrap: "wrap" },
  table: { borderWidth: 1, marginTop: 12, overflow: "hidden" },
  row: { flexDirection: "row", borderTopWidth: 1, paddingVertical: 10, paddingHorizontal: 12, alignItems: "center" },
  headRow: { borderTopWidth: 0 },
  cell: { flex: 1, fontSize: 14 },
  headCell: { fontSize: 11, fontWeight: "700", textTransform: "uppercase", letterSpacing: 0.5 },
  cards: { flexDirection: "row", gap: 12, flexWrap: "wrap", marginTop: 12 },
  tier: { flex: 1, minWidth: 220, padding: 16 },
});
