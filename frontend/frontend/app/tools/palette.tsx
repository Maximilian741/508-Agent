/**
 * Palette matrix — standalone tool. Paste your brand colours and instantly see
 * which foreground/background pairings pass WCAG 2.1 AA contrast. Designers
 * constantly need "which of my colours can I safely combine?"; this answers it
 * at a glance instead of checking pairs one at a time.
 *
 * Pure frontend — reuses the verified contrast math in src/domain/contrast.ts.
 */
import { useMemo, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { contrastRatio, parseHex } from "../../src/domain/contrast";
import { Card } from "../../src/ui/components/Card";
import { Hero } from "../../src/ui/components/Hero";
import { Screen } from "../../src/ui/components/Screen";
import { useTheme } from "../../src/ui/useTheme";

const MAX_COLORS = 8;
const DEFAULT_COLORS = ["#0F172A", "#FFFFFF", "#2D5BFF", "#F59E0B", "#E2E8F0"];

function _norm(input: string): string | null {
  const rgb = parseHex(input);
  if (!rgb) return null;
  return (
    "#" +
    rgb.map((x) => x.toString(16).padStart(2, "0")).join("").toUpperCase()
  );
}

export default function PaletteMatrix() {
  const theme = useTheme();
  const [colors, setColors] = useState<string[]>(DEFAULT_COLORS);
  const [draft, setDraft] = useState("");

  const valid = useMemo(() => {
    const out: string[] = [];
    for (const c of colors) {
      const n = _norm(c);
      if (n && !out.includes(n)) out.push(n);
    }
    return out;
  }, [colors]);

  const addColor = () => {
    const n = _norm(draft);
    if (!n) return;
    if (colors.length >= MAX_COLORS) return;
    if (valid.includes(n)) {
      setDraft("");
      return;
    }
    setColors((prev) => [...prev, n]);
    setDraft("");
  };

  const removeColor = (hex: string) => setColors((prev) => prev.filter((c) => _norm(c) !== hex));

  return (
    <Screen scroll title="Palette matrix">
      <Hero
        eyebrow="PALETTE"
        title="Accessible palette matrix"
        subtitle="Add your brand colours and see every text-on-background pairing at once: which combinations meet WCAG 2.1 AA, and which to avoid. No more checking pairs one by one."
      />

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Your colours</Text>
        <View style={styles.chips}>
          {valid.map((hex) => (
            <View key={hex} style={[styles.chip, { borderColor: theme.colors.border, borderRadius: theme.radius.pill }]}>
              <View style={[styles.chipSwatch, { backgroundColor: hex, borderColor: theme.colors.border }]} />
              <Text style={{ color: theme.colors.text, fontSize: 12, fontFamily: "monospace" }}>{hex}</Text>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`Remove ${hex}`}
                onPress={() => removeColor(hex)}
                hitSlop={6}
              >
                <Text style={{ color: theme.colors.textMuted, fontSize: 16, fontWeight: "700" }}>×</Text>
              </Pressable>
            </View>
          ))}
        </View>

        {colors.length < MAX_COLORS ? (
          <View style={styles.addRow}>
            <TextInput
              value={draft}
              onChangeText={setDraft}
              placeholder="#1A2B3C"
              placeholderTextColor={theme.colors.textMuted}
              autoCapitalize="characters"
              autoCorrect={false}
              accessibilityLabel="Add a hex colour"
              onSubmitEditing={addColor}
              style={[styles.input, { color: theme.colors.text, borderColor: theme.colors.border, backgroundColor: theme.colors.surface, borderRadius: theme.radius.xs }]}
            />
            {Platform.OS === "web" ? (
              // @ts-ignore - native HTML color picker
              <input
                type="color"
                onChange={(e: any) => {
                  const n = _norm(e.target.value);
                  if (n && !valid.includes(n) && colors.length < MAX_COLORS) setColors((p) => [...p, n]);
                }}
                style={{ width: 38, height: 38, padding: 0, border: "none", background: "transparent" }}
              />
            ) : null}
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Add colour"
              onPress={addColor}
              style={[styles.addBtn, { backgroundColor: theme.colors.accent, borderRadius: theme.radius.sm }]}
            >
              <Text style={{ color: "#fff", fontWeight: "700" }}>Add</Text>
            </Pressable>
          </View>
        ) : (
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 8 }]}>
            Up to {MAX_COLORS} colours. Remove one to add another.
          </Text>
        )}
      </Card>

      {valid.length >= 2 ? (
        <Card>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>AA pairings (4.5:1)</Text>
          <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 4, marginBottom: 12 }}>
            Each cell is its row colour as text on its column colour as background. Green = passes AA for normal text.
          </Text>
          <View style={styles.matrixScroll}>
            {/* header row */}
            <View style={styles.matrixRow}>
              <View style={styles.corner} />
              {valid.map((bg) => (
                <View key={`h-${bg}`} style={[styles.headCell, { backgroundColor: bg, borderColor: theme.colors.border, borderRadius: theme.radius.none }]} />
              ))}
            </View>
            {valid.map((fg) => (
              <View key={`r-${fg}`} style={styles.matrixRow}>
                <View style={[styles.headCell, { backgroundColor: fg, borderColor: theme.colors.border, borderRadius: theme.radius.none }]} />
                {valid.map((bg) => {
                  const fgRgb = parseHex(fg)!;
                  const bgRgb = parseHex(bg)!;
                  const ratio = contrastRatio(fgRgb, bgRgb);
                  const pass = ratio >= 4.5;
                  const same = fg === bg;
                  return (
                    <View
                      key={`${fg}-${bg}`}
                      style={[styles.cell, { backgroundColor: bg, borderColor: theme.colors.border, borderRadius: theme.radius.none }]}
                    >
                      <Text style={{ color: fg, fontSize: 13, fontWeight: "700" }}>Aa</Text>
                      <Text style={{ color: fg, fontSize: 9 }}>{same ? "—" : ratio.toFixed(1)}</Text>
                      {!same ? (
                        <View
                          style={[
                            styles.dot,
                            { backgroundColor: pass ? theme.colors.success : theme.colors.danger },
                          ]}
                        />
                      ) : null}
                    </View>
                  );
                })}
              </View>
            ))}
          </View>
        </Card>
      ) : (
        <Card>
          <Text style={{ color: theme.colors.textMuted }}>Add at least two valid colours to see the matrix.</Text>
        </Card>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 12 },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderWidth: 1,
    paddingLeft: 6,
    paddingRight: 10,
    paddingVertical: 5,
  },
  chipSwatch: { width: 20, height: 20, borderRadius: 10, borderWidth: 1 },
  addRow: { flexDirection: "row", gap: 8, marginTop: 14, alignItems: "center" },
  input: { flex: 1, borderWidth: 1, padding: 10, minWidth: 120 },
  addBtn: { paddingHorizontal: 16, paddingVertical: 10 },
  matrixScroll: { gap: 4 },
  matrixRow: { flexDirection: "row", gap: 4 },
  corner: { width: 40, height: 40 },
  headCell: { width: 40, height: 40, borderWidth: 1 },
  cell: {
    width: 40,
    height: 40,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  dot: { width: 8, height: 8, borderRadius: 4, marginTop: 2 },
});
