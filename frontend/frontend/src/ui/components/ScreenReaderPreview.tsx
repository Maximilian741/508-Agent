/**
 * ScreenReaderPreview — makes an accessibility error visible.
 *
 * The whole problem with document accessibility is that the damage is
 * INVISIBLE to the person who published the file: the page looks fine. This
 * component shows the same content twice — as a sighted reader sees it, and as
 * a screen reader actually announces it — so the gap between the two is
 * obvious at a glance.
 *
 * Every example maps to a real check the engine performs (the rule id is shown),
 * so this is a demonstration of the product, not marketing fiction.
 *
 * Own-accessibility notes (this is an accessibility product — our UI has to be
 * exemplary): the visual mock is marked aria-hidden because it is decorative
 * duplication of the text beside it, the announcement is quoted as plain text
 * rather than mimicked with ARIA, and every pair is a labelled group so the
 * comparison still makes sense when read linearly.
 */
import { StyleSheet, Text, View } from "react-native";

import { useTheme } from "../useTheme";

export interface SrExample {
  /** Engine rule this demonstrates, e.g. "MISSING_ALT_TEXT". */
  ruleId: string;
  /** Plain-language name of the problem. */
  title: string;
  /** WCAG criterion, e.g. "1.1.1". */
  criterion: string;
  severity: "error" | "warning";
  /** How the visual mock should render. */
  visual: "image" | "link" | "heading" | "table" | "contrast";
  /** The words on the page. */
  seen: string;
  /** What assistive tech actually announces. */
  heard: string;
  /** One line on why that breaks the user. */
  impact: string;
}

export const SR_EXAMPLES: SrExample[] = [
  {
    ruleId: "MISSING_ALT_TEXT",
    title: "Image with no alt text",
    criterion: "WCAG 1.1.1",
    severity: "error",
    visual: "image",
    seen: "Q3 revenue by region",
    heard: "image",
    impact: "The entire chart is skipped. Nothing about it is conveyed.",
  },
  {
    ruleId: "LINK_TEXT_NON_DESCRIPTIVE",
    title: "Generic link text",
    criterion: "WCAG 2.4.4",
    severity: "warning",
    visual: "link",
    seen: "To apply for a permit, click here.",
    heard: "link, click here",
    impact: "Many users browse a list of every link. This one says nothing.",
  },
  {
    ruleId: "TEXT_STYLED_AS_HEADING",
    title: "Text that only looks like a heading",
    criterion: "WCAG 1.3.1",
    severity: "warning",
    visual: "heading",
    seen: "Annual Report 2025",
    heard: "Annual Report 2025",
    impact: "Big and bold, but not a real heading — so it never appears in the outline users navigate by.",
  },
  {
    ruleId: "TABLE_MISSING_HEADERS",
    title: "Table with no header cells",
    criterion: "WCAG 1.3.1",
    severity: "error",
    visual: "table",
    seen: "Region · Q1 · Q2   /   North · 120 · 140",
    heard: "120. 140. 90. 110.",
    impact: "Numbers with no column names. The reader can't tell what any value means.",
  },
  {
    ruleId: "LOW_CONTRAST_TEXT",
    title: "Text too faint to read",
    criterion: "WCAG 1.4.3",
    severity: "warning",
    visual: "contrast",
    seen: "Deadline: April 24, 2026",
    heard: "Deadline: April 24, 2026",
    impact: "Announced fine, but invisible to many readers with low vision.",
  },
];

function VisualMock({ example }: { example: SrExample }) {
  const theme = useTheme();
  const faint = theme.isDark ? "#3A4354" : "#C3CBD6";

  if (example.visual === "image") {
    return (
      <View style={{ gap: 6 }}>
        <View
          style={[
            styles.imageMock,
            { backgroundColor: theme.colors.surface3, borderColor: theme.colors.border },
          ]}
        >
          {[38, 56, 30, 68].map((h, i) => (
            <View
              key={i}
              style={{
                width: 12,
                height: h,
                borderRadius: 2,
                backgroundColor: theme.colors.accent,
                opacity: 0.35 + i * 0.15,
              }}
            />
          ))}
        </View>
        <Text style={[styles.mockCaption, { color: theme.colors.textMuted }]}>{example.seen}</Text>
      </View>
    );
  }

  if (example.visual === "link") {
    return (
      <Text style={[styles.mockBody, { color: theme.colors.text }]}>
        To apply for a permit,{" "}
        <Text style={{ color: theme.colors.accent, textDecorationLine: "underline" }}>click here</Text>.
      </Text>
    );
  }

  if (example.visual === "heading") {
    return (
      <Text style={[styles.mockFakeHeading, { color: theme.colors.text }]}>{example.seen}</Text>
    );
  }

  if (example.visual === "table") {
    return (
      <View style={{ gap: 3 }}>
        {[
          ["Region", "Q1", "Q2"],
          ["North", "120", "140"],
          ["South", "90", "110"],
        ].map((row, r) => (
          <View key={r} style={{ flexDirection: "row", gap: 3 }}>
            {row.map((cell, c) => (
              <View
                key={c}
                style={[
                  styles.cell,
                  {
                    borderColor: theme.colors.border,
                    backgroundColor: r === 0 ? theme.colors.surface3 : "transparent",
                  },
                ]}
              >
                <Text
                  style={[
                    styles.cellText,
                    { color: theme.colors.text, fontWeight: r === 0 ? "700" : "400" },
                  ]}
                >
                  {cell}
                </Text>
              </View>
            ))}
          </View>
        ))}
      </View>
    );
  }

  // contrast
  return <Text style={[styles.mockBody, { color: faint }]}>{example.seen}</Text>;
}

export function ScreenReaderPreview({ examples = SR_EXAMPLES }: { examples?: SrExample[] }) {
  const theme = useTheme();

  return (
    <View style={{ gap: 14 }}>
      <View style={styles.legend}>
        <View style={styles.legendItem}>
          <View style={[styles.legendSwatch, { backgroundColor: theme.colors.surface3, borderColor: theme.colors.border }]} />
          <Text style={[styles.legendText, { color: theme.colors.textMuted }]}>What you see</Text>
        </View>
        <View style={styles.legendItem}>
          <View style={[styles.legendSwatch, { backgroundColor: "transparent", borderColor: theme.colors.accent }]} />
          <Text style={[styles.legendText, { color: theme.colors.textMuted }]}>
            What a screen reader announces
          </Text>
        </View>
      </View>

      {examples.map((ex) => {
        const sevColor = ex.severity === "error" ? theme.colors.danger : theme.colors.warning;
        return (
          <View
            key={ex.ruleId}
            accessible={false}
            style={[styles.row, { borderColor: theme.colors.border }]}
          >
            <View style={styles.rowHead}>
              <View style={[styles.sevDot, { backgroundColor: sevColor }]} />
              <Text style={[styles.rowTitle, { color: theme.colors.text }]}>{ex.title}</Text>
              <Text style={[styles.criterion, { color: theme.colors.textMuted, borderColor: theme.colors.border }]}>
                {ex.criterion}
              </Text>
            </View>

            <View style={styles.panes}>
              {/* Decorative duplicate of the text described beside it. */}
              <View
                aria-hidden
                importantForAccessibility="no-hide-descendants"
                style={[
                  styles.pane,
                  { backgroundColor: theme.colors.surface2, borderColor: theme.colors.border },
                ]}
              >
                <Text style={[styles.paneLabel, { color: theme.colors.textMuted }]}>ON THE PAGE</Text>
                <VisualMock example={ex} />
              </View>

              <View
                style={[
                  styles.pane,
                  styles.paneHeard,
                  { borderColor: theme.colors.accent, backgroundColor: "transparent" },
                ]}
              >
                <Text style={[styles.paneLabel, { color: theme.colors.accent }]}>ANNOUNCED AS</Text>
                <Text style={[styles.heard, { color: theme.colors.text }]}>“{ex.heard}”</Text>
                <Text style={[styles.impact, { color: theme.colors.textMuted }]}>{ex.impact}</Text>
              </View>
            </View>
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  legend: { flexDirection: "row", gap: 18, flexWrap: "wrap" },
  legendItem: { flexDirection: "row", alignItems: "center", gap: 6 },
  legendSwatch: { width: 12, height: 12, borderWidth: 1, borderRadius: 2 },
  legendText: { fontSize: 12, fontWeight: "600" },

  row: { borderTopWidth: 1, paddingTop: 14, gap: 10 },
  rowHead: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
  sevDot: { width: 9, height: 9, borderRadius: 5 },
  rowTitle: { fontSize: 15, fontWeight: "700", flexShrink: 1 },
  criterion: {
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 0.4,
    borderWidth: 1,
    borderRadius: 3,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },

  panes: { flexDirection: "row", flexWrap: "wrap", gap: 10 },
  // flexBasis + grow + minWidth = side-by-side when there is room, stacked when
  // there isn't — no width measurement, so it can't get stuck in one mode.
  pane: {
    flexGrow: 1,
    flexShrink: 1,
    flexBasis: 280,
    minWidth: 240,
    borderWidth: 1,
    borderRadius: 4,
    padding: 12,
    gap: 8,
    minHeight: 108,
    justifyContent: "center",
  },
  paneHeard: { borderStyle: "dashed" },
  paneLabel: { fontSize: 10, fontWeight: "800", letterSpacing: 0.8 },

  imageMock: {
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 6,
    height: 76,
    borderWidth: 1,
    borderRadius: 3,
    padding: 8,
  },
  mockCaption: { fontSize: 11 },
  mockBody: { fontSize: 14, lineHeight: 20 },
  mockFakeHeading: { fontSize: 20, fontWeight: "800" },
  cell: { borderWidth: 1, paddingHorizontal: 8, paddingVertical: 3, minWidth: 52 },
  cellText: { fontSize: 12 },

  heard: {
    fontSize: 15,
    fontWeight: "700",
    fontStyle: "italic",
  },
  impact: { fontSize: 12, lineHeight: 17 },
});
