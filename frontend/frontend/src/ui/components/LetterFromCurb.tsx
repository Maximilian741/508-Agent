/**
 * LetterFromCurb - a warm, conversational summary of an audit result.
 *
 * Replaces (or augments) the dry stats at the top of the audit results card
 * with a paragraph or two of plain prose so the reader gets the gist before
 * having to parse numbers and chips.
 *
 * Deterministic, generated client-side from the PipelineResponse shape. NO
 * LLM calls. Numbers (auto-fixable count, judgment-call count, time estimate)
 * are derived from the response.
 */

import { StyleSheet, Text, View } from "react-native";

import { PipelineResponse, PipelineViolation } from "../../api/client";
import { useTheme } from "../useTheme";
import { Card } from "./Card";

interface LetterFromCurbProps {
  report: PipelineResponse;
}

/**
 * A violation is "auto-fixable" when at least one of its recommended actions
 * is something we can apply deterministically without a human judgment call.
 * The negative list (manual review, generate alt, link rewrite) tracks the
 * heuristics surfaced in audit.tsx's bulkDecide rules.
 */
function isAutoFixable(v: PipelineViolation): boolean {
  if (!v.recommendedActions || v.recommendedActions.length === 0) return false;
  const judgmentActions = new Set([
    "FLAG_FOR_MANUAL_REVIEW",
    "GENERATE_ALT_TEXT",
    "IMPROVE_LINK_TEXT",
  ]);
  return v.recommendedActions.some((a) => !judgmentActions.has(a));
}

interface BucketCounts {
  altText: number;
  language: number;
  headings: number;
  tables: number;
  links: number;
  other: number;
}

function categorize(violations: PipelineViolation[]): BucketCounts {
  const buckets: BucketCounts = {
    altText: 0,
    language: 0,
    headings: 0,
    tables: 0,
    links: 0,
    other: 0,
  };
  for (const v of violations) {
    const id = v.ruleId.toLowerCase();
    if (id.includes("alt") || id.includes("figure") || id.includes("image")) {
      buckets.altText += 1;
    } else if (id.includes("lang")) {
      buckets.language += 1;
    } else if (id.includes("heading")) {
      buckets.headings += 1;
    } else if (id.includes("table")) {
      buckets.tables += 1;
    } else if (id.includes("link")) {
      buckets.links += 1;
    } else {
      buckets.other += 1;
    }
  }
  return buckets;
}

function topCategoriesPhrase(buckets: BucketCounts, limit = 3): string {
  const labels: Array<{ key: keyof BucketCounts; phrase: string; count: number }> = [
    { key: "altText", phrase: "alt text", count: buckets.altText },
    { key: "language", phrase: "language tag", count: buckets.language },
    { key: "headings", phrase: "heading levels", count: buckets.headings },
    { key: "tables", phrase: "table structure", count: buckets.tables },
    { key: "links", phrase: "link text", count: buckets.links },
  ];
  const present = labels.filter((l) => l.count > 0).slice(0, limit);
  if (present.length === 0) return "structural metadata";
  if (present.length === 1) return present[0].phrase;
  if (present.length === 2) return present[0].phrase + " and " + present[1].phrase;
  return (
    present[0].phrase + ", " + present[1].phrase + ", " + present[2].phrase
  );
}

function gradeFromScore(score: number): string {
  if (score >= 95) return "A+";
  if (score >= 90) return "A";
  if (score >= 80) return "B";
  if (score >= 70) return "C";
  if (score >= 60) return "D";
  return "F";
}

export function LetterFromCurb({ report }: LetterFromCurbProps) {
  const theme = useTheme();
  const styles = createStyles(theme);

  const total = report.violations.length;
  const pageCount = report.summary.pageCount ?? 0;
  const score = Math.round(report.score?.score ?? 0);
  const startingGrade = report.score?.grade || gradeFromScore(score);

  const autoFixable = report.violations.filter(isAutoFixable);
  const judgmentCalls = report.violations.filter((v) => !isAutoFixable(v));
  const autoCount = autoFixable.length;
  const pendingCount = judgmentCalls.length;

  const autoCategories = topCategoriesPhrase(categorize(autoFixable), 3);
  const pendingCategories = topCategoriesPhrase(categorize(judgmentCalls), 2);

  const minutes = Math.max(2, Math.ceil(pendingCount * 0.7));

  // Build the prose. We pick one of three shapes based on the spread of
  // auto-fixable vs. judgment-call work, so the letter feels tailored.
  let opening: string;
  if (total === 0) {
    opening =
      "Hi - I took a look at your document. It's " +
      pageCount +
      " page" +
      (pageCount === 1 ? "" : "s") +
      " and scored " +
      score +
      " right out of the gate. I did not find anything that needs remediation. Nice work.";
  } else {
    opening =
      "Hi - I took a look at your document. It's " +
      pageCount +
      " page" +
      (pageCount === 1 ? "" : "s") +
      " and got a " +
      score +
      " (" +
      startingGrade +
      ") to start.";
  }

  let body = "";
  if (total > 0) {
    if (autoCount > 0 && pendingCount > 0) {
      body =
        " The good news: " +
        autoCount +
        " issue" +
        (autoCount === 1 ? " was" : "s were") +
        " straightforward enough that I can fix " +
        (autoCount === 1 ? "it" : "them") +
        " automatically (" +
        autoCategories +
        "). The other " +
        pendingCount +
        " need" +
        (pendingCount === 1 ? "s" : "") +
        " your eyes - mostly judgment calls about " +
        pendingCategories +
        ". Walk through them below and you'll be at A+ in about " +
        minutes +
        " minute" +
        (minutes === 1 ? "" : "s") +
        ".";
    } else if (autoCount > 0 && pendingCount === 0) {
      body =
        " The good news: all " +
        autoCount +
        " finding" +
        (autoCount === 1 ? " is" : "s are") +
        " straightforward auto-fixes (" +
        autoCategories +
        "). Approve them below and you're done in about " +
        minutes +
        " minute" +
        (minutes === 1 ? "" : "s") +
        ".";
    } else {
      body =
        " All " +
        pendingCount +
        " finding" +
        (pendingCount === 1 ? "" : "s") +
        " need your eyes - they're judgment calls about " +
        pendingCategories +
        ". Walk through them below and you'll be at A+ in about " +
        minutes +
        " minute" +
        (minutes === 1 ? "" : "s") +
        ".";
    }
  }

  const letter = opening + body;

  return (
    <Card>
      <View style={styles.row}>
        <View style={[styles.avatar, { backgroundColor: theme.colors.accent }]}>
          <Text style={[styles.avatarText, { color: theme.colors.onAccent }]}>C</Text>
        </View>
        <View style={styles.body}>
          <Text style={[theme.typography.eyebrow, { color: theme.colors.textMuted, marginBottom: 4 }]}>
            A NOTE FROM CURB
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.text, lineHeight: 22 }]}>
            {letter}
          </Text>
        </View>
      </View>
    </Card>
  );
}

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    row: {
      flexDirection: "row",
      alignItems: "flex-start",
      gap: 14,
    },
    avatar: {
      width: 36,
      height: 36,
      borderRadius: 18,
      alignItems: "center",
      justifyContent: "center",
    },
    avatarText: {
      color: "#FFFFFF",
      fontWeight: "800",
      fontSize: 16,
    },
    body: {
      flex: 1,
    },
  });
