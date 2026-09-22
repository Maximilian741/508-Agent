/**
 * Readability checker — standalone tool. Paste text, see how hard it is to read
 * (Flesch Reading Ease + grade level), which supports WCAG 2.1 §3.1.5 plain
 * language. Pure frontend; reuses src/domain/readability.ts.
 */
import { useMemo, useState } from "react";
import { StyleSheet, Text, TextInput, View } from "react-native";

import { analyzeReadability } from "../../src/domain/readability";
import { Card } from "../../src/ui/components/Card";
import { Hero } from "../../src/ui/components/Hero";
import { Screen } from "../../src/ui/components/Screen";
import { useTheme } from "../../src/ui/useTheme";

const SAMPLE =
  "Our organization is committed to ensuring that all of the digital documents " +
  "we produce are accessible to every member of the public, including those who " +
  "rely on assistive technologies such as screen readers. We endeavor to remediate " +
  "deficiencies expeditiously upon identification.";

function _scoreColor(score: number, theme: ReturnType<typeof useTheme>): string {
  if (score >= 60) return theme.colors.success;
  if (score >= 30) return theme.colors.warning;
  return theme.colors.danger;
}

export default function ReadabilityChecker() {
  const theme = useTheme();
  const [text, setText] = useState(SAMPLE);
  const result = useMemo(() => analyzeReadability(text), [text]);

  return (
    <Screen scroll title="Readability checker">
      <Hero
        eyebrow="READABILITY"
        title="Readability checker"
        subtitle="Paste any text to see how easy it is to read: Flesch Reading Ease and grade level. Plain language is an accessibility requirement (WCAG 2.1 §3.1.5); aim for a score of 60 or higher for a general audience."
      />

      <Card>
        <Text style={[theme.typography.eyebrow, { color: theme.colors.textMuted, marginBottom: 6 }]}>
          YOUR TEXT
        </Text>
        <TextInput
          value={text}
          onChangeText={setText}
          multiline
          accessibilityLabel="Text to analyze"
          placeholder="Paste a paragraph, a page, or a whole document…"
          placeholderTextColor={theme.colors.textMuted}
          style={[
            styles.input,
            { color: theme.colors.text, borderColor: theme.colors.border, backgroundColor: theme.colors.surface, borderRadius: theme.radius.xs },
          ]}
        />
      </Card>

      {result ? (
        <>
          <Card>
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Reading ease</Text>
            <View style={styles.scoreRow}>
              <Text style={[styles.score, { color: _scoreColor(result.fleschReadingEase, theme) }]}>
                {result.fleschReadingEase}
              </Text>
              <View style={{ flex: 1 }}>
                <Text style={[theme.typography.body, { color: theme.colors.text, fontSize: 15, fontWeight: "700" }]}>
                  {result.band}
                </Text>
                <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 2 }]}>
                  Flesch Reading Ease (0–100, higher is easier) · approx. grade {result.fleschKincaidGrade}
                </Text>
              </View>
            </View>
          </Card>

          <Card>
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Details</Text>
            <View style={styles.statGrid}>
              <Stat label="Words" value={String(result.words)} theme={theme} />
              <Stat label="Sentences" value={String(result.sentences)} theme={theme} />
              <Stat label="Words / sentence" value={String(result.avgWordsPerSentence)} theme={theme} />
              <Stat label="Syllables / word" value={String(result.avgSyllablesPerWord)} theme={theme} />
              <Stat label="Complex words (3+ syll.)" value={String(result.complexWords)} theme={theme} />
              <Stat label="Grade level" value={String(result.fleschKincaidGrade)} theme={theme} />
            </View>

            {result.longSentences.length > 0 ? (
              <View style={{ marginTop: 14 }}>
                <Text style={[theme.typography.eyebrow, { color: theme.colors.textMuted }]}>
                  LONG SENTENCES TO SPLIT (OVER 25 WORDS)
                </Text>
                {result.longSentences.map((s) => (
                  <Text key={s.index} style={[theme.typography.body, { color: theme.colors.text, fontSize: 13, marginTop: 4 }]}>
                    • Sentence {s.index}: {s.words} words
                  </Text>
                ))}
                <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 8 }]}>
                  Shorter sentences and simpler words raise the score the fastest.
                </Text>
              </View>
            ) : (
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 12 }]}>
                No sentences over 25 words. Nice and tight.
              </Text>
            )}
          </Card>
        </>
      ) : (
        <Card>
          <Text style={{ color: theme.colors.textMuted }}>Enter some text to see its readability.</Text>
        </Card>
      )}
    </Screen>
  );
}

function Stat({
  label,
  value,
  theme,
}: {
  label: string;
  value: string;
  theme: ReturnType<typeof useTheme>;
}) {
  return (
    <View style={[styles.stat, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2, borderRadius: theme.radius.none }]}>
      <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 22 }]}>{value}</Text>
      <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 2 }]}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  input: { borderWidth: 1, padding: 12, minHeight: 160, fontSize: 15, lineHeight: 22, textAlignVertical: "top" },
  scoreRow: { flexDirection: "row", alignItems: "center", gap: 18, marginTop: 8 },
  score: { fontSize: 56, fontWeight: "800" },
  statGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10, marginTop: 12 },
  stat: { minWidth: 140, flexGrow: 1, borderWidth: 1, padding: 12 },
});
