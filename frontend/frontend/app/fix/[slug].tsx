/**
 * /fix/<slug> — one public page per accessibility issue we detect.
 *
 * This is the search-traffic surface. People don't google "accessibility
 * remediation platform"; they google their actual problem — "how to add alt
 * text to a PDF", "pdf has no tags", "table header row 508". Each of these
 * pages answers that question honestly and completely, including how to do it
 * BY HAND with no product involved, and then shows that we can do it
 * automatically. A page that only pitches would deserve to rank nowhere.
 *
 * Content comes from two places that are already the source of truth:
 * issueCatalog.ts (what it is / why it matters / what our auto-fix does) and
 * fixGuides.ts (the per-format manual procedure). Nothing is hand-typed here,
 * so these pages cannot drift from what the product actually detects and does.
 *
 * generateStaticParams makes every one of these a real HTML file in the static
 * export — without it the route ships as a single JS shell and search engines
 * see nothing.
 */
import { useLocalSearchParams, useRouter } from "expo-router";
import { StyleSheet, Text, View } from "react-native";

import { CATALOG_ENTRIES, lookupIssue } from "../../src/domain/issueCatalog";
import {
  FIX_GUIDES,
  FORMAT_LABELS,
  FixFormat,
  GUIDE_EXCLUDED,
  ruleSlug,
  slugToRuleId,
} from "../../src/domain/fixGuides";
import { Button } from "../../src/ui/components/Button";
import { Card } from "../../src/ui/components/Card";
import { Chip } from "../../src/ui/components/Chip";
import { Hero } from "../../src/ui/components/Hero";
import { Screen } from "../../src/ui/components/Screen";
import { Seo, useJsonLd } from "../../src/ui/components/Seo";
import { useTheme } from "../../src/ui/useTheme";

/** Every rule that gets a page. Exported so /fix (the index) and the sitemap
 *  generator agree with this route exactly. */
export const GUIDE_RULES = CATALOG_ENTRIES.filter((e) => !GUIDE_EXCLUDED.has(e.ruleId));

export async function generateStaticParams(): Promise<{ slug: string }[]> {
  return GUIDE_RULES.map((e) => ({ slug: ruleSlug(e.ruleId) }));
}

const FORMAT_ORDER: FixFormat[] = ["pdf", "docx", "pptx", "html"];

export default function FixGuideScreen() {
  const theme = useTheme();
  const router = useRouter();
  const params = useLocalSearchParams<{ slug?: string }>();
  const slug = typeof params.slug === "string" ? params.slug : "";
  const ruleId = slugToRuleId(slug);
  const entry = lookupIssue(ruleId);
  const guide = FIX_GUIDES[ruleId];
  const formats = FORMAT_ORDER.filter((f) => guide?.[f]);

  const title = `${entry.title} — how to fix it | 508 Agent`;
  const description =
    `${entry.summary} ${entry.why}`.slice(0, 155).trim() + "…";

  // HowTo rich result, built from the same steps shown on the page. Only when
  // we actually have procedural steps — schema.org HowTo without steps is a
  // structured-data error, and Google penalises markup that doesn't match the
  // visible page.
  const firstGuide = formats.length ? guide?.[formats[0]] : undefined;
  useJsonLd(
    firstGuide
      ? {
          "@context": "https://schema.org",
          "@type": "HowTo",
          name: `How to fix: ${entry.title}`,
          description: entry.why,
          step: firstGuide.steps.map((s, i) => ({
            "@type": "HowToStep",
            position: i + 1,
            text: s,
          })),
        }
      : {
          "@context": "https://schema.org",
          "@type": "Article",
          headline: entry.title,
          description: entry.summary,
        },
  );

  const standards = [
    ...entry.standards.wcag.map((s) => `WCAG ${s}`),
    ...entry.standards.section508.map((s) => `Section 508 ${s}`),
    ...entry.standards.pdfUa.map((s) => `PDF/UA ${s}`),
  ];

  return (
    <Screen scroll title={entry.title}>
      <Seo title={title} description={description} />

      <Hero eyebrow="HOW TO FIX" title={entry.title} subtitle={entry.summary}>
        <View style={styles.ctaRow}>
          <Button title="Scan my document free" href="/audit" />
          <Button title="All issues" variant="ghost" href="/fix" />
        </View>
      </Hero>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Why it matters</Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8, lineHeight: 23 }]}>
          {entry.why}
        </Text>
        {standards.length ? (
          <View style={styles.chipRow}>
            {standards.map((s) => (
              <Chip key={s} label={s} tone="default" />
            ))}
          </View>
        ) : null}
      </Card>

      {formats.length ? (
        formats.map((fmt) => {
          const g = guide![fmt]!;
          return (
            <Card key={fmt}>
              <View style={styles.headRow}>
                <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
                  Fix it by hand: {FORMAT_LABELS[fmt]}
                </Text>
                <Chip label={g.tool} tone="info" />
              </View>
              <View style={{ marginTop: 12, gap: 10 }}>
                {g.steps.map((step, i) => (
                  <View key={i} style={styles.stepRow}>
                    <View
                      style={[
                        styles.stepNum,
                        { backgroundColor: theme.colors.surface3, borderColor: theme.colors.border },
                      ]}
                    >
                      <Text style={{ color: theme.colors.accent, fontWeight: "800", fontSize: 12 }}>
                        {i + 1}
                      </Text>
                    </View>
                    <Text style={[theme.typography.body, { color: theme.colors.text, flex: 1, lineHeight: 22 }]}>
                      {step}
                    </Text>
                  </View>
                ))}
              </View>
              {g.note ? (
                <View
                  style={[
                    styles.note,
                    { borderColor: theme.colors.accent, backgroundColor: theme.colors.surface2, borderRadius: theme.radius.md },
                  ]}
                >
                  <Text style={{ color: theme.colors.accent, fontSize: 12, fontWeight: "800" }}>WORTH KNOWING</Text>
                  <Text style={[theme.typography.body, { color: theme.colors.text, flex: 1, fontSize: 13, lineHeight: 20 }]}>
                    {g.note}
                  </Text>
                </View>
              ) : null}
            </Card>
          );
        })
      ) : (
        <Card>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Fixing it by hand</Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8, lineHeight: 23 }]}>
            This one depends heavily on how your document was built, so there's no single set of
            steps we'd stand behind for every case. Our scan points at the exact elements involved,
            which is usually the hard part.
          </Text>
        </Card>
      )}

      <Card variant="data">
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Or let us do it</Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8, lineHeight: 23 }]}>
          {entry.autoFix}
        </Text>
        {entry.manualJudgment ? (
          <>
            <Text style={[theme.typography.eyebrow, { color: theme.colors.warning, marginTop: 14 }]}>
              WHERE YOU STILL HAVE TO DECIDE
            </Text>
            <Text style={[theme.typography.body, { color: theme.colors.text, marginTop: 4, lineHeight: 22 }]}>
              {entry.manualJudgment}
            </Text>
          </>
        ) : null}
        <View style={[styles.ctaRow, { marginTop: 16 }]}>
          <Button title="Scan my document free" href="/audit" />
        </View>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 10 }]}>
          Scanning is free and needs no card. You only spend credits to download a fixed file.
        </Text>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Other issues we check</Text>
        <View style={styles.chipRow}>
          {GUIDE_RULES.filter((e) => e.ruleId !== entry.ruleId)
            .slice(0, 12)
            .map((e) => (
              <Button
                key={e.ruleId}
                title={e.title}
                variant="ghost"
                href={`/fix/${ruleSlug(e.ruleId)}`}
              />
            ))}
        </View>
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  ctaRow: { flexDirection: "row", gap: 10, flexWrap: "wrap", marginTop: 12 },
  chipRow: { flexDirection: "row", gap: 8, flexWrap: "wrap", marginTop: 12 },
  headRow: { flexDirection: "row", alignItems: "center", gap: 10, flexWrap: "wrap" },
  stepRow: { flexDirection: "row", gap: 10, alignItems: "flex-start" },
  stepNum: {
    width: 22,
    height: 22,
    borderRadius: 11,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
    marginTop: 1,
  },
  note: { flexDirection: "row", gap: 10, alignItems: "flex-start", borderWidth: 1, padding: 12, marginTop: 14 },
});
