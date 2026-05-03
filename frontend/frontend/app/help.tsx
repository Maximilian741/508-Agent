/**
 * Help & WCAG Glossary screen.
 *
 * Searchable index of every flag this tool can detect, plus a quick primer on
 * the underlying WCAG 2.1 / Section 508 / PDF-UA criteria each flag cites.
 *
 * Why this matters: remediators new to a particular criterion need a fast
 * reference to back up their judgement when approving / rejecting fixes.
 * Bundling the glossary in-app saves them tab-juggling and gives the product
 * a real "we know this domain" feel.
 */

import { useMemo, useState } from "react";
import { Linking, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import {
  CATALOG_ENTRIES,
  IssueCatalogEntry,
} from "../src/domain/issueCatalog";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { EmptyState } from "../src/ui/components/EmptyState";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

const SEVERITY_ORDER: IssueCatalogEntry["severity"][] = ["error", "warning", "info"];

const FAQ: { question: string; answer: string }[] = [
  {
    question: "What is the difference between WCAG, Section 508, and PDF/UA?",
    answer:
      "WCAG (Web Content Accessibility Guidelines) is the international standard published by the W3C — it covers web pages and digital documents.  Section 508 is the U.S. federal procurement law that adopts WCAG 2.0 (Level AA) by reference.  PDF/UA is the accessibility specification specific to PDF files — it complements WCAG with PDF-specific structural requirements like proper tag trees and reading order.",
  },
  {
    question: "What are conformance levels A, AA, and AAA?",
    answer:
      "WCAG ranks success criteria by impact.  Level A is the floor — failures will block significant numbers of users.  Level AA is the practical target most regulations require.  Level AAA is aspirational; some criteria are technically incompatible with certain content (for example, sign language interpretation for live audio).  This tool's score weighting reflects the AA target.",
  },
  {
    question: "Why does the auto-fix flag so many things for human review?",
    answer:
      "Several remediations require human judgement — the meaning of an image, the right phrasing for a link, whether a heading hierarchy break is intentional.  The agent makes a starting suggestion (heuristic or AI) and queues it for you to approve, edit, or reject.  Anything we apply silently is deterministic and reversible (e.g. removing alt text from a marked-decorative image).",
  },
  {
    question: "How accurate is the AI alt-text suggestion?",
    answer:
      "It depends on the provider.  With ANTHROPIC_API_KEY or OPENAI_API_KEY set, you get vision-AI alt-text generation that's usually serviceable.  Without a key, the heuristic provider produces filename- / context-derived phrases that should always be reviewed.  Every suggestion is tagged with a confidence score and a provider label so you know what you're approving.",
  },
  {
    question: "What does the remediated file actually contain?",
    answer:
      "When you click Download remediated file, the backend re-runs the executors against the original file and produces a copy with deterministic fixes applied — set alt text, heading-level normalization, header-cell scope, document-language, and so on.  Items that need your judgement (link rewrites, AI alt-text) are queued for manual review and not silently overwritten.",
  },
  {
    question: "Can I configure custom rules or severities?",
    answer:
      "Policy packs let teams override which actions are allowed (auto-applicable, AI-required, manual-review-required) per rule.  See the Settings screen and the docs/POLICIES.md file for the schema.",
  },
];

export default function HelpScreen() {
  const theme = useTheme();
  const [query, setQuery] = useState("");

  const filteredEntries = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return CATALOG_ENTRIES;
    return CATALOG_ENTRIES.filter((e) =>
      [e.title, e.summary, e.why, e.autoFix, e.ruleId].some((s) =>
        s.toLowerCase().includes(q),
      ),
    );
  }, [query]);

  const grouped = useMemo(() => {
    const out: Record<string, IssueCatalogEntry[]> = {};
    for (const entry of filteredEntries) {
      out[entry.severity] = out[entry.severity] ?? [];
      out[entry.severity].push(entry);
    }
    return out;
  }, [filteredEntries]);

  return (
    <Screen scroll title="Help & Glossary">
      <View style={styles.header}>
        <Text style={[theme.typography.title, { color: theme.colors.text }]}>Help & Glossary</Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Reference for every accessibility check this tool runs, plus answers to common questions
          remediators ask.
        </Text>
      </View>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Quick search</Text>
        <TextInput
          value={query}
          onChangeText={setQuery}
          placeholder="Search rules — try 'alt text', 'heading', 'language'…"
          placeholderTextColor={theme.colors.textMuted}
          accessibilityLabel="Search help articles"
          style={[
            styles.searchInput,
            {
              borderColor: theme.colors.border,
              color: theme.colors.text,
              backgroundColor: theme.colors.surface,
            },
          ]}
        />
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
          {filteredEntries.length} of {CATALOG_ENTRIES.length} rules
        </Text>
      </Card>

      {filteredEntries.length === 0 ? (
        <EmptyState
          title="No matches"
          message={`Nothing in the catalog matches "${query}". Try a broader term.`}
        />
      ) : (
        SEVERITY_ORDER.map((severity) =>
          grouped[severity] ? (
            <Card key={severity}>
              <View style={styles.sectionHeader}>
                <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
                  {severity === "error"
                    ? "Errors"
                    : severity === "warning"
                    ? "Warnings"
                    : "Informational"}
                </Text>
                <Chip
                  label={`${grouped[severity].length}`}
                  tone={
                    severity === "error"
                      ? "danger"
                      : severity === "warning"
                      ? "warning"
                      : "info"
                  }
                />
              </View>
              {grouped[severity].map((entry) => (
                <EntryView key={entry.ruleId} entry={entry} />
              ))}
            </Card>
          ) : null,
        )
      )}

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Frequently asked</Text>
        {FAQ.map((item, i) => (
          <View
            key={i}
            style={[
              styles.faq,
              { borderColor: theme.colors.border },
              i === 0 ? { borderTopWidth: 0, paddingTop: 0 } : null,
            ]}
          >
            <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 15 }]}>
              {item.question}
            </Text>
            <Text style={[theme.typography.body, { color: theme.colors.text, marginTop: 6 }]}>
              {item.answer}
            </Text>
          </View>
        ))}
      </Card>
    </Screen>
  );
}

function EntryView({ entry }: { entry: IssueCatalogEntry }) {
  const theme = useTheme();
  return (
    <View style={[styles.entry, { borderColor: theme.colors.border }]}>
      <View style={styles.entryHead}>
        <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 15 }]}>
          {entry.title}
        </Text>
        <Chip
          label={entry.ruleId}
          tone="default"
          textStyle={{ fontFamily: "monospace" as any, fontSize: 10 }}
        />
      </View>
      <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
        {entry.summary}
      </Text>
      <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 8 }]}>
        Why it matters
      </Text>
      <Text style={[theme.typography.body, { color: theme.colors.text }]}>{entry.why}</Text>
      <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 8 }]}>
        How we fix it
      </Text>
      <Text style={[theme.typography.body, { color: theme.colors.text }]}>{entry.autoFix}</Text>
      {entry.manualJudgment ? (
        <>
          <Text style={[theme.typography.caption, { color: theme.colors.warning, marginTop: 8 }]}>
            Needs human judgement
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.text }]}>
            {entry.manualJudgment}
          </Text>
        </>
      ) : null}
      <View style={styles.standardsRow}>
        {entry.standards.wcag.map((id) => (
          <Chip key={`wcag-${id}`} label={`WCAG ${id}`} tone="default" />
        ))}
        {entry.standards.section508.map((id) => (
          <Chip key={`508-${id}`} label={`§508 ${id}`} tone="default" />
        ))}
        {entry.standards.pdfUa.map((id) => (
          <Chip key={`pdfua-${id}`} label={`PDF/UA ${id}`} tone="default" />
        ))}
        {entry.learnMoreUrl ? (
          <Pressable onPress={() => Linking.openURL(entry.learnMoreUrl)}>
            <Chip label="Read W3C ↗" tone="info" />
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  header: { gap: 6, marginBottom: 8 },
  searchInput: {
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
    marginTop: 10,
    fontSize: 16,
  },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
  },
  entry: {
    borderTopWidth: 1,
    paddingTop: 14,
    paddingBottom: 6,
    marginTop: 14,
    gap: 4,
  },
  entryHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 8,
    flexWrap: "wrap",
  },
  standardsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 12 },
  faq: { borderTopWidth: 1, paddingTop: 12, marginTop: 12, gap: 4 },
});
