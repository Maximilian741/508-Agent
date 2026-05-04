/**
 * Settings — backend URL configuration and Demo Mode toggle.
 *
 * Demo Mode is the renamed-and-explained "mockMode" toggle.  In testing,
 * users couldn't tell whether they were looking at real findings or fake
 * data; the new copy spells it out.
 */

import { useState } from "react";
import { Pressable, StyleSheet, Switch, Text, TextInput, View } from "react-native";

import { clearHistory } from "../src/domain/auditHistory";
import {
  clearDemoData,
  hasDemoData,
  loadDemoData,
  nukeDemoData,
} from "../src/domain/demoSeed";
import { DEFAULT_WEIGHTS, loadWeights, resetWeights, saveWeights } from "../src/domain/scoreWeights";
import {
  notificationsAvailable,
  notificationsEnabled,
  requestPermission,
  setNotificationsEnabled,
} from "../src/domain/notifications";
import { useAppStore } from "../src/store/useAppStore";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { PixelIcon } from "../src/ui/components/PixelIcon";
import { Chip } from "../src/ui/components/Chip";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";
import { ShaderCanvas } from "../src/ui/components/ShaderCanvas";
import { useToast } from "../src/ui/toast";
import { useTheme } from "../src/ui/useTheme";

export default function SettingsScreen() {
  const theme = useTheme();
  const toast = useToast();
  const apiBaseUrl = useAppStore((state) => state.apiBaseUrl);
  const mockMode = useAppStore((state) => state.mockMode);
  const backendUrlWarning = useAppStore((state) => state.backendUrlWarning);
  const backendHealth = useAppStore((state) => state.backendHealth);
  const backendHealthMessage = useAppStore((state) => state.backendHealthMessage);
  const themeMode = useAppStore((state) => state.themeMode);
  const setThemeMode = useAppStore((state) => state.setThemeMode);
  const setApiBaseUrl = useAppStore((state) => state.setApiBaseUrl);
  const saveApiBaseUrl = useAppStore((state) => state.saveApiBaseUrl);
  const setMockMode = useAppStore((state) => state.setMockMode);
  const [draftUrl, setDraftUrl] = useState(apiBaseUrl);
  const [notifyOn, setNotifyOn] = useState(notificationsEnabled());

  return (
    <Screen scroll>
      <View style={{ position: "relative", borderRadius: 18, overflow: "hidden", marginBottom: 16, minHeight: 160, backgroundColor: "#0B1020", padding: 24, justifyContent: "center" }}>
        <ShaderCanvas variant="ember" opacity={0.45} />
        <View style={[styles.header, { position: "relative", zIndex: 1 }]}>
          <View>
            <Text style={[theme.typography.title, { color: "#FFFFFF" }]}>Settings</Text>
            <Text style={[theme.typography.body, { color: "rgba(255,255,255,0.85)" }]}>
              Where the analyzer lives, and whether you're working with real or fake data.
            </Text>
          </View>
          <Chip
            label={mockMode ? "Demo data" : "Live data"}
            tone={mockMode ? "warning" : "success"}
          />
        </View>
      </View>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="play" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Demo Mode</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          When Demo Mode is on, the app shows pre-baked sample data instead of calling the
          analyzer service. It's useful for exploring the UI without setting up the backend, but
          findings and scores in Demo Mode are <Text style={{ fontWeight: "700" }}>not real</Text>
          — don't rely on them for an audit.
        </Text>
        <View style={styles.toggleRow}>
          <Text style={[theme.typography.body, { color: theme.colors.text }]}>
            {mockMode ? "Demo Mode is ON" : "Demo Mode is OFF (live)"}
          </Text>
          <Switch value={mockMode} onValueChange={setMockMode} />
        </View>
      </Card>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="bolt" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Analyzer service URL</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          The Python backend writes its own URL to{" "}
          <Text style={[theme.typography.mono, { color: theme.colors.text }]}>
            backend/.runtime/backend_url.txt
          </Text>{" "}
          when you run <Text style={[theme.typography.mono, { color: theme.colors.text }]}>python dev_run.py</Text>.
          You only need to override it here if you're running the backend on a different host.
        </Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 6 }]}>
          Currently using: {apiBaseUrl}
        </Text>
        <TextInput
          value={draftUrl}
          onChangeText={setDraftUrl}
          style={[
            styles.input,
            { borderColor: theme.colors.border, color: theme.colors.text },
          ]}
          placeholder="http://127.0.0.1:8000"
          placeholderTextColor={theme.colors.textMuted}
        />
        <View style={styles.buttonRow}>
          <Button title="Save & test" onPress={() => saveApiBaseUrl(draftUrl)} />
          <Button title="Save without testing" onPress={() => setApiBaseUrl(draftUrl)} variant="ghost" />
        </View>
        {!mockMode && backendHealth === "error" && (
          <InlineNotice
            title="Analyzer is unreachable"
            message={
              backendHealthMessage ??
              backendUrlWarning ??
              "Open a terminal in the project folder and run:  cd backend  then  python dev_run.py — leave it running."
            }
            tone="danger"
          />
        )}
        {!mockMode && backendHealth === "ok" && (
          <InlineNotice
            title="Connected"
            message={`Successfully reached ${apiBaseUrl}.`}
            tone="success"
          />
        )}
      </Card>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="star" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Appearance</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Light / Dark / Match system. Affects the entire app.
        </Text>
        <View style={styles.themeRow}>
          {(["system", "light", "dark"] as const).map((mode) => (
            <Pressable
              key={mode}
              onPress={() => setThemeMode(mode)}
              accessibilityLabel={`Set theme to ${mode}`}
              style={[
                styles.themeChoice,
                {
                  borderColor: themeMode === mode ? theme.colors.accent : theme.colors.border,
                  backgroundColor: themeMode === mode ? theme.colors.accent + "22" : theme.colors.surface,
                },
              ]}
            >
              <Text
                style={[
                  theme.typography.body,
                  {
                    color: themeMode === mode ? theme.colors.accent : theme.colors.text,
                    fontWeight: themeMode === mode ? "700" : "500",
                  },
                ]}
              >
                {mode === "system" ? "Match system" : mode === "light" ? "Light" : "Dark"}
              </Text>
            </Pressable>
          ))}
        </View>
      </Card>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="spark" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Notifications</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Get a browser notification and a soft chime when an audit finishes — useful when you've
          switched tabs while a long document is being analyzed.
        </Text>
        <View style={styles.toggleRow}>
          <Text style={[theme.typography.body, { color: theme.colors.text }]}>
            {notifyOn
              ? "Audit-complete notifications: ON"
              : notificationsAvailable()
              ? "Audit-complete notifications: OFF"
              : "Notifications aren't supported in this browser"}
          </Text>
          <Switch
            value={notifyOn}
            disabled={!notificationsAvailable()}
            onValueChange={async (next) => {
              if (next) {
                const status = await requestPermission();
                if (status === "granted") {
                  setNotificationsEnabled(true);
                  setNotifyOn(true);
                  toast.success("Notifications enabled");
                } else {
                  toast.warning("Permission not granted", {
                    description: "Enable notifications in your browser settings, then try again.",
                  });
                }
              } else {
                setNotificationsEnabled(false);
                setNotifyOn(false);
                toast.info("Notifications disabled");
              }
            }}
          />
        </View>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Score weighting</Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Tune how much each severity contributes to the live audit score. Defaults match the
          backend formula. Set Info to 1 if you want informational findings to count, or set
          Warning to 2 if your team treats warnings as effectively as serious as errors.
        </Text>
        <ScoreWeightControls />
      </Card>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="coin" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Demo data</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Populate the dashboard, history, and achievements with a realistic
          set of fixture audits so you can poke around without uploading any
          documents. Seeded rows live in localStorage and can be removed with
          one click.
        </Text>
        <View style={[styles.buttonRow, { marginTop: 8 }]}>
          <Button
            title="Load demo data"
            onPress={() => {
              const r = loadDemoData({ mode: "merge" });
              toast.success(
                `Loaded ${r.historyAdded} audits, ${r.achievementsUnlocked} new badges, ${r.workspacesCreated} workspaces.`,
              );
            }}
          />
          <Button
            title={hasDemoData() ? "Clear demo data" : "Clear demo (none)"}
            variant="ghost"
            disabled={!hasDemoData()}
            onPress={() => {
              const r = clearDemoData();
              toast.info(`Removed ${r.removed} demo audits.`);
            }}
          />
          <Button
            title="Reset everything"
            variant="ghost"
            onPress={() => {
              if (
                typeof window !== "undefined" &&
                !window.confirm(
                  "Reset all demo data, achievements, and seeded workspaces? Real audits and your own workspaces are kept.",
                )
              ) {
                return;
              }
              nukeDemoData();
              toast.info("Demo data, achievements, and seeded workspaces reset.");
            }}
          />
        </View>
      </Card>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="doc" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Audit history</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          A list of every audit you've run is kept locally so the home page can show recent work.
          Nothing is sent off-device. Clear it any time.
        </Text>
        <View style={[styles.buttonRow, { marginTop: 8 }]}>
          <Button
            title="Clear audit history"
            variant="ghost"
            onPress={() => {
              if (
                typeof window !== "undefined" &&
                !window.confirm("Clear all locally-stored audit history?")
              ) {
                return;
              }
              clearHistory();
              toast.info("Audit history cleared");
            }}
          />
        </View>
      </Card>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="gear" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Diagnostics</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Run a quick health check to confirm the analyzer is reachable and what AI provider is
          active. The result appears below.
        </Text>
        <DiagnosticsPanel apiBaseUrl={apiBaseUrl} />
      </Card>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="spark" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>AI provider</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Alt-text and link-text suggestions can be powered by an AI vision model. The backend
          picks one based on environment variables when it starts:
        </Text>
        <View style={styles.aiList}>
          <Text style={[theme.typography.body, { color: theme.colors.text }]}>
            • Set <Text style={[theme.typography.mono]}>ANTHROPIC_API_KEY</Text> for Claude with vision.
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.text }]}>
            • Set <Text style={[theme.typography.mono]}>OPENAI_API_KEY</Text> for GPT-4 with vision.
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.text }]}>
            • Without either, the backend falls back to local heuristics (no network, lower quality).
          </Text>
        </View>
      </Card>
    </Screen>
  );
}

interface DiagnosticCheck {
  name: string;
  ok: boolean;
  ms: number;
  detail?: string;
  items?: string[];
}

interface DiagnosticPayload {
  status: "ok" | "degraded";
  checks: DiagnosticCheck[];
  ai: {
    provider: string;
    ok: boolean;
    ms: number;
    sampleOutput?: string;
    confidence?: number;
    error?: string;
  };
  totals: { ms: number };
}

function ScoreWeightControls() {
  const theme = useTheme();
  const toast = useToast();
  const [weights, setWeights] = useState(loadWeights());

  const setWeight = (key: "error" | "warning" | "info", delta: number) => {
    const next = { ...weights, [key]: Math.max(0, Math.min(10, weights[key] + delta)) };
    setWeights(next);
    saveWeights(next);
  };

  const reset = () => {
    setWeights(resetWeights());
    toast.info("Score weights reset to defaults");
  };

  const isDefault =
    weights.error === DEFAULT_WEIGHTS.error &&
    weights.warning === DEFAULT_WEIGHTS.warning &&
    weights.info === DEFAULT_WEIGHTS.info;

  return (
    <View style={{ marginTop: 12, gap: 8 }}>
      {(["error", "warning", "info"] as const).map((key) => (
        <View
          key={key}
          style={{
            flexDirection: "row",
            alignItems: "center",
            gap: 12,
            padding: 8,
            borderWidth: 1,
            borderColor: theme.colors.border,
            backgroundColor: theme.colors.surface2,
            borderRadius: 8,
          }}
        >
          <View
            style={{
              width: 8,
              height: 8,
              borderRadius: 4,
              backgroundColor:
                key === "error" ? theme.colors.danger : key === "warning" ? theme.colors.warning : theme.colors.info,
            }}
          />
          <Text style={[theme.typography.body, { color: theme.colors.text, flex: 1, fontWeight: "600" }]}>
            {key === "error" ? "Error" : key === "warning" ? "Warning" : "Info"}
          </Text>
          <Pressable
            onPress={() => setWeight(key, -1)}
            accessibilityLabel={`Decrease ${key} weight`}
            style={{
              width: 28,
              height: 28,
              borderRadius: 6,
              borderWidth: 1,
              borderColor: theme.colors.border,
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Text style={{ color: theme.colors.text, fontWeight: "700" }}>−</Text>
          </Pressable>
          <Text
            style={[
              theme.typography.body,
              { color: theme.colors.text, fontWeight: "700", minWidth: 32, textAlign: "center" },
            ]}
          >
            {weights[key]}
          </Text>
          <Pressable
            onPress={() => setWeight(key, 1)}
            accessibilityLabel={`Increase ${key} weight`}
            style={{
              width: 28,
              height: 28,
              borderRadius: 6,
              borderWidth: 1,
              borderColor: theme.colors.border,
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Text style={{ color: theme.colors.text, fontWeight: "700" }}>+</Text>
          </Pressable>
        </View>
      ))}
      <View style={{ flexDirection: "row", gap: 8, marginTop: 4 }}>
        <Button title="Reset to defaults" variant="ghost" onPress={reset} disabled={isDefault} />
        <Text
          style={[
            theme.typography.caption,
            { color: theme.colors.textMuted, alignSelf: "center", marginLeft: "auto" },
          ]}
        >
          Active during your next audit run
        </Text>
      </View>
    </View>
  );
}

function DiagnosticsPanel({ apiBaseUrl }: { apiBaseUrl: string }) {
  const theme = useTheme();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DiagnosticPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runDiagnostics = async () => {
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl}/diagnostics`);
      if (!response.ok) {
        // Fall back to /healthz so we report something useful.
        await fetch(`${apiBaseUrl}/healthz`);
        throw new Error(`Diagnostics endpoint returned ${response.status}`);
      }
      const payload = (await response.json()) as DiagnosticPayload;
      setResult(payload);
      if (payload.status === "ok") {
        toast.success("All systems healthy", { description: `${payload.totals.ms}ms` });
      } else {
        toast.warning("Some checks degraded", {
          description: "See diagnostics panel for details.",
        });
      }
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      toast.error("Diagnostics failed", { description: msg });
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={{ marginTop: 8, gap: 12 }}>
      <View style={{ flexDirection: "row", gap: 8 }}>
        <Button title={busy ? "Running…" : "Run diagnostic"} onPress={runDiagnostics} loading={busy} />
      </View>
      {error ? (
        <View
          style={{
            borderWidth: 1,
            borderColor: theme.colors.danger,
            backgroundColor: theme.colors.danger + "11",
            borderRadius: 8,
            padding: 10,
          }}
        >
          <Text style={[theme.typography.mono, { color: theme.colors.danger }]}>
            ERROR: {error}
          </Text>
        </View>
      ) : null}
      {result ? (
        <View style={{ gap: 8 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 14 }]}>
              {result.status === "ok" ? "All checks passed" : "Some checks degraded"}
            </Text>
            <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
              ({result.totals.ms}ms total)
            </Text>
          </View>
          {result.checks.map((check) => (
            <View
              key={check.name}
              style={{
                borderWidth: 1,
                borderColor: check.ok ? theme.colors.border : theme.colors.danger,
                backgroundColor: check.ok ? theme.colors.surface2 : theme.colors.danger + "11",
                borderRadius: 8,
                padding: 10,
                gap: 4,
              }}
            >
              <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
                <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "700" }]}>
                  {check.ok ? "✓" : "✗"} {check.name}
                </Text>
                <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                  {check.ms}ms
                </Text>
              </View>
              {check.detail ? (
                <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
                  {check.detail}
                </Text>
              ) : null}
              {check.items && check.items.length ? (
                <Text
                  style={[theme.typography.mono, { color: theme.colors.textMuted, fontSize: 11 }]}
                  numberOfLines={3}
                >
                  {check.items.join(", ")}
                </Text>
              ) : null}
            </View>
          ))}
          <View
            style={{
              borderWidth: 1,
              borderColor: result.ai.ok ? theme.colors.border : theme.colors.danger,
              backgroundColor: result.ai.ok ? theme.colors.surface2 : theme.colors.danger + "11",
              borderRadius: 8,
              padding: 10,
              gap: 6,
            }}
          >
            <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
              <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "700" }]}>
                {result.ai.ok ? "✓" : "✗"} AI provider · {result.ai.provider}
              </Text>
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                {result.ai.ms}ms
              </Text>
            </View>
            {result.ai.error ? (
              <Text style={[theme.typography.mono, { color: theme.colors.danger, fontSize: 11 }]}>
                {result.ai.error}
              </Text>
            ) : null}
            {result.ai.sampleOutput ? (
              <View>
                <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                  Example output (alt-text for a synthetic image)
                  {typeof result.ai.confidence === "number"
                    ? ` · confidence ${result.ai.confidence}`
                    : ""}
                </Text>
                <Text
                  style={[theme.typography.body, { color: theme.colors.text, marginTop: 2 }]}
                >
                  "{result.ai.sampleOutput}"
                </Text>
              </View>
            ) : null}
          </View>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  input: { borderWidth: 1, borderRadius: 10, padding: 10, marginTop: 12 },
  buttonRow: { flexDirection: "row", marginTop: 12, gap: 8 },
  toggleRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 16,
    marginTop: 12,
  },
  aiList: { gap: 6, marginTop: 8 },
  themeRow: { flexDirection: "row", gap: 8, marginTop: 12, flexWrap: "wrap" },
  themeChoice: {
    flex: 1,
    minWidth: 90,
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderRadius: 10,
    borderWidth: 1.5,
    alignItems: "center",
  },
});
