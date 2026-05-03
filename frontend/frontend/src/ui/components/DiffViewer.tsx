/**
 * DiffViewer — before/after view of writer-applied remediations.
 *
 * The remediation API returns two free-form arrays after a run:
 *   • `applied[]`  — { kind, target_id, summary } — what the writer baked in
 *   • `skipped[]`  — { target_id, reason }       — what the writer left alone
 *
 * The `summary` string is human-written by the writer. It comes in a handful
 * of shapes that we try to coerce into a clean before/after table:
 *
 *   1. "img-3 /Alt -> 'A bar chart of quarterly revenue'"
 *      → before: "(no alt text)" or the prefix's old value if quoted
 *      → after:  "A bar chart of quarterly revenue"
 *
 *   2. "Title -> 'Q4 Budget'"
 *      → before: "(empty)"
 *      → after:  "Q4 Budget"
 *
 *   3. "img-7 marked decorative"
 *      → before: "(alt text)"
 *      → after:  "(decorative — no alt text)"
 *
 *   4. Anything else → render the raw summary as a single-line fallback so
 *      the user still sees what the writer reported.
 *
 * Visual:
 *   • Before panel uses the theme's `danger` colour at 12% opacity, with
 *     line-through text in `danger`.
 *   • After panel uses the theme's `success` colour at 12% opacity, with
 *     regular text in `success`.
 *   • On wide viewports (>= 720px) the two panels sit side-by-side with a
 *     "→" arrow between them. On narrower viewports they stack vertically
 *     with the arrow rotated to face downward.
 *
 * Accessibility:
 *   • The applied list is rendered as a semantic table on web (role="table")
 *     with row/cell roles so screen readers can announce columns. On native
 *     RN we fall back to plain Views — the labels are still readable.
 *   • The "→" glyph is marked aria-hidden because the surrounding labels
 *     ("Before"/"After") already convey the relationship.
 *   • The skipped panel announces itself as a region with aria-label="Did
 *     not apply" so screen readers can land on it directly.
 */

import { useEffect, useState } from "react";
import { Platform, StyleSheet, Text, View, useWindowDimensions } from "react-native";

import { useTheme } from "../useTheme";
import { Chip } from "./Chip";

export interface DiffViewerProps {
  applied: Array<{ kind: string; target_id: string; summary: string }>;
  skipped: Array<{ target_id: string; reason: string }>;
}

/**
 * Parsed view of a single applied summary string.
 *
 * `mode === "diff"` → render two side-by-side panels using `before`/`after`.
 * `mode === "raw"`  → render `raw` as a single line (we couldn't parse it).
 */
type ParsedSummary =
  | { mode: "diff"; before: string; after: string }
  | { mode: "raw"; raw: string };

/**
 * Try to coerce a writer summary into a before/after pair.
 *
 * The writer doesn't emit structured before/after fields, so we pattern-match
 * on the strings we know about and fall through to "raw" for anything else.
 */
function parseSummary(kind: string, summary: string): ParsedSummary {
  const trimmed = summary.trim();

  // "X marked decorative" — image was demoted to a decorative artifact.
  if (/\bmarked decorative\b/i.test(trimmed)) {
    return {
      mode: "diff",
      before: "(alt text)",
      after: "(decorative — no alt text)",
    };
  }

  // "<lhs> -> <rhs>" — most common shape. The rhs may be quoted with single
  // or double quotes. The lhs may include a leading target id and a slash-key
  // (e.g. "img-3 /Alt"); we don't surface those here, since the surrounding
  // row already prints the target id and the kind chip.
  const arrow = trimmed.match(/^(.*?)\s*->\s*(.*)$/);
  if (arrow) {
    const rhsRaw = arrow[2].trim();
    const after = stripQuotes(rhsRaw);

    // If the lhs itself was quoted (e.g. "'old' -> 'new'"), surface the old
    // value as the "before"; otherwise fall back to a kind-aware placeholder.
    const lhsRaw = arrow[1].trim();
    const lhsQuoted = matchQuoted(lhsRaw);
    const before = lhsQuoted ?? defaultBefore(kind);

    return { mode: "diff", before, after };
  }

  return { mode: "raw", raw: trimmed };
}

/**
 * Strip a single layer of matching surrounding quotes (' or ").
 * We don't try to be clever about escaped quotes — the writer doesn't emit
 * them in summaries.
 */
function stripQuotes(s: string): string {
  if (s.length >= 2) {
    const first = s[0];
    const last = s[s.length - 1];
    if ((first === "'" || first === '"') && first === last) {
      return s.slice(1, -1);
    }
  }
  return s;
}

/** Return the inner text if `s` is a single quoted string, else null. */
function matchQuoted(s: string): string | null {
  const m = s.match(/^['"](.*)['"]$/);
  return m ? m[1] : null;
}

/** Pick a sensible "before" placeholder when the writer didn't include one. */
function defaultBefore(kind: string): string {
  switch (kind) {
    case "alt_text":
      return "(no alt text)";
    case "title":
      return "(no title)";
    case "language":
      return "(no language set)";
    case "heading_level":
      return "(previous level)";
    case "header_row":
      return "(no header row)";
    case "decorative":
      return "(alt text)";
    default:
      return "(empty)";
  }
}

/** Tone for the kind chip. Decorative is muted; metadata is info; etc. */
function kindTone(kind: string): "default" | "success" | "warning" | "info" {
  switch (kind) {
    case "alt_text":
    case "header_row":
      return "success";
    case "decorative":
      return "default";
    case "title":
    case "language":
      return "info";
    case "heading_level":
      return "warning";
    default:
      return "default";
  }
}

/** Pretty-print the kind for the chip label. */
function kindLabel(kind: string): string {
  return kind.replace(/_/g, " ");
}

export function DiffViewer({ applied, skipped }: DiffViewerProps) {
  const theme = useTheme();
  const styles = createStyles(theme);

  // Track viewport width so we can stack the before/after panels on narrow
  // screens. useWindowDimensions is RN's web-friendly hook and re-renders
  // on resize, so this stays responsive without extra plumbing.
  const { width } = useWindowDimensions();
  const wide = width >= 720;

  // Hydration guard: the first paint on web should match the server-side
  // render (which has no window). After mount we flip to the real layout.
  // (RN-Web doesn't SSR by default in this project, but this also avoids a
  // flash for users whose viewport is being measured async.)
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const showWide = mounted && wide;

  // Empty applied[] is a valid, friendly state — the writer found nothing to
  // do because every approved fix was a no-op against the source file.
  const hasApplied = applied.length > 0;

  const tableA11y =
    Platform.OS === "web"
      ? ({ accessibilityRole: "table" as any } as any)
      : {};

  return (
    <View style={styles.root}>
      {hasApplied ? (
        <View style={styles.tableWrap} {...tableA11y}>
          <View
            style={styles.headerRow}
            {...(Platform.OS === "web" ? ({ accessibilityRole: "row" as any } as any) : {})}
          >
            <Text style={[styles.headerCell, styles.kindCol]}>Kind</Text>
            <Text style={[styles.headerCell, styles.targetCol]}>Target</Text>
            <Text style={[styles.headerCell, styles.diffCol]}>Before → After</Text>
          </View>
          {applied.map((entry, index) => (
            <DiffRow
              key={`${entry.target_id}-${entry.kind}-${index}`}
              entry={entry}
              wide={showWide}
            />
          ))}
        </View>
      ) : (
        <View style={styles.emptyApplied}>
          <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>
            No changes were baked in
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 4 }]}>
            Every approved fix was already a no-op against this file.
          </Text>
        </View>
      )}

      {skipped.length > 0 ? (
        <View
          style={styles.skippedPanel}
          accessibilityLabel="Did not apply"
          {...(Platform.OS === "web" ? ({ accessibilityRole: "region" as any } as any) : {})}
        >
          <Text style={[theme.typography.h2, styles.skippedTitle]}>
            Did not apply ({skipped.length})
          </Text>
          <Text style={[theme.typography.body, styles.skippedSubtitle]}>
            The remediated file was still produced, but these items need follow-up.
          </Text>
          <View style={styles.skippedList}>
            {skipped.map((s, i) => (
              <View key={`${s.target_id}-${i}`} style={styles.skippedRow}>
                <Text style={[theme.typography.mono, styles.skippedTarget]}>{s.target_id}</Text>
                <Text style={[theme.typography.body, styles.skippedReason]}>{s.reason}</Text>
              </View>
            ))}
          </View>
        </View>
      ) : null}
    </View>
  );
}

interface DiffRowProps {
  entry: { kind: string; target_id: string; summary: string };
  wide: boolean;
}

function DiffRow({ entry, wide }: DiffRowProps) {
  const theme = useTheme();
  const styles = createStyles(theme);
  const parsed = parseSummary(entry.kind, entry.summary);

  return (
    <View
      style={[styles.bodyRow, wide ? styles.bodyRowWide : styles.bodyRowStacked]}
      {...(Platform.OS === "web" ? ({ accessibilityRole: "row" as any } as any) : {})}
    >
      <View style={[styles.cell, styles.kindCol]}>
        <Chip label={kindLabel(entry.kind)} tone={kindTone(entry.kind)} />
      </View>
      <View style={[styles.cell, styles.targetCol]}>
        <Text
          style={[theme.typography.mono, { color: theme.colors.textMuted }]}
          selectable
        >
          {entry.target_id}
        </Text>
      </View>
      <View style={[styles.cell, styles.diffCol]}>
        {parsed.mode === "diff" ? (
          <DiffPanels before={parsed.before} after={parsed.after} wide={wide} />
        ) : (
          <Text
            style={[theme.typography.body, { color: theme.colors.text }]}
            accessibilityLabel={`Summary: ${parsed.raw}`}
          >
            {parsed.raw}
          </Text>
        )}
      </View>
    </View>
  );
}

interface DiffPanelsProps {
  before: string;
  after: string;
  wide: boolean;
}

function DiffPanels({ before, after, wide }: DiffPanelsProps) {
  const theme = useTheme();
  const styles = createStyles(theme);

  return (
    <View style={[styles.panels, wide ? styles.panelsRow : styles.panelsColumn]}>
      <View
        style={[
          styles.panel,
          {
            // 12% opacity over the danger colour. Hex+two-digit alpha is the
            // simplest cross-platform way to express this in RN-Web.
            backgroundColor: theme.colors.danger + "1F",
            borderColor: theme.colors.danger + "55",
          },
        ]}
        accessibilityLabel={`Before: ${before}`}
      >
        <Text style={[theme.typography.caption, { color: theme.colors.danger }]}>Before</Text>
        <Text
          style={[
            theme.typography.body,
            {
              color: theme.colors.danger,
              textDecorationLine: "line-through",
              opacity: 0.85,
              marginTop: 2,
            },
          ]}
          selectable
        >
          {before}
        </Text>
      </View>
      <Text
        style={[styles.arrow, { color: theme.colors.textMuted }]}
        // "→" is decorative — the labels above ("Before"/"After") carry the
        // semantics, so we hide the glyph from assistive tech.
        accessibilityElementsHidden
        importantForAccessibility="no-hide-descendants"
        {...(Platform.OS === "web" ? ({ "aria-hidden": true } as any) : {})}
      >
        {wide ? "→" : "↓"}
      </Text>
      <View
        style={[
          styles.panel,
          {
            backgroundColor: theme.colors.success + "1F",
            borderColor: theme.colors.success + "55",
          },
        ]}
        accessibilityLabel={`After: ${after}`}
      >
        <Text style={[theme.typography.caption, { color: theme.colors.success }]}>After</Text>
        <Text
          style={[
            theme.typography.body,
            { color: theme.colors.success, fontWeight: "600", marginTop: 2 },
          ]}
          selectable
        >
          {after}
        </Text>
      </View>
    </View>
  );
}

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    root: {
      gap: theme.spacing.md,
    },
    tableWrap: {
      borderWidth: 1,
      borderColor: theme.colors.border,
      borderRadius: theme.radius.lg,
      overflow: "hidden",
      backgroundColor: theme.colors.surface,
    },
    headerRow: {
      flexDirection: "row",
      alignItems: "center",
      paddingVertical: theme.spacing.sm,
      paddingHorizontal: theme.spacing.md,
      backgroundColor: theme.colors.surface2,
      borderBottomWidth: 1,
      borderBottomColor: theme.colors.border,
      gap: theme.spacing.md,
    },
    headerCell: {
      ...theme.typography.caption,
      color: theme.colors.textMuted,
    },
    bodyRow: {
      paddingVertical: theme.spacing.md,
      paddingHorizontal: theme.spacing.md,
      borderBottomWidth: 1,
      borderBottomColor: theme.colors.border,
      gap: theme.spacing.sm,
    },
    bodyRowWide: {
      flexDirection: "row",
      alignItems: "center",
    },
    bodyRowStacked: {
      flexDirection: "column",
      alignItems: "stretch",
    },
    cell: {
      // Cells share the same gap rhythm as the header row.
    },
    kindCol: {
      width: 140,
      flexShrink: 0,
    },
    targetCol: {
      width: 120,
      flexShrink: 0,
    },
    diffCol: {
      flex: 1,
      minWidth: 0,
    },
    panels: {
      gap: theme.spacing.sm,
    },
    panelsRow: {
      flexDirection: "row",
      alignItems: "stretch",
    },
    panelsColumn: {
      flexDirection: "column",
      alignItems: "stretch",
    },
    panel: {
      flex: 1,
      borderWidth: 1,
      borderRadius: theme.radius.md,
      paddingVertical: theme.spacing.sm,
      paddingHorizontal: theme.spacing.md,
      minWidth: 0,
    },
    arrow: {
      fontSize: 18,
      fontWeight: "700",
      alignSelf: "center",
      paddingHorizontal: 2,
    },
    emptyApplied: {
      borderWidth: 1,
      borderColor: theme.colors.border,
      borderRadius: theme.radius.lg,
      padding: theme.spacing.lg,
      backgroundColor: theme.colors.surface2,
      alignItems: "flex-start",
    },
    skippedPanel: {
      borderWidth: 1.5,
      borderRadius: theme.radius.md,
      padding: theme.spacing.md,
      borderColor: theme.colors.warning,
      backgroundColor: theme.colors.warning + "1F",
    },
    skippedTitle: {
      color: theme.colors.warning,
      fontSize: 14,
    },
    skippedSubtitle: {
      color: theme.colors.text,
      marginTop: 4,
    },
    skippedList: {
      marginTop: theme.spacing.sm,
      gap: theme.spacing.xs,
    },
    skippedRow: {
      flexDirection: "row",
      alignItems: "flex-start",
      gap: theme.spacing.sm,
      flexWrap: "wrap",
    },
    skippedTarget: {
      color: theme.colors.text,
      fontSize: 12,
    },
    skippedReason: {
      color: theme.colors.text,
      flex: 1,
      minWidth: 0,
    },
  });
