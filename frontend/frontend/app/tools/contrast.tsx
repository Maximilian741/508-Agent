/**
 * Contrast checker — standalone WCAG color-contrast tool.
 *
 * Two color inputs (foreground and background), live preview, ratio number,
 * pass/fail badges for every WCAG criterion (AA/AAA × normal/large/UI).  Hex
 * input accepts #RGB, #RRGGBB, or RRGGBB.
 *
 * This isn't part of the document audit flow — remediators reach for it
 * constantly when reviewing visual design, and most accessibility apps don't
 * bundle one.
 */

import { useEffect, useMemo, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { evaluate, parseHex, suggestPassing } from "../../src/domain/contrast";
import { Card } from "../../src/ui/components/Card";
import { Chip } from "../../src/ui/components/Chip";
import { Hero } from "../../src/ui/components/Hero";
import { Screen } from "../../src/ui/components/Screen";
import { useTheme } from "../../src/ui/useTheme";

/**
 * Native eyedropper. Returns true if the current browser supports
 * `window.EyeDropper` — Chromium 95+ (Chrome, Edge, Brave, Opera). Safari
 * and Firefox do not support it as of writing.
 */
function _hasEyeDropper(): boolean {
  if (Platform.OS !== "web" || typeof window === "undefined") return false;
  return typeof (window as any).EyeDropper === "function";
}

/**
 * Open the native screen-color picker. Returns the picked hex (e.g.
 * "#A1B2C3") or null if the user cancelled or the API errored.
 */
async function _pickColorWithEyeDropper(): Promise<string | null> {
  try {
    const Ctor = (window as any).EyeDropper;
    if (typeof Ctor !== "function") return null;
    const result = await new Ctor().open();
    const hex = (result && (result.sRGBHex || result.srgbHex)) as string | undefined;
    return hex && /^#[0-9A-Fa-f]{6}$/.test(hex) ? hex.toUpperCase() : null;
  } catch (e) {
    // User pressed Escape, or EyeDropper rejected. Treat as cancel.
    return null;
  }
}

const PRESETS: Array<[string, string, string]> = [
  ["Light text on accent", "#FFFFFF", "#2D5BFF"],
  ["Dark UI", "#F8FAFC", "#0B0F1A"],
  ["Warning chip", "#000000", "#F59E0B"],
  ["Risky combo", "#A0AEC0", "#E2E8F0"],
];

export default function ContrastChecker() {
  const theme = useTheme();
  const [fg, setFg] = useState("#0F172A");
  const [bg, setBg] = useState("#FFFFFF");

  const evaluation = useMemo(() => evaluate(fg, bg), [fg, bg]);
  const suggestions = useMemo(
    () =>
      evaluation.ok && !evaluation.result.aaNormal ? suggestPassing(fg, bg, 4.5) : null,
    [evaluation, fg, bg],
  );

  return (
    <Screen scroll title="Contrast checker">
      <Hero
        shader="aurora"
        eyebrow="CONTRAST"
        title="Contrast checker"
        subtitle="Paste any two hex colors (foreground over background) and instantly see whether they meet WCAG 2.1 AA/AAA contrast minimums. Useful for design review and when remediating PDFs with hard-to-read body text."
      />

      <Card>
        <View style={styles.inputRow}>
          <ColorInput label="Foreground" value={fg} onChange={setFg} />
          <ColorInput label="Background" value={bg} onChange={setBg} />
        </View>

        <View style={styles.presetRow}>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            Try a preset:
          </Text>
          {PRESETS.map(([label, f, b]) => (
            <Chip
              key={label}
              label={label}
              tone="default"
              accessibilityLabel={`Use preset ${label}`}
              onPress={() => {
                setFg(f);
                setBg(b);
              }}
            />
          ))}
        </View>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Live preview</Text>
        <View style={[styles.preview, { backgroundColor: _safe(bg) }]}>
          <Text style={[styles.previewLarge, { color: _safe(fg) }]}>
            18.66pt large text — Aa
          </Text>
          <Text style={[styles.previewBody, { color: _safe(fg) }]}>
            Normal body text. The quick brown fox jumps over the lazy dog.
          </Text>
          <Text style={[styles.previewSmall, { color: _safe(fg) }]}>
            Small print 12pt — fine details, footnotes, footers, legal disclaimers.
          </Text>
        </View>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Compliance</Text>
        {evaluation.ok ? (
          <>
            <View style={styles.ratioRow}>
              <Text style={[styles.ratio, { color: theme.colors.text }]}>
                {evaluation.result.ratio.toFixed(2)}
                <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}> : 1</Text>
              </Text>
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                Higher is better. WCAG AA needs at least 4.5:1 for normal text.
              </Text>
            </View>

            <View style={styles.passGrid}>
              <PassPill label="AA · normal text" minRatio={4.5} pass={evaluation.result.aaNormal} />
              <PassPill label="AA · large text (18pt+)" minRatio={3} pass={evaluation.result.aaLarge} />
              <PassPill label="AA · UI components" minRatio={3} pass={evaluation.result.aaUiComponents} />
              <PassPill label="AAA · normal text" minRatio={7} pass={evaluation.result.aaaNormal} />
              <PassPill label="AAA · large text" minRatio={4.5} pass={evaluation.result.aaaLarge} />
            </View>

            {suggestions && (suggestions.foreground || suggestions.background) ? (
              <View style={styles.fixBlock}>
                <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                  FAILS AA FOR NORMAL TEXT — ONE TAP TO FIX
                </Text>
                <View style={styles.fixRow}>
                  {suggestions.foreground ? (
                    <FixSwatch
                      label="Darken / lighten the text"
                      hex={suggestions.foreground.hex}
                      ratio={suggestions.foreground.ratio}
                      onPress={() => setFg(suggestions.foreground!.hex)}
                    />
                  ) : null}
                  {suggestions.background ? (
                    <FixSwatch
                      label="Adjust the background"
                      hex={suggestions.background.hex}
                      ratio={suggestions.background.ratio}
                      onPress={() => setBg(suggestions.background!.hex)}
                    />
                  ) : null}
                </View>
                <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 8 }]}>
                  Each option is the closest colour to your original that reaches 4.5:1. Tap to apply.
                </Text>
              </View>
            ) : null}
          </>
        ) : (
          <Text style={[theme.typography.body, { color: theme.colors.danger }]}>
            {evaluation.reason}
          </Text>
        )}
      </Card>
    </Screen>
  );
}

function ColorInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const theme = useTheme();
  const valid = !!parseHex(value);
  const [picking, setPicking] = useState(false);
  const eyedropperAvailable = _hasEyeDropper();

  const grabColor = async () => {
    if (!eyedropperAvailable || picking) return;
    setPicking(true);
    try {
      const hex = await _pickColorWithEyeDropper();
      if (hex) onChange(hex);
    } finally {
      setPicking(false);
    }
  };

  return (
    <View style={styles.colorBlock}>
      <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>{label}</Text>
      <View style={styles.colorPickerRow}>
        <View
          style={[
            styles.swatch,
            {
              backgroundColor: valid ? value : theme.colors.surface2,
              borderColor: theme.colors.border,
            },
          ]}
        />
        <TextInput
          value={value}
          onChangeText={onChange}
          placeholder="#000000"
          placeholderTextColor={theme.colors.textMuted}
          autoCapitalize="characters"
          autoCorrect={false}
          accessibilityLabel={`${label} hex code`}
          style={[
            styles.input,
            {
              color: theme.colors.text,
              borderColor: valid ? theme.colors.border : theme.colors.danger,
              backgroundColor: theme.colors.surface,
            },
          ]}
        />
        {Platform.OS === "web" ? (
          // @ts-ignore - native HTML color picker
          <input
            type="color"
            value={valid ? value : "#000000"}
            onChange={(event: any) => onChange(event.target.value)}
            style={{ width: 36, height: 36, padding: 0, border: "none", background: "transparent" }}
          />
        ) : null}
        {eyedropperAvailable ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={
              "Grab a color from anywhere on screen for the " + label.toLowerCase()
            }
            accessibilityHint="Opens an eyedropper. Click anything on the screen to capture its hex color."
            onPress={grabColor}
            disabled={picking}
            style={({ hovered, pressed }: any) => [
              styles.grabBtn,
              {
                borderColor: picking ? theme.colors.accent : theme.colors.border,
                backgroundColor: pressed
                  ? theme.colors.accent + "33"
                  : hovered
                  ? theme.colors.surface2
                  : theme.colors.surface,
              },
            ]}
          >
            <Text style={[styles.grabBtnIcon, { color: theme.colors.accent }]}>{picking ? "..." : "+"}</Text>
            <Text style={[styles.grabBtnLabel, { color: theme.colors.text }]}>
              {picking ? "Pick..." : "Grab"}
            </Text>
          </Pressable>
        ) : null}
      </View>
      {!eyedropperAvailable && Platform.OS === "web" ? (
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 4 }]}>
          Tip: a screen eyedropper would appear here if your browser supported it. Try Chrome, Edge, or Brave.
        </Text>
      ) : null}
    </View>
  );
}

function PassPill({
  label,
  minRatio,
  pass,
}: {
  label: string;
  minRatio: number;
  pass: boolean;
}) {
  const theme = useTheme();
  return (
    <View
      style={[
        styles.pill,
        {
          backgroundColor: pass ? theme.colors.success + "22" : theme.colors.danger + "22",
          borderColor: pass ? theme.colors.success : theme.colors.danger,
        },
      ]}
    >
      <Text
        style={[
          styles.pillIcon,
          { color: pass ? theme.colors.success : theme.colors.danger },
        ]}
      >
        {pass ? "PASS" : "FAIL"}
      </Text>
      <View style={{ flex: 1 }}>
        <Text style={[theme.typography.body, { color: theme.colors.text, fontSize: 13 }]}>
          {label}
        </Text>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
          requires at least {minRatio}:1
        </Text>
      </View>
    </View>
  );
}

function FixSwatch({
  label,
  hex,
  ratio,
  onPress,
}: {
  label: string;
  hex: string;
  ratio: number;
  onPress: () => void;
}) {
  const theme = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${label}: use ${hex}, contrast ${ratio.toFixed(2)} to 1. Tap to apply.`}
      onPress={onPress}
      style={({ hovered, pressed }: any) => [
        styles.fixSwatch,
        {
          borderColor: theme.colors.border,
          backgroundColor: pressed || hovered ? theme.colors.surface2 : theme.colors.surface,
        },
      ]}
    >
      <View style={[styles.fixSwatchChip, { backgroundColor: hex, borderColor: theme.colors.border }]} />
      <View style={{ flex: 1 }}>
        <Text style={[theme.typography.body, { color: theme.colors.text, fontSize: 13, fontWeight: "600" }]}>
          {label}
        </Text>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
          {hex} · {ratio.toFixed(2)}:1
        </Text>
      </View>
    </Pressable>
  );
}

function _safe(value: string): string {
  return parseHex(value) ? value : "#888888";
}

const styles = StyleSheet.create({
  inputRow: { flexDirection: "row", gap: 12, flexWrap: "wrap" },
  colorBlock: { flex: 1, minWidth: 220, gap: 6 },
  colorPickerRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  swatch: { width: 36, height: 36, borderRadius: 8, borderWidth: 1 },
  input: { flex: 1, borderWidth: 1, borderRadius: 8, padding: 10, minWidth: 120 },
  grabBtn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
  },
  grabBtnIcon: { fontSize: 16, fontWeight: "800", lineHeight: 18 },
  grabBtnLabel: { fontSize: 13, fontWeight: "600" },
  presetRow: { flexDirection: "row", gap: 6, flexWrap: "wrap", alignItems: "center", marginTop: 16 },
  preview: {
    marginTop: 12,
    padding: 20,
    borderRadius: 12,
    minHeight: 140,
    justifyContent: "center",
    gap: 8,
  },
  previewLarge: { fontSize: 24, fontWeight: "700" },
  previewBody: { fontSize: 16 },
  previewSmall: { fontSize: 12 },
  ratioRow: { flexDirection: "row", alignItems: "baseline", gap: 12, flexWrap: "wrap" },
  ratio: { fontSize: 42, fontWeight: "800" },
  passGrid: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 12 },
  pill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    minWidth: 200,
    flexGrow: 1,
  },
  pillIcon: { fontSize: 18, fontWeight: "800" },
  fixBlock: { marginTop: 18 },
  fixRow: { flexDirection: "row", gap: 10, flexWrap: "wrap", marginTop: 10 },
  fixSwatch: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    borderWidth: 1,
    borderRadius: 10,
    padding: 10,
    minWidth: 220,
    flexGrow: 1,
  },
  fixSwatchChip: { width: 28, height: 28, borderRadius: 6, borderWidth: 1 },
});
