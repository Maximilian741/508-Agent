/**
 * Scan a URL — free, read-only accessibility scan of any public web page.
 *
 * Enter a URL; the backend fetches it (SSRF-guarded) and runs the same WCAG
 * analyzers as an uploaded document, then we show the score + a plain-English
 * list of findings. A live page can't be auto-fixed here (we can't write back to
 * someone's site), so the CTA points to the upload flow for documents.
 */
import { useMemo, useState } from "react";
import { Platform, Pressable, PressableStateCallbackType, StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";

import { PipelineResponse, SiteScanResponse, createApiClient } from "../src/api/client";
import { lookupIssue } from "../src/domain/issueCatalog";
import { useAppStore } from "../src/store/useAppStore";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Hero } from "../src/ui/components/Hero";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

const SEV_ORDER: Record<string, number> = { error: 0, warning: 1, info: 2 };

export default function ScanUrlScreen() {
  const theme = useTheme();
  const router = useRouter();
  const apiBaseUrl = useAppStore((s) => s.apiBaseUrl);
  const mockMode = useAppStore((s) => s.mockMode);

  const [url, setUrl] = useState("");
  const [mode, setMode] = useState<"page" | "site">("page");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PipelineResponse | null>(null);
  const [siteResult, setSiteResult] = useState<SiteScanResponse | null>(null);

  const run = async () => {
    const target = url.trim();
    if (!target) {
      setError(mode === "site" ? "Enter a site URL to scan." : "Enter a web page URL to scan.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    setSiteResult(null);
    try {
      const client = createApiClient({ baseUrl: apiBaseUrl, mockMode });
      if (mode === "site") {
        setSiteResult(await client.runSiteScan(target, 10));
      } else {
        setResult(await client.runPipelineUrl(target));
      }
    } catch (e) {
      const err = e as Error & { status?: number };
      if (err.status === 401) {
        setError("Please sign in to use the scanner.");
      } else {
        // The client already extracted the backend's structured {detail}; it's a
        // clear, user-safe message for SSRF/fetch failures.
        setError(err.message || "Could not scan that URL.");
      }
    } finally {
      setLoading(false);
    }
  };

  // Group findings by rule so the list reads as "3× Image missing alt text".
  const grouped = useMemo(() => {
    if (!result) return [];
    const counts = new Map<string, { ruleId: string; severity: string; count: number }>();
    for (const v of result.violations) {
      const prev = counts.get(v.ruleId);
      if (prev) prev.count += 1;
      else counts.set(v.ruleId, { ruleId: v.ruleId, severity: v.severity, count: 1 });
    }
    return Array.from(counts.values()).sort(
      (a, b) => (SEV_ORDER[a.severity] ?? 9) - (SEV_ORDER[b.severity] ?? 9) || b.count - a.count,
    );
  }, [result]);

  const sevColor = (sev: string) =>
    sev === "error" ? theme.colors.danger : sev === "warning" ? theme.colors.warning : theme.colors.textMuted;

  return (
    <Screen scroll>
      <Hero
        eyebrow="Free tool"
        title="Scan a web page or whole site for accessibility issues"
        subtitle="Paste any public URL. We check it against WCAG 2.1 AA / Section 508 and show what to fix — no upload, no credits."
      />

      <Card variant="content" style={{ gap: 12 }}>
        <View
          style={[styles.modeRow, { borderColor: theme.colors.border }]}
          accessibilityRole="radiogroup"
          accessibilityLabel="What to scan"
        >
          {([
            { key: "page", label: "This page" },
            { key: "site", label: "Whole site" },
          ] as const).map((m) => {
            const active = mode === m.key;
            return (
              <Pressable
                key={m.key}
                onPress={() => setMode(m.key)}
                accessibilityRole="radio"
                accessibilityState={{ selected: active }}
                accessibilityLabel={`Scan ${m.label}`}
                style={({ focused }: PressableStateCallbackType & { focused?: boolean }) => [
                  styles.modeBtn,
                  active && { backgroundColor: theme.colors.surface3 },
                  focused ? { outlineWidth: 2, outlineColor: theme.colors.accent, outlineStyle: "solid" } : null,
                ]}
              >
                <Text
                  style={[
                    styles.modeText,
                    { color: active ? theme.colors.text : theme.colors.textMuted, fontWeight: active ? "700" : "500" },
                  ]}
                >
                  {m.label}
                </Text>
              </Pressable>
            );
          })}
        </View>

        <Text style={[styles.label, { color: theme.colors.text }]}>
          {mode === "site" ? "Site URL" : "Web page URL"}
        </Text>
        <TextInput
          value={url}
          onChangeText={setUrl}
          placeholder="https://example.com"
          placeholderTextColor={theme.colors.textMuted}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType={Platform.OS === "web" ? "default" : "url"}
          onSubmitEditing={run}
          accessibilityLabel={mode === "site" ? "Site URL to scan" : "Web page URL to scan"}
          style={[
            styles.input,
            { color: theme.colors.text, borderColor: theme.colors.border, backgroundColor: theme.colors.surface },
          ]}
        />
        {mode === "site" ? (
          <Text style={[styles.hint, { color: theme.colors.textMuted }]}>
            We read the site&apos;s sitemap.xml and scan up to 10 pages on the same domain.
          </Text>
        ) : null}
        <Button
          title={loading ? "Scanning…" : mode === "site" ? "Scan this site" : "Scan this page"}
          onPress={run}
          disabled={loading}
        />
        {error ? <InlineNotice tone="danger" title="Couldn't scan" message={error} /> : null}
      </Card>

      {siteResult ? (
        <Card variant="data" style={{ gap: 14, marginTop: 16 }}>
          <View style={styles.scoreRow}>
            <View>
              <Text style={[styles.grade, { color: theme.colors.text }]}>{siteResult.grade || "—"}</Text>
              <Text style={[styles.scoreSub, { color: theme.colors.textMuted }]}>
                {Math.round(siteResult.score)} / 100 average
              </Text>
            </View>
            <View style={{ flex: 1, alignItems: "flex-end" }}>
              <Text style={[styles.issueCount, { color: theme.colors.text }]}>
                {siteResult.totalIssues} issue{siteResult.totalIssues === 1 ? "" : "s"} across{" "}
                {siteResult.pagesScanned} page{siteResult.pagesScanned === 1 ? "" : "s"}
              </Text>
              <Text style={[styles.scoreSub, { color: theme.colors.textMuted }]}>
                {siteResult.pagesDiscovered} discovered
                {siteResult.pagesFailed > 0 ? ` · ${siteResult.pagesFailed} couldn't be read` : ""}
              </Text>
            </View>
          </View>

          <Text style={[styles.sectionTitle, { color: theme.colors.text }]}>Most common issues</Text>
          {siteResult.issues.length === 0 ? (
            <Text style={[styles.findingWhy, { color: theme.colors.textMuted }]}>
              No issues detected on the pages we scanned.
            </Text>
          ) : (
            siteResult.issues.map((i) => {
              const entry = lookupIssue(i.ruleId);
              return (
                <View key={i.ruleId} style={[styles.finding, { borderTopColor: theme.colors.border }]}>
                  <View style={[styles.dot, { backgroundColor: sevColor(i.severity) }]} />
                  <View style={{ flex: 1 }}>
                    <Text style={[styles.findingTitle, { color: theme.colors.text }]}>
                      {entry.title}  ·  {i.totalCount}×
                    </Text>
                    <Text style={[styles.findingWhy, { color: theme.colors.textMuted }]}>
                      on {i.pageCount} page{i.pageCount === 1 ? "" : "s"} · {entry.standards.wcag.join(", ")}
                    </Text>
                  </View>
                </View>
              );
            })
          )}

          <Text style={[styles.sectionTitle, { color: theme.colors.text }]}>Pages</Text>
          {siteResult.pages.map((p) => (
            <View key={p.url} style={[styles.finding, { borderTopColor: theme.colors.border }]}>
              <View style={{ flex: 1 }}>
                <Text style={[styles.findingTitle, { color: theme.colors.text }]} numberOfLines={1}>
                  {p.title || p.url}
                </Text>
                <Text style={[styles.findingWhy, { color: theme.colors.textMuted }]} numberOfLines={1}>
                  {p.status === "failed"
                    ? p.note || "Could not be read"
                    : `${p.issueCount} issue${p.issueCount === 1 ? "" : "s"} · ${p.errorCount} error${
                        p.errorCount === 1 ? "" : "s"
                      } · grade ${p.grade}`}
                </Text>
              </View>
            </View>
          ))}

          <InlineNotice
            tone="info"
            title="Want these fixed automatically?"
            message="A live site can't be auto-fixed here. Upload a document (PDF, Word, PowerPoint, HTML) and we'll remediate it for you."
          />
          <Button title="Upload a document to auto-fix" variant="secondary" onPress={() => router.push("/audit")} />
        </Card>
      ) : null}

      {result ? (
        <Card variant="data" style={{ gap: 14, marginTop: 16 }}>
          <View style={styles.scoreRow}>
            <View>
              <Text style={[styles.grade, { color: theme.colors.text }]}>{result.score.grade}</Text>
              <Text style={[styles.scoreSub, { color: theme.colors.textMuted }]}>
                {Math.round(result.score.score)} / 100
              </Text>
            </View>
            <View style={{ flex: 1, alignItems: "flex-end" }}>
              <Text style={[styles.issueCount, { color: theme.colors.text }]}>
                {result.violations.length === 0
                  ? "No issues detected"
                  : `${result.violations.length} issue${result.violations.length === 1 ? "" : "s"} found`}
              </Text>
              {result.summary.title ? (
                <Text style={[styles.scoreSub, { color: theme.colors.textMuted }]} numberOfLines={1}>
                  {result.summary.title}
                </Text>
              ) : null}
            </View>
          </View>

          {grouped.map((g) => {
            const entry = lookupIssue(g.ruleId);
            return (
              <View key={g.ruleId} style={[styles.finding, { borderTopColor: theme.colors.border }]}>
                <View style={[styles.dot, { backgroundColor: sevColor(g.severity) }]} />
                <View style={{ flex: 1 }}>
                  <Text style={[styles.findingTitle, { color: theme.colors.text }]}>
                    {entry.title}
                    {g.count > 1 ? `  ·  ${g.count}×` : ""}
                  </Text>
                  <Text style={[styles.findingWhy, { color: theme.colors.textMuted }]}>{entry.summary}</Text>
                  <Text style={[styles.findingStd, { color: theme.colors.textMuted }]}>
                    {entry.standards.wcag.join(", ")}
                  </Text>
                </View>
              </View>
            );
          })}

          <InlineNotice
            tone="info"
            title="Want these fixed automatically?"
            message="A live web page can't be auto-fixed here. To auto-remediate a document (PDF, Word, PowerPoint, HTML), upload the file."
          />
          <Button title="Upload a document to auto-fix" variant="secondary" onPress={() => router.push("/audit")} />
        </Card>
      ) : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  label: { fontSize: 14, fontWeight: "600" },
  modeRow: { flexDirection: "row", borderWidth: 1, borderRadius: 8, overflow: "hidden", alignSelf: "flex-start" },
  modeBtn: { paddingVertical: 8, paddingHorizontal: 16 },
  modeText: { fontSize: 14 },
  hint: { fontSize: 12 },
  sectionTitle: { fontSize: 15, fontWeight: "700", marginTop: 4 },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 15 },
  scoreRow: { flexDirection: "row", alignItems: "center", gap: 16 },
  grade: { fontSize: 40, fontWeight: "800", lineHeight: 44 },
  scoreSub: { fontSize: 13 },
  issueCount: { fontSize: 16, fontWeight: "700" },
  finding: { flexDirection: "row", gap: 10, paddingTop: 12, borderTopWidth: 1 },
  dot: { width: 10, height: 10, borderRadius: 5, marginTop: 5 },
  findingTitle: { fontSize: 15, fontWeight: "600" },
  findingWhy: { fontSize: 13, marginTop: 2 },
  findingStd: { fontSize: 12, marginTop: 4 },
});
