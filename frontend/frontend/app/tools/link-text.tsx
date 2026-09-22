/**
 * Link Text Checker — standalone tool. Type a link's visible text (and
 * optionally its destination) to see whether it's descriptive enough for screen
 * readers (WCAG 2.4.4), and get a suggested better label derived from the URL.
 * Pure frontend; reuses src/domain/linkText.ts (mirrors the backend analyzer).
 */
import { useMemo, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";

import { checkLinkText, suggestLinkText } from "../../src/domain/linkText";
import { Button } from "../../src/ui/components/Button";
import { Card } from "../../src/ui/components/Card";
import { Hero } from "../../src/ui/components/Hero";
import { Screen } from "../../src/ui/components/Screen";
import { useTheme } from "../../src/ui/useTheme";
import { useToast } from "../../src/ui/toast";

const EXAMPLES: { text: string; url: string }[] = [
  { text: "click here", url: "https://example.gov/reports/2025-annual-accessibility-report.pdf" },
  { text: "read more", url: "https://news.example.com/2025/06/transit-funding-approved" },
  { text: "https://example.com/very/long/link", url: "https://example.com/very/long/link" },
  { text: "Download the 2025 budget (PDF)", url: "https://example.gov/budget-2025.pdf" },
];

export default function LinkTextChecker() {
  const theme = useTheme();
  const router = useRouter();
  const toast = useToast();
  const [text, setText] = useState("click here");
  const [url, setUrl] = useState("https://example.gov/reports/2025-annual-accessibility-report.pdf");

  const verdict = useMemo(() => (text.trim() ? checkLinkText(text) : null), [text]);
  const suggestion = useMemo(() => (url.trim() ? suggestLinkText(url) : null), [url]);

  const copy = (value: string) => {
    if (Platform.OS !== "web") return;
    try {
      (navigator as any)?.clipboard?.writeText(value);
      toast.success(`Copied “${value}”`);
    } catch {}
  };

  return (
    <Screen scroll title="Link text checker">
      <Hero
        eyebrow="LINK TEXT"
        title="Link text checker"
        subtitle="Screen-reader users often pull every link onto one list to scan a page. “Click here” and bare URLs are useless there. Paste a link's text to see if it's descriptive (WCAG 2.4.4) — and get a better label suggested from its destination."
      />

      <Card>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginBottom: 6 }]}>
          LINK TEXT (WHAT THE USER SEES)
        </Text>
        <TextInput
          value={text}
          onChangeText={setText}
          accessibilityLabel="Link text"
          placeholder="e.g. Read the full quarterly report"
          placeholderTextColor={theme.colors.textMuted}
          style={[styles.input, { color: theme.colors.text, borderColor: theme.colors.border, backgroundColor: theme.colors.surface }]}
        />
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 14, marginBottom: 6 }]}>
          DESTINATION URL (OPTIONAL — USED TO SUGGEST A LABEL)
        </Text>
        <TextInput
          value={url}
          onChangeText={setUrl}
          accessibilityLabel="Destination URL"
          autoCapitalize="none"
          placeholder="https://example.com/reports/q3"
          placeholderTextColor={theme.colors.textMuted}
          style={[styles.input, { color: theme.colors.text, borderColor: theme.colors.border, backgroundColor: theme.colors.surface }]}
        />
      </Card>

      {verdict ? (
        <Card style={{ borderColor: verdict.ok ? theme.colors.success : theme.colors.danger, borderWidth: 2 }}>
          <View style={styles.verdictRow}>
            <View style={[styles.badge, { backgroundColor: (verdict.ok ? theme.colors.success : theme.colors.danger) + "22" }]}>
              <Text style={{ color: verdict.ok ? theme.colors.success : theme.colors.danger, fontWeight: "800", fontSize: 13 }}>
                {verdict.ok ? "PASSES" : "NOT DESCRIPTIVE"}
              </Text>
            </View>
          </View>
          <Text style={[theme.typography.body, { color: theme.colors.text, marginTop: 10, lineHeight: 22 }]}>
            {verdict.reason}
          </Text>

          {!verdict.ok && suggestion ? (
            <View style={[styles.suggestBox, { borderColor: theme.colors.accent, backgroundColor: theme.colors.surface2 }]}>
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginBottom: 6 }]}>
                SUGGESTED LABEL (FROM THE URL — EDIT TO TASTE)
              </Text>
              <View style={styles.suggestRow}>
                <Text style={[theme.typography.body, { color: theme.colors.accent, fontWeight: "800", flex: 1, fontSize: 16 }]}>
                  {suggestion}
                </Text>
                {Platform.OS === "web" ? (
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`Copy suggested link text ${suggestion}`}
                    onPress={() => copy(suggestion)}
                    style={[styles.copyBtn, { borderColor: theme.colors.border }]}
                  >
                    <Text style={{ color: theme.colors.accent, fontWeight: "700", fontSize: 12 }}>Copy</Text>
                  </Pressable>
                ) : null}
              </View>
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 8 }]}>
                A suggestion derived from the link's address — always tweak it so it reads naturally in your sentence.
              </Text>
            </View>
          ) : null}
        </Card>
      ) : (
        <Card>
          <Text style={{ color: theme.colors.textMuted }}>Type some link text above to check it.</Text>
        </Card>
      )}

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Try an example</Text>
        <View style={styles.exRow}>
          {EXAMPLES.map((ex) => (
            <Pressable
              key={ex.text}
              accessibilityRole="button"
              accessibilityLabel={`Load example: ${ex.text}`}
              onPress={() => {
                setText(ex.text);
                setUrl(ex.url);
              }}
              style={({ hovered }: any) => [
                styles.exChip,
                { borderColor: theme.colors.border },
                hovered ? { opacity: 0.7 } : null,
              ]}
            >
              <Text style={{ color: theme.colors.text, fontSize: 13 }} numberOfLines={1}>
                {ex.text}
              </Text>
            </Pressable>
          ))}
        </View>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Got a whole document?</Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 6, lineHeight: 22 }]}>
          508 Agent finds every vague link in your PDF, Word, PowerPoint, or HTML file and rewrites it
          automatically — along with alt text, headings, tables, language and contrast. Your first audits are free.
        </Text>
        <View style={{ marginTop: 14 }}>
          <Button title="Audit a document free" href="/audit" />
        </View>
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  input: { borderWidth: 1, borderRadius: 10, padding: 12, fontSize: 15, ...(Platform.OS === "web" ? ({ outlineStyle: "none" } as any) : null) },
  verdictRow: { flexDirection: "row", alignItems: "center" },
  badge: { borderRadius: 999, paddingHorizontal: 12, paddingVertical: 5 },
  suggestBox: { borderWidth: 1, borderLeftWidth: 3, borderRadius: 10, padding: 12, marginTop: 16 },
  suggestRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  copyBtn: { borderWidth: 1, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 5 },
  exRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 12 },
  exChip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 8, maxWidth: 260 },
});
