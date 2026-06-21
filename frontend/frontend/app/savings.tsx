/**
 * Savings calculator — an honest manual-vs-508-Agent cost comparison.
 *
 * The numbers are deliberately defensible, not salesy:
 *  - Manual cost = pages × a $/page rate the user picks within the industry
 *    $5–25 range the landing page cites (default $15).
 *  - 508 Agent cost = documents × credits-per-file × a real per-credit price
 *    (Pro pack, $0.06/credit — the mid "most popular" tier, not the cheapest).
 * Remediation is priced per file regardless of page count, which is exactly how
 * the product charges, so multi-page documents legitimately favour automation.
 * Every assumption is shown on screen so the figure can be checked.
 */
import { useMemo, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, TextInput, View, useWindowDimensions } from "react-native";
import { useRouter } from "expo-router";

import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Hero } from "../src/ui/components/Hero";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

type DocType = "pdf" | "word" | "ppt" | "html";

// Credits per remediated file, mirrored from billing.tsx / help.tsx / credits.py.
const CREDITS_BY_TYPE: Record<DocType, number> = { pdf: 5, word: 3, ppt: 4, html: 3 };
const TYPE_LABEL: Record<DocType, string> = { pdf: "PDF", word: "Word", ppt: "PowerPoint", html: "HTML" };
// Pro credit pack: $15 / 250 credits = $0.06 per credit (representative mid tier).
const PER_CREDIT_USD = 15 / 250;

function parseNum(s: string): number {
  const n = parseFloat(s.replace(/[^0-9.]/g, ""));
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function money(n: number): string {
  return "$" + n.toLocaleString(undefined, { maximumFractionDigits: n < 100 ? 2 : 0 });
}

export default function SavingsScreen() {
  const theme = useTheme();
  const router = useRouter();
  const { width } = useWindowDimensions();
  const stacked = width < 760;

  const [docs, setDocs] = useState("100");
  const [pages, setPages] = useState("10");
  const [rate, setRate] = useState("15");
  const [docType, setDocType] = useState<DocType>("pdf");

  const result = useMemo(() => {
    const nDocs = parseNum(docs);
    const nPages = parseNum(pages);
    const nRate = parseNum(rate);
    const totalPages = nDocs * nPages;
    const manual = totalPages * nRate;
    const ours = nDocs * CREDITS_BY_TYPE[docType] * PER_CREDIT_USD;
    const saved = manual - ours;
    const pct = manual > 0 ? (saved / manual) * 100 : 0;
    return { nDocs, totalPages, manual, ours, saved, pct };
  }, [docs, pages, rate, docType]);

  const hasInput = result.nDocs > 0 && result.totalPages > 0;
  const beneficial = hasInput && result.saved > 0;

  return (
    <Screen scroll title="Savings calculator">
      <Hero
        shader="ember"
        eyebrow="SAVINGS"
        title="What you'd save vs. manual remediation"
        subtitle="Manual document accessibility work is billed by the page. 508 Agent is billed per file, at a fraction of the cost. Put in your numbers — every assumption is shown so you can check the math."
      />

      <View style={[styles.row, stacked && styles.col, { marginTop: 16 }]}>
        {/* Inputs */}
        <Card style={stacked ? undefined : { flex: 1 }}>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Your backlog</Text>

          <Field label="How many documents?" value={docs} onChange={setDocs} theme={theme} suffix="documents" />
          <Field label="Average pages per document" value={pages} onChange={setPages} theme={theme} suffix="pages" />
          <Field label="Manual rate per page" value={rate} onChange={setRate} theme={theme} prefix="$" suffix="/ page" />
          <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 4 }}>
            Industry manual remediation typically runs $5–25 per page.
          </Text>

          <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 18 }]}>
            DOCUMENT TYPE
          </Text>
          <View style={styles.typeRow}>
            {(Object.keys(CREDITS_BY_TYPE) as DocType[]).map((t) => {
              const active = docType === t;
              return (
                <Pressable
                  key={t}
                  accessibilityRole="button"
                  accessibilityLabel={`${TYPE_LABEL[t]} — ${CREDITS_BY_TYPE[t]} credits per file`}
                  onPress={() => setDocType(t)}
                  style={[
                    styles.typeChip,
                    {
                      borderColor: active ? theme.colors.accent : theme.colors.border,
                      backgroundColor: active ? theme.colors.accent + "22" : "transparent",
                    },
                  ]}
                >
                  <Text style={{ color: active ? theme.colors.accent : theme.colors.textMuted, fontWeight: "700", fontSize: 13 }}>
                    {TYPE_LABEL[t]} · {CREDITS_BY_TYPE[t]}cr
                  </Text>
                </Pressable>
              );
            })}
          </View>
        </Card>

        {/* Result */}
        <Card style={[stacked ? undefined : { flex: 1 }, { borderColor: theme.colors.accent, borderWidth: 2 }]}>
          {beneficial ? (
            <>
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>YOU'D SAVE</Text>
              <Text style={[theme.typography.display, { color: theme.colors.accent, marginTop: 2 }]}>
                {money(result.saved)}
              </Text>
              <Text style={{ color: theme.colors.text, fontSize: 14, marginTop: 2 }}>
                {result.ours > 0 ? Math.min(99, Math.round(result.pct)) : 100}% less than doing it manually
              </Text>

              <View style={styles.compareBlock}>
                <CompareRow label="Manual remediation" value={money(result.manual)} sub={`${result.totalPages.toLocaleString()} pages × ${money(parseNum(rate))}/page`} theme={theme} muted />
                <CompareRow label="508 Agent" value={money(result.ours)} sub={`${result.nDocs.toLocaleString()} × ${TYPE_LABEL[docType]} (${CREDITS_BY_TYPE[docType]} credits each)`} theme={theme} />
              </View>

              <View style={{ marginTop: 18 }}>
                <Button title="Start free — 25 credits" onPress={() => router.push("/audit" as any)} />
                <View style={{ height: 10 }} />
                <Button title="See pricing" variant="ghost" onPress={() => router.push("/billing" as any)} />
              </View>
            </>
          ) : (
            <View style={{ paddingVertical: 24, alignItems: "center" }}>
              <Text style={{ color: theme.colors.textMuted, textAlign: "center" }}>
                Enter your document count, pages and rate to see the comparison.
              </Text>
            </View>
          )}
        </Card>
      </View>

      <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 20, lineHeight: 18, paddingHorizontal: 4 }}>
        How this is calculated: manual cost = total pages × your per-page rate. 508 Agent cost = number of
        documents × credits per file ({TYPE_LABEL[docType]} = {CREDITS_BY_TYPE[docType]} credits) × $
        {PER_CREDIT_USD.toFixed(2)} per credit (our Pro credit pack — not the cheapest pack). Analysis is always free,
        so it isn't counted. Remediation is priced per file regardless of length, which is why longer documents save
        more. Actual results depend on document complexity and how many fixes need human review.
      </Text>

      <View style={{ height: 24 }} />
    </Screen>
  );
}

function Field({
  label,
  value,
  onChange,
  theme,
  prefix,
  suffix,
}: {
  label: string;
  value: string;
  onChange: (s: string) => void;
  theme: ReturnType<typeof useTheme>;
  prefix?: string;
  suffix?: string;
}) {
  return (
    <View style={{ marginTop: 16 }}>
      <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginBottom: 6 }]}>
        {label.toUpperCase()}
      </Text>
      <View style={[styles.inputWrap, { borderColor: theme.colors.border }]}>
        {prefix ? <Text style={{ color: theme.colors.textMuted, fontSize: 16, marginRight: 4 }}>{prefix}</Text> : null}
        <TextInput
          value={value}
          onChangeText={onChange}
          keyboardType={Platform.OS === "web" ? "default" : "numeric"}
          inputMode="numeric"
          accessibilityLabel={label}
          placeholderTextColor={theme.colors.textMuted}
          style={[styles.input, { color: theme.colors.text }]}
        />
        {suffix ? <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginLeft: 6 }}>{suffix}</Text> : null}
      </View>
    </View>
  );
}

function CompareRow({
  label,
  value,
  sub,
  theme,
  muted,
}: {
  label: string;
  value: string;
  sub: string;
  theme: ReturnType<typeof useTheme>;
  muted?: boolean;
}) {
  return (
    <View style={styles.compareRow}>
      <View style={{ flex: 1 }}>
        <Text style={{ color: theme.colors.text, fontSize: 14, fontWeight: "700" }}>{label}</Text>
        <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 2 }}>{sub}</Text>
      </View>
      <Text style={{ color: muted ? theme.colors.textMuted : theme.colors.text, fontSize: 18, fontWeight: "800" }}>
        {value}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", gap: 16, alignItems: "flex-start" },
  col: { flexDirection: "column" },
  typeRow: { flexDirection: "row", gap: 8, marginTop: 8, flexWrap: "wrap" },
  typeChip: { paddingHorizontal: 12, paddingVertical: 8, borderRadius: 999, borderWidth: 1 },
  inputWrap: { flexDirection: "row", alignItems: "center", borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 4 },
  input: { flex: 1, fontSize: 16, paddingVertical: 8, ...(Platform.OS === "web" ? ({ outlineStyle: "none" } as any) : null) },
  compareBlock: { marginTop: 20, gap: 12 },
  compareRow: { flexDirection: "row", alignItems: "center", gap: 12 },
});
