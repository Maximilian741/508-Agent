/**
 * Help & WCAG Glossary - structured as a reference document.
 *
 * Page is a real reference: serif display title, left-rail sticky TOC on web,
 * flowing prose with horizontal rules between rule entries. No card-of-everything.
 */

import { useMemo, useState } from "react";
import { Linking, Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import {
  CATALOG_ENTRIES,
  IssueCatalogEntry,
} from "../src/domain/issueCatalog";
import { Chip } from "../src/ui/components/Chip";
import { EmptyState } from "../src/ui/components/EmptyState";
import { Hero } from "../src/ui/components/Hero";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";
import { Seo } from "../src/ui/components/Seo";

const SEVERITY_ORDER: IssueCatalogEntry["severity"][] = ["error", "warning", "info"];

const FAQ: { question: string; answer: string }[] = [
  {
    question: "What is the difference between WCAG, Section 508, and PDF/UA?",
    answer:
      "WCAG (Web Content Accessibility Guidelines) is the international standard published by the W3C - it covers web pages and digital documents. Section 508 is the U.S. federal procurement law that adopts WCAG 2.0 (Level AA) by reference. PDF/UA is the accessibility specification specific to PDF files - it complements WCAG with PDF-specific structural requirements like proper tag trees and reading order.",
  },
  {
    question: "What are conformance levels A, AA, and AAA?",
    answer:
      "WCAG ranks success criteria by impact. Level A is the floor - failures will block significant numbers of users. Level AA is the practical target most regulations require. Level AAA is aspirational; some criteria are technically incompatible with certain content (for example, sign language interpretation for live audio). This tool's score weighting reflects the AA target.",
  },
  {
    question: "Why does the auto-fix flag so many things for human review?",
    answer:
      "Several remediations require human judgement - the meaning of an image, the right phrasing for a link, whether a heading hierarchy break is intentional. The agent makes a starting suggestion (heuristic or AI) and queues it for you to approve, edit, or reject. Anything we apply silently is deterministic and reversible (e.g. removing alt text from a marked-decorative image).",
  },
  {
    question: "How accurate is the AI alt-text suggestion?",
    answer:
      "It depends on the provider. With ANTHROPIC_API_KEY or OPENAI_API_KEY set, you get vision-AI alt-text generation that is usually serviceable. Without a key, the heuristic provider produces filename- / context-derived phrases that should always be reviewed. Every suggestion is tagged with a confidence score and a provider label so you know what you are approving.",
  },
  {
    question: "What does the remediated file actually contain?",
    answer:
      "When you click Download remediated file, the backend applies every fix you approved and writes a new copy of your document: alt text, document title and language, heading renumbering, link-text rewrites (Word/PowerPoint, including links inside tables, text boxes and footnotes), table header rows (Word/PowerPoint), typed \"- item\" lines converted into real lists (Word numbering / PowerPoint bullets), and for PDFs a full structure tree: headings, lists, tables, figures with alt, link and form-field tagging, and header/footer artifacts. The score only counts fixes that genuinely persist into the file; anything else is honestly listed as pending manual work. Use \"Verify the fix\" after downloading to re-audit the fixed file for free.",
  },
  {
    question: "What does a remediation cost?",
    answer:
      "Analysis is always free: upload as many documents as you like and read every finding. Writing a remediated file costs credits by format: PDF 5 credits, Word (DOCX) 3 credits, PowerPoint (PPTX) 4 credits, HTML 3 credits. Certificates cost 2 credits, or are included free on Team and Business plans. New accounts start with 25 free credits.",
  },
  {
    question: "How do I get help?",
    answer:
      "Email support@508-agent.app and a human will get back to you. For privacy requests use privacy@508-agent.app; for security reports use security@508-agent.app (see SECURITY.md in the repository for our disclosure policy).",
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

  const isWeb = Platform.OS === "web";

  return (
    <Screen scroll title="Help & Glossary">
      <Seo
        title="Accessibility Rules, in Plain English — WCAG 2.1, Section 508 & PDF/UA Glossary"
        description="Every check 508 Agent runs, explained for humans: what breaks for screen-reader users, the exact WCAG/508/PDF-UA citation, and how the automated fix works."
      />
      <Hero
        eyebrow="HELP"
        title="Help & glossary"
        subtitle="Every accessibility check this tool runs, in plain English, with the underlying WCAG 2.1, Section 508, and PDF/UA citations. Use it as a reference when deciding whether to approve or reject a fix."
      />

      <View style={styles.layout}>
        {/* Left rail: sticky TOC on web. */}
        {isWeb ? (
          <View
            // @ts-ignore - position: sticky is web-only
            style={[styles.toc, { borderRightColor: theme.colors.border, position: "sticky" as any, top: 24 }]}
          >
            <Text
              style={[
                theme.typography.caption,
                { color: theme.colors.textMuted, marginBottom: 14 },
              ]}
            >
              ON THIS PAGE
            </Text>
            <TocLink href="errors" label={`Errors (${grouped.error?.length ?? 0})`} />
            <TocLink href="warnings" label={`Warnings (${grouped.warning?.length ?? 0})`} />
            <TocLink href="info" label={`Informational (${grouped.info?.length ?? 0})`} />
            <View style={{ height: 16 }} />
            <TocLink href="faq" label="Frequently asked" />
            <TocLink href="standards" label="Standards primer" />
          </View>
        ) : null}

        <View style={styles.body}>
          {/* Search */}
          <TextInput
            value={query}
            onChangeText={setQuery}
            placeholder="Filter rules - try 'alt text', 'heading', 'language'..."
            placeholderTextColor={theme.colors.textMuted}
            accessibilityLabel="Search help articles"
            style={[
              styles.search,
              {
                borderColor: theme.colors.border,
                color: theme.colors.text,
              },
            ]}
          />
          <Text
            style={[
              theme.typography.caption,
              { color: theme.colors.textMuted, marginTop: 8 },
            ]}
          >
            Showing {filteredEntries.length} of {CATALOG_ENTRIES.length} rules
          </Text>

          {filteredEntries.length === 0 ? (
            <View style={{ marginTop: 32 }}>
              <EmptyState
                title="No matches"
                message={`Nothing in the catalog matches "${query}". Try a broader term.`}
              />
            </View>
          ) : (
            SEVERITY_ORDER.map((severity) => {
              const entries = grouped[severity];
              if (!entries) return null;
              const anchor =
                severity === "error" ? "errors" : severity === "warning" ? "warnings" : "info";
              return (
                <View
                  key={severity}
                  // @ts-ignore - id used as scroll target on web
                  nativeID={anchor}
                  style={styles.sectionBlock}
                >
                  <Text
                    style={[
                      theme.typography.displaySmall as any,
                      {
                        color: theme.colors.text,
                        fontSize: 26,
                        marginBottom: 4,
                      },
                    ]}
                  >
                    {severity === "error"
                      ? "Errors"
                      : severity === "warning"
                      ? "Warnings"
                      : "Informational"}
                  </Text>
                  <Text
                    style={[
                      theme.typography.body,
                      { color: theme.colors.textMuted, marginBottom: 8 },
                    ]}
                  >
                    {severity === "error"
                      ? "Failures that block users with disabilities. Fix these first."
                      : severity === "warning"
                      ? "Concerns that should be addressed but are not strictly blocking."
                      : "Notices and best-practice nudges - useful, not required."}
                  </Text>
                  {entries.map((entry, i) => (
                    <EntryView
                      key={entry.ruleId}
                      entry={entry}
                      first={i === 0}
                    />
                  ))}
                </View>
              );
            })
          )}

          {/* FAQ section - flowing prose */}
          <View
            // @ts-ignore
            nativeID="faq"
            style={styles.sectionBlock}
          >
            <Text
              style={[
                theme.typography.displaySmall as any,
                {
                  color: theme.colors.text,
                  fontSize: 26,
                  marginBottom: 12,
                },
              ]}
            >
              Frequently asked
            </Text>
            {FAQ.map((item, i) => (
              <View
                key={i}
                style={[
                  styles.faqEntry,
                  i === 0
                    ? null
                    : { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: theme.colors.border },
                ]}
              >
                <Text
                  style={[
                    theme.typography.h2,
                    { color: theme.colors.text, fontSize: 17 },
                  ]}
                >
                  {item.question}
                </Text>
                <Text
                  style={[
                    theme.typography.body,
                    {
                      color: theme.colors.text,
                      marginTop: 8,
                      lineHeight: 24,
                      fontSize: 15,
                    },
                  ]}
                >
                  {item.answer}
                </Text>
              </View>
            ))}
          </View>

          <View
            // @ts-ignore
            nativeID="standards"
            style={styles.sectionBlock}
          >
            <Text
              style={[
                theme.typography.displaySmall as any,
                {
                  color: theme.colors.text,
                  fontSize: 26,
                  marginBottom: 12,
                },
              ]}
            >
              Standards primer
            </Text>
            <Text
              style={[
                theme.typography.body,
                { color: theme.colors.text, marginTop: 4, lineHeight: 24, fontSize: 15 },
              ]}
            >
              The three standards this tool cites cover overlapping ground.
              WCAG 2.1 is the underlying success-criteria document; everything
              else either adopts it (Section 508) or specialises it for a
              particular file format (PDF/UA). When a finding cites more than
              one, fixing it once satisfies all of them.
            </Text>
          </View>
        </View>
      </View>
    </Screen>
  );
}

function TocLink({ href, label }: { href: string; label: string }) {
  const theme = useTheme();
  const onPress = () => {
    if (Platform.OS !== "web") return;
    if (typeof document === "undefined") return;
    const el = document.getElementById(href);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`Jump to ${label}`}
      onPress={onPress}
      style={({ hovered }: any) => [
        styles.tocLink,
        hovered ? { opacity: 0.7 } : null,
      ]}
    >
      <Text
        style={[
          theme.typography.body,
          { color: theme.colors.text, fontSize: 14, fontWeight: "500" },
        ]}
      >
        {label}
      </Text>
    </Pressable>
  );
}

function EntryView({ entry, first }: { entry: IssueCatalogEntry; first: boolean }) {
  const theme = useTheme();
  return (
    <View
      style={[
        styles.entry,
        first
          ? null
          : { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: theme.colors.border },
      ]}
    >
      <View style={styles.entryHead}>
        <Text
          style={[
            theme.typography.h2,
            { color: theme.colors.text, fontSize: 18, flex: 1 },
          ]}
        >
          {entry.title}
        </Text>
        <Text
          style={[
            theme.typography.mono as any,
            { color: theme.colors.textMuted, fontSize: 11 },
          ]}
        >
          {entry.ruleId}
        </Text>
      </View>
      <Text
        style={[
          theme.typography.body,
          {
            color: theme.colors.text,
            marginTop: 8,
            lineHeight: 24,
            fontSize: 15,
          },
        ]}
      >
        {entry.summary}
      </Text>
      <Text
        style={[
          theme.typography.caption,
          { color: theme.colors.textMuted, marginTop: 14 },
        ]}
      >
        WHY IT MATTERS
      </Text>
      <Text
        style={[
          theme.typography.body,
          { color: theme.colors.text, marginTop: 4, lineHeight: 23 },
        ]}
      >
        {entry.why}
      </Text>
      <Text
        style={[
          theme.typography.caption,
          { color: theme.colors.textMuted, marginTop: 14 },
        ]}
      >
        HOW WE FIX IT
      </Text>
      <Text
        style={[
          theme.typography.body,
          { color: theme.colors.text, marginTop: 4, lineHeight: 23 },
        ]}
      >
        {entry.autoFix}
      </Text>
      {entry.manualJudgment ? (
        <>
          <Text
            style={[
              theme.typography.caption,
              { color: theme.colors.warning, marginTop: 14 },
            ]}
          >
            NEEDS HUMAN JUDGEMENT
          </Text>
          <Text
            style={[
              theme.typography.body,
              { color: theme.colors.text, marginTop: 4, lineHeight: 23 },
            ]}
          >
            {entry.manualJudgment}
          </Text>
        </>
      ) : null}
      <View style={styles.standardsRow}>
        {entry.standards.wcag.map((id) => (
          <Chip key={`wcag-${id}`} label={`WCAG ${id}`} tone="default" />
        ))}
        {entry.standards.section508.map((id) => (
          <Chip key={`508-${id}`} label={`s.508 ${id}`} tone="default" />
        ))}
        {entry.standards.pdfUa.map((id) => (
          <Chip key={`pdfua-${id}`} label={`PDF/UA ${id}`} tone="default" />
        ))}
        {/* The link below names the rule it belongs to. Every one of these
            used to be "Open external link", so a screen-reader user pulling up
            this page's link list saw the same words 29 times with nothing to
            choose between (WCAG 2.4.4) — and none of them contained the
            visible "Read W3C" (WCAG 2.5.3). Both are defects this product
            detects in other people's documents. */}
        {entry.learnMoreUrl ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`Read W3C reference for: ${entry.title} (external link)`}
            onPress={() => Linking.openURL(entry.learnMoreUrl)}
          >
            <Chip label="Read W3C" tone="info" />
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  layout: {
    flexDirection: "row",
    gap: 48,
    alignItems: "flex-start",
    flexWrap: "wrap",
  },
  toc: {
    width: 200,
    paddingRight: 24,
    borderRightWidth: StyleSheet.hairlineWidth,
    paddingVertical: 8,
  },
  tocLink: {
    paddingVertical: 6,
  },
  body: {
    flex: 1,
    minWidth: 320,
    maxWidth: 720,
  },
  search: {
    borderBottomWidth: 1,
    paddingVertical: 10,
    paddingHorizontal: 0,
    fontSize: 15,
  },
  sectionBlock: {
    marginTop: 48,
  },
  entry: {
    paddingTop: 28,
    paddingBottom: 4,
    marginTop: 0,
  },
  entryHead: {
    flexDirection: "row",
    alignItems: "baseline",
    gap: 12,
  },
  standardsRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 16,
  },
  faqEntry: {
    paddingTop: 22,
    paddingBottom: 6,
    marginTop: 0,
  },
});
