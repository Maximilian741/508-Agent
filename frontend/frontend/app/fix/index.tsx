/**
 * /fix — index of every accessibility issue we detect, each linking to its
 * own how-to-fix page.
 *
 * Two jobs. For a reader: a plain-language menu of "what's wrong with my
 * document" without needing to scan anything first. For search: a hub that
 * links to all ~30 /fix/<slug> pages, which is how those pages get found and
 * how the topic reads as coherent rather than as thirty orphans.
 */
import { useRouter } from "expo-router";
import { StyleSheet, Text, View } from "react-native";

import { CATALOG_ENTRIES } from "../../src/domain/issueCatalog";
import { FIX_GUIDES, GUIDE_EXCLUDED, ruleSlug } from "../../src/domain/fixGuides";
import { Button } from "../../src/ui/components/Button";
import { Card } from "../../src/ui/components/Card";
import { Chip } from "../../src/ui/components/Chip";
import { Hero } from "../../src/ui/components/Hero";
import { Screen } from "../../src/ui/components/Screen";
import { Seo } from "../../src/ui/components/Seo";
import { useTheme } from "../../src/ui/useTheme";

const RULES = CATALOG_ENTRIES.filter((e) => !GUIDE_EXCLUDED.has(e.ruleId));

const GROUPS: { title: string; blurb: string; match: (id: string) => boolean }[] = [
  {
    title: "Images",
    blurb: "What a screen reader says when it reaches a picture.",
    match: (id) => id.includes("ALT_TEXT") || id.includes("DECORATIVE"),
  },
  {
    title: "Headings & structure",
    blurb: "How someone skims your document without seeing it.",
    match: (id) => id.includes("HEADING") || id === "READING_ORDER_AMBIGUOUS" || id === "SLIDE_TITLE_MISSING",
  },
  {
    title: "Tables",
    blurb: "Whether a number can still be traced to its column.",
    match: (id) => id.startsWith("TABLE_"),
  },
  {
    title: "Links & forms",
    blurb: "Whether a control can be understood and operated.",
    match: (id) => id.startsWith("LINK_") || id.includes("FORM_FIELD") || id.includes("INPUT_") || id.includes("LABEL_IN_NAME") || id.includes("TABINDEX") || id.includes("IFRAME"),
  },
  {
    title: "Whole-document",
    blurb: "Settings that affect every page at once.",
    match: (id) =>
      id.startsWith("DOCUMENT_") || id === "PDF_UNTAGGED" || id === "SCANNED_DOCUMENT_NO_TEXT" || id === "LIST_STRUCTURE_INVALID" || id === "LOW_CONTRAST_TEXT",
  },
];

export default function FixIndexScreen() {
  const theme = useTheme();
  const router = useRouter();

  const claimed = new Set<string>();
  const grouped = GROUPS.map((g) => {
    const items = RULES.filter((e) => !claimed.has(e.ruleId) && g.match(e.ruleId));
    items.forEach((e) => claimed.add(e.ruleId));
    return { ...g, items };
  }).filter((g) => g.items.length);
  const leftovers = RULES.filter((e) => !claimed.has(e.ruleId));
  if (leftovers.length) {
    grouped.push({ title: "Other checks", blurb: "", match: () => false, items: leftovers } as any);
  }

  return (
    <Screen scroll title="How to fix accessibility issues">
      <Seo
        title="How to Fix Every Common Document Accessibility Issue (WCAG / Section 508)"
        description="Step-by-step fixes for the accessibility problems found in PDFs, Word documents, PowerPoint decks and web pages — in Acrobat, Word, PowerPoint and HTML, plus what can be fixed automatically."
      />
      <Hero
        eyebrow="FIX GUIDE"
        title="How to fix accessibility issues, one at a time"
        subtitle={`Plain-English instructions for the ${RULES.length} problems we check for — how to fix each by hand in Acrobat, Word, PowerPoint or HTML, and which ones we can just fix for you. No account needed to read any of it.`}
      >
        <View style={styles.ctaRow}>
          <Button title="Scan my document free" href="/audit" />
        </View>
      </Hero>

      {grouped.map((g) => (
        <Card key={g.title}>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>{g.title}</Text>
          {g.blurb ? (
            <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 4 }]}>{g.blurb}</Text>
          ) : null}
          <View style={{ marginTop: 12, gap: 10 }}>
            {g.items.map((e) => {
              const guided = Boolean(FIX_GUIDES[e.ruleId]);
              return (
                <View key={e.ruleId} style={[styles.row, { borderColor: theme.colors.border, borderRadius: theme.radius.sm }]}>
                  <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
                    <View style={styles.titleRow}>
                      <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "700" }]}>
                        {e.title}
                      </Text>
                      <Chip
                        label={e.severity === "error" ? "Error" : e.severity === "warning" ? "Warning" : "Info"}
                        tone={e.severity === "error" ? "danger" : e.severity === "warning" ? "warning" : "default"}
                      />
                      {guided ? <Chip label="Step-by-step" tone="success" /> : null}
                    </View>
                    <Text style={[theme.typography.caption, { color: theme.colors.textMuted, lineHeight: 18 }]}>
                      {e.summary}
                    </Text>
                  </View>
                  <Button
                    title="How to fix"
                    variant="secondary"
                    accessibilityLabel={`How to fix: ${e.title}`}
                    href={`/fix/${ruleSlug(e.ruleId)}`}
                  />
                </View>
              );
            })}
          </View>
        </Card>
      ))}
    </Screen>
  );
}

const styles = StyleSheet.create({
  ctaRow: { flexDirection: "row", gap: 10, flexWrap: "wrap", marginTop: 12 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 10,
    flexWrap: "wrap",
  },
  titleRow: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
});
