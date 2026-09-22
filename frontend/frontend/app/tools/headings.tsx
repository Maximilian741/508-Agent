/**
 * Heading-outline checker — standalone free tool. Paste HTML or Markdown, see
 * the document outline and the common heading problems screen-reader users hit
 * (no H1, skipped levels, empty headings). Pure frontend; reuses
 * src/domain/headingOutline.ts. Lead-gen, like the other /tools pages.
 */
import { useMemo, useState } from "react";
import { StyleSheet, Text, TextInput, View } from "react-native";

import { analyzeHeadingOutline, OutlineSeverity } from "../../src/domain/headingOutline";
import { Card } from "../../src/ui/components/Card";
import { Hero } from "../../src/ui/components/Hero";
import { Screen } from "../../src/ui/components/Screen";
import { useTheme } from "../../src/ui/useTheme";

const SAMPLE = [
  "<h1>Annual Accessibility Report</h1>",
  "<h2>Summary</h2>",
  "<h4>Key metrics</h4>",
  "<h2>Methodology</h2>",
  "<h3></h3>",
  "<h2>Next steps</h2>",
].join("\n");

function _sevColor(sev: OutlineSeverity, theme: ReturnType<typeof useTheme>): string {
  if (sev === "error") return theme.colors.danger;
  if (sev === "warning") return theme.colors.warning;
  return theme.colors.textMuted;
}

export default function HeadingOutlineChecker() {
  const theme = useTheme();
  const [text, setText] = useState(SAMPLE);
  const result = useMemo(() => analyzeHeadingOutline(text), [text]);

  return (
    <Screen scroll title="Heading structure checker">
      <Hero
        eyebrow="HEADINGS"
        title="Heading structure checker"
        subtitle="Paste a page's HTML or Markdown to see its heading outline and the structural problems screen-reader users hit: a missing H1, skipped levels, or empty headings (WCAG 1.3.1 and 2.4.6). Nothing leaves your browser."
      />

      <Card>
        <Text style={[theme.typography.eyebrow, { color: theme.colors.textMuted, marginBottom: 6 }]}>
          YOUR HTML OR MARKDOWN
        </Text>
        <TextInput
          value={text}
          onChangeText={setText}
          multiline
          accessibilityLabel="HTML or Markdown to analyze"
          placeholder="Paste HTML (<h1>…</h1>) or Markdown (# Heading)…"
          placeholderTextColor={theme.colors.textMuted}
          style={[
            styles.input,
            { color: theme.colors.text, borderColor: theme.colors.border, backgroundColor: theme.colors.surface, borderRadius: theme.radius.xs },
          ]}
        />
      </Card>

      <Card>
        <View style={styles.verdictRow}>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>
            {result.headings.length} heading{result.headings.length === 1 ? "" : "s"}
          </Text>
          {result.headings.length > 0 ? (
            <Text
              style={[
                styles.badge,
                {
                  color: result.ok ? theme.colors.success : theme.colors.warning,
                  borderColor: result.ok ? theme.colors.success : theme.colors.warning,
                  borderRadius: theme.radius.sm,
                },
              ]}
            >
              {result.ok ? "Well structured" : "Needs attention"}
            </Text>
          ) : null}
        </View>

        {result.headings.length > 0 ? (
          <View style={{ marginTop: 10 }}>
            {result.headings.map((h, i) => (
              <View key={i} style={[styles.outlineRow, { paddingLeft: (h.level - 1) * 18 }]}>
                <Text style={[styles.tag, { color: theme.colors.accent }]}>H{h.level}</Text>
                <Text
                  style={[
                    theme.typography.body,
                    { color: h.text ? theme.colors.text : theme.colors.danger, flex: 1 },
                  ]}
                  numberOfLines={1}
                >
                  {h.text || "(empty heading)"}
                </Text>
              </View>
            ))}
          </View>
        ) : null}
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Findings</Text>
        {result.issues.length === 0 ? (
          <Text style={[theme.typography.body, { color: theme.colors.success, marginTop: 8 }]}>
            No structural problems found — the outline is clean.
          </Text>
        ) : (
          <View style={{ marginTop: 8 }}>
            {result.issues.map((issue, i) => (
              <View key={i} style={styles.issueRow}>
                <View style={[styles.dot, { backgroundColor: _sevColor(issue.severity, theme) }]} />
                <Text style={[theme.typography.body, { color: theme.colors.text, flex: 1, fontSize: 14 }]}>
                  {issue.message}
                </Text>
              </View>
            ))}
          </View>
        )}
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 14 }]}>
          Want this checked and fixed automatically across whole documents (PDF, Word, PowerPoint, HTML)? Run an audit — heading levels are normalized for you.
        </Text>
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  input: { borderWidth: 1, padding: 12, minHeight: 160, fontSize: 14, lineHeight: 21, textAlignVertical: "top" },
  verdictRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  badge: { borderWidth: 1.5, paddingVertical: 4, paddingHorizontal: 10, fontSize: 12, fontWeight: "800", overflow: "hidden" },
  outlineRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 3 },
  tag: { fontSize: 12, fontWeight: "800", width: 28 },
  issueRow: { flexDirection: "row", alignItems: "flex-start", gap: 10, paddingVertical: 5 },
  dot: { width: 8, height: 8, borderRadius: 4, marginTop: 6 },
});
