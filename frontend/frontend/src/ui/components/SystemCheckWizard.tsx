/**
 * SystemCheckWizard — one-click "is everything working?" self-test.
 *
 * Solves the "I don't know if it's broken" problem: runs the real backend
 * probes (liveness, database readiness, and a genuine end-to-end engine
 * self-test that parses a document and detects an issue on the live server)
 * and renders a clear green/red report, plus what's configured on the
 * deployment (OCR, Stripe, email). Open it from anywhere with
 * `openSystemCheck()`.
 *
 * Portaled to <body> so the overlay can never be trapped by a transformed
 * ancestor.
 */
import React, { useCallback, useEffect, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { useAppStore } from "../../store/useAppStore";
import { useTheme } from "../useTheme";
import { Portal } from "./Portal";

const OPEN_EVENT = "508:open-system-check";

export function openSystemCheck(): void {
  if (typeof window === "undefined") return;
  try {
    window.dispatchEvent(new Event(OPEN_EVENT));
  } catch {}
}

type StepState = "pending" | "running" | "ok" | "fail";

interface StepResult {
  key: string;
  label: string;
  state: StepState;
  detail?: string;
}

interface DeploymentInfo {
  environment?: string;
  appVersion?: string;
  ocrEnabled?: boolean;
  ocrAvailable?: boolean;
  stripeConfigured?: boolean;
  emailConfigured?: boolean;
  error?: string;
}

function _baseUrl(): string {
  try {
    return (useAppStore.getState().apiBaseUrl || "").replace(/\/+$/, "");
  } catch {
    return "";
  }
}

async function _getJson(path: string, timeoutMs = 12000): Promise<any> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(_baseUrl() + path, { signal: ctrl.signal, headers: { Accept: "application/json" } });
    const body = await res.json().catch(() => null);
    return { ok: res.ok, status: res.status, body };
  } finally {
    clearTimeout(t);
  }
}

export function SystemCheckWizard() {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const [steps, setSteps] = useState<StepResult[]>([]);
  const [deployment, setDeployment] = useState<DeploymentInfo | null>(null);
  const [running, setRunning] = useState(false);

  const run = useCallback(async () => {
    setRunning(true);
    setDeployment(null);
    const initial: StepResult[] = [
      { key: "backend", label: "Backend reachable", state: "pending" },
      { key: "database", label: "Database ready", state: "pending" },
      { key: "engine", label: "Engine end-to-end self-test", state: "pending" },
    ];
    setSteps(initial);
    const update = (key: string, patch: Partial<StepResult>) =>
      setSteps((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)));

    // 1) Backend reachable
    update("backend", { state: "running" });
    try {
      const r = await _getJson("/healthz");
      if (r.ok && r.body && (r.body.ok === true || r.body.status === "ok")) {
        update("backend", { state: "ok", detail: "The API server responded." });
      } else {
        update("backend", { state: "fail", detail: `Unexpected response (HTTP ${r.status}).` });
        update("database", { state: "fail", detail: "Skipped: backend not reachable." });
        update("engine", { state: "fail", detail: "Skipped: backend not reachable." });
        setRunning(false);
        return;
      }
    } catch (e: any) {
      update("backend", {
        state: "fail",
        detail:
          "Could not reach the API. Is the backend running, and is the URL in Settings correct?",
      });
      update("database", { state: "fail", detail: "Skipped: backend not reachable." });
      update("engine", { state: "fail", detail: "Skipped: backend not reachable." });
      setRunning(false);
      return;
    }

    // 2) Database ready
    update("database", { state: "running" });
    try {
      const r = await _getJson("/readyz");
      if (r.ok && r.body && r.body.ready === true) {
        update("database", { state: "ok", detail: "The database is connected." });
      } else {
        update("database", { state: "fail", detail: `Not ready (HTTP ${r.status}).` });
      }
    } catch {
      update("database", { state: "fail", detail: "The readiness check did not respond." });
    }

    // 3) Engine end-to-end self-test (+ deployment flags)
    update("engine", { state: "running" });
    try {
      const r = await _getJson("/diagnostics", 20000);
      const checks: any[] = (r.body && r.body.checks) || [];
      setDeployment((r.body && r.body.deployment) || null);
      const pipeline = checks.find((c) => c.name === "pipeline");
      const analyzers = checks.find((c) => c.name === "analyzers");
      const allOk = r.ok && (r.body?.status === "ok") && checks.every((c) => c.ok !== false);
      if (pipeline?.ok && allOk) {
        update("engine", {
          state: "ok",
          detail: `${analyzers?.detail || "Engine wired"} · ${pipeline.detail}`,
        });
      } else if (pipeline?.ok) {
        update("engine", {
          state: "ok",
          detail: `${pipeline.detail} (the AI provider is optional and may be off).`,
        });
      } else {
        update("engine", {
          state: "fail",
          detail: pipeline?.detail || "The engine self-test did not pass.",
        });
      }
    } catch {
      update("engine", { state: "fail", detail: "The diagnostics probe did not respond." });
    }

    setRunning(false);
  }, []);

  useEffect(() => {
    if (Platform.OS !== "web") return;
    const handler = () => {
      setOpen(true);
      void run();
    };
    window.addEventListener(OPEN_EVENT, handler);
    return () => window.removeEventListener(OPEN_EVENT, handler);
  }, [run]);

  if (!open || Platform.OS !== "web") return null;

  const allDone = steps.length > 0 && steps.every((s) => s.state === "ok" || s.state === "fail");
  const allGood = allDone && steps.every((s) => s.state === "ok");
  const anyFail = steps.some((s) => s.state === "fail");

  return (
    <Portal>
      <Pressable
        onPress={() => !running && setOpen(false)}
        accessibilityLabel="System check"
        // @ts-ignore web aria
        accessibilityRole={"dialog" as any}
        style={[styles.dim, { backgroundColor: theme.colors.shadow }]}
      >
        <Pressable
          onPress={(e: any) => e?.stopPropagation && e.stopPropagation()}
          accessibilityLabel="System check content"
          style={[styles.card, { borderRadius: theme.radius.md, backgroundColor: theme.colors.surface, borderColor: theme.colors.border }]}
        >
          <Text accessibilityRole="header" style={[theme.typography.h1, { color: theme.colors.text, fontSize: 22 }]}>
            System check
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 6 }]}>
            Verifies your install is wired up and the engine actually works, end to end, on your
            own server. Nothing is uploaded and no credits are spent.
          </Text>

          {/* Verdict banner */}
          {allDone ? (
            <View
              style={[
                styles.banner,
                {
                  borderRadius: theme.radius.md,
                  borderColor: allGood ? theme.colors.success : theme.colors.danger,
                  backgroundColor: theme.colors.surface2,
                },
              ]}
            >
              <Text style={[theme.typography.body, { color: theme.colors.text, flex: 1, fontWeight: "700" }]}>
                {allGood
                  ? "Everything's working. You're good to go."
                  : anyFail
                    ? "Something needs attention. See the failed step below."
                    : "Check complete."}
              </Text>
            </View>
          ) : null}

          {/* Steps */}
          <View style={{ marginTop: 16, gap: 10 }}>
            {steps.map((s) => (
              <View key={s.key} style={styles.stepRow}>
                <StatusDot state={s.state} theme={theme} />
                <View style={{ flex: 1 }}>
                  <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "700" }]}>
                    {s.label}
                  </Text>
                  {s.detail ? (
                    <Text style={[theme.typography.body, { color: theme.colors.textMuted, fontSize: 13, marginTop: 2 }]}>
                      {s.detail}
                    </Text>
                  ) : null}
                </View>
              </View>
            ))}
          </View>

          {/* Deployment summary */}
          {deployment && !deployment.error ? (
            <View style={[styles.deploy, { borderRadius: theme.radius.md, borderColor: theme.colors.border }]}>
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginBottom: 6 }]}>
                THIS DEPLOYMENT
              </Text>
              <DeployRow label="Environment" value={deployment.environment || "?"} theme={theme} />
              <DeployRow
                label="OCR (scanned PDFs)"
                value={deployment.ocrAvailable ? "Active" : deployment.ocrEnabled ? "Enabled, no Tesseract" : "Off"}
                theme={theme}
              />
              <DeployRow label="Stripe billing" value={deployment.stripeConfigured ? "Configured" : "Not configured"} theme={theme} />
              <DeployRow label="Email (verification/reset)" value={deployment.emailConfigured ? "Configured" : "Not configured"} theme={theme} />
            </View>
          ) : null}

          <View style={styles.actions}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Re-run the system check"
              onPress={() => !running && run()}
              disabled={running}
              style={({ hovered }: any) => [
                styles.ghostBtn,
                { borderRadius: theme.radius.sm, borderColor: theme.colors.border, opacity: running ? 0.4 : 1 },
                hovered && !running ? { opacity: 0.8 } : null,
              ]}
            >
              <Text style={{ color: theme.colors.text, fontWeight: "700", fontSize: 13 }}>
                {running ? "Running…" : "Re-run"}
              </Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Close system check"
              onPress={() => !running && setOpen(false)}
              style={({ hovered }: any) => [
                styles.primaryBtn,
                { borderRadius: theme.radius.sm, backgroundColor: theme.colors.accent },
                hovered ? { opacity: 0.92 } : null,
              ]}
            >
              <Text style={{ color: "#FFFFFF", fontWeight: "800", fontSize: 13 }}>Done</Text>
            </Pressable>
          </View>
        </Pressable>
      </Pressable>
    </Portal>
  );
}

function StatusDot({ state, theme }: { state: StepState; theme: any }) {
  let bg = theme.colors.border;
  let glyph = "";
  if (state === "running") {
    bg = theme.colors.accent;
    glyph = "…";
  } else if (state === "ok") {
    bg = theme.colors.success;
    glyph = "✓";
  } else if (state === "fail") {
    bg = theme.colors.danger;
    glyph = "✕";
  }
  return (
    <View style={[styles.dot, { backgroundColor: bg }]}>
      <Text style={{ color: "#FFFFFF", fontWeight: "800", fontSize: 12 }}>{glyph}</Text>
    </View>
  );
}

function DeployRow({ label, value, theme }: { label: string; value: string; theme: any }) {
  return (
    <View style={styles.deployRow}>
      <Text style={[theme.typography.body, { color: theme.colors.textMuted, fontSize: 13 }]}>{label}</Text>
      <Text style={[theme.typography.body, { color: theme.colors.text, fontSize: 13, fontWeight: "700" }]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  dim: {
    position: (Platform.OS === "web" ? "fixed" : "absolute") as any,
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    zIndex: 10000,
    alignItems: "center",
    justifyContent: "center",
    padding: 16,
  },
  card: {
    width: "100%",
    maxWidth: 520,
    borderWidth: 1,
    padding: 22,
    // @ts-ignore web shadow
    boxShadow: "0 24px 60px rgba(0, 0, 0, 0.35)",
  },
  banner: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    borderWidth: 1,
    padding: 12,
    marginTop: 16,
  },
  stepRow: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  dot: { width: 24, height: 24, borderRadius: 12, alignItems: "center", justifyContent: "center", marginTop: 1 },
  deploy: { borderWidth: 1, padding: 12, marginTop: 16 },
  deployRow: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 3 },
  actions: { flexDirection: "row", alignItems: "center", justifyContent: "flex-end", gap: 10, marginTop: 20 },
  ghostBtn: { borderWidth: 1, paddingVertical: 9, paddingHorizontal: 14 },
  primaryBtn: { paddingVertical: 10, paddingHorizontal: 18 },
});

export default SystemCheckWizard;
