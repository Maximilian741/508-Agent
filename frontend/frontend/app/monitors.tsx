/**
 * Monitored sites — schedule automatic re-scans and get alerted on regressions.
 *
 * We only email when something NEW breaks (never for a clean re-check or for
 * issues that were already there), so the list here doubles as the record of
 * what we're watching and what the last check found.
 */
import { useCallback, useEffect, useState } from "react";
import { Platform, Pressable, PressableStateCallbackType, StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";

import { Monitor, createApiClient } from "../src/api/client";
import { useAppStore } from "../src/store/useAppStore";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { EmptyState } from "../src/ui/components/EmptyState";
import { Hero } from "../src/ui/components/Hero";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

function whenLabel(iso?: string | null): string {
  if (!iso) return "not yet";
  const t = Date.parse(iso.endsWith("Z") ? iso : `${iso}Z`);
  if (!Number.isFinite(t)) return "not yet";
  const mins = Math.round((t - Date.now()) / 60000);
  const abs = Math.abs(mins);
  const unit = abs < 60 ? `${abs} min` : abs < 1440 ? `${Math.round(abs / 60)} hr` : `${Math.round(abs / 1440)} day`;
  return mins >= 0 ? `in ${unit}` : `${unit} ago`;
}

export default function MonitorsScreen() {
  const theme = useTheme();
  const router = useRouter();
  const apiBaseUrl = useAppStore((s) => s.apiBaseUrl);
  const mockMode = useAppStore((s) => s.mockMode);

  const [monitors, setMonitors] = useState<Monitor[]>([]);
  const [url, setUrl] = useState("");
  const [email, setEmail] = useState("");
  const [frequency, setFrequency] = useState<"daily" | "weekly">("weekly");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const client = useCallback(
    () => createApiClient({ baseUrl: apiBaseUrl, mockMode }),
    [apiBaseUrl, mockMode],
  );

  const refresh = useCallback(async () => {
    try {
      setMonitors(await client().listMonitors());
      setError(null);
    } catch (e) {
      const err = e as Error & { status?: number };
      if (err.status === 401) setError("Please sign in to manage monitored pages.");
      else setError(err.message || "Could not load your monitors.");
    } finally {
      setLoaded(true);
    }
  }, [client]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const add = async () => {
    if (!url.trim()) {
      setError("Enter the URL you want us to watch.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await client().createMonitor(url.trim(), frequency, email.trim());
      setUrl("");
      await refresh();
    } catch (e) {
      setError((e as Error).message || "Could not start monitoring that URL.");
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (m: Monitor) => {
    try {
      await client().updateMonitor(m.id, { enabled: !m.enabled });
      await refresh();
    } catch (e) {
      setError((e as Error).message || "Could not update that monitor.");
    }
  };

  const remove = async (m: Monitor) => {
    try {
      await client().deleteMonitor(m.id);
      await refresh();
    } catch (e) {
      setError((e as Error).message || "Could not remove that monitor.");
    }
  };

  const focusRing = ({ focused }: PressableStateCallbackType & { focused?: boolean }) =>
    focused ? { outlineWidth: 2, outlineColor: theme.colors.accent, outlineStyle: "solid" as const } : null;

  return (
    <Screen scroll>
      <Hero
        eyebrow="Stay compliant"
        title="Monitored pages"
        subtitle="We re-check these on a schedule and email you only when something NEW breaks — never for a clean check or issues you already knew about."
      />

      <Card variant="content" style={{ gap: 12 }}>
        <Text style={[styles.label, { color: theme.colors.text }]}>Page URL</Text>
        <TextInput
          value={url}
          onChangeText={setUrl}
          placeholder="https://example.com/important-page"
          placeholderTextColor={theme.colors.textMuted}
          autoCapitalize="none"
          autoCorrect={false}
          accessibilityLabel="URL to monitor"
          style={[styles.input, { color: theme.colors.text, borderColor: theme.colors.border, backgroundColor: theme.colors.surface }]}
        />
        <Text style={[styles.label, { color: theme.colors.text }]}>Alert email</Text>
        <TextInput
          value={email}
          onChangeText={setEmail}
          placeholder="you@example.com"
          placeholderTextColor={theme.colors.textMuted}
          autoCapitalize="none"
          autoCorrect={false}
          accessibilityLabel="Email address for alerts"
          style={[styles.input, { color: theme.colors.text, borderColor: theme.colors.border, backgroundColor: theme.colors.surface }]}
        />

        <View style={[styles.freqRow, { borderColor: theme.colors.border }]} accessibilityRole="radiogroup" accessibilityLabel="Check frequency">
          {(["daily", "weekly"] as const).map((f) => {
            const active = frequency === f;
            return (
              <Pressable
                key={f}
                onPress={() => setFrequency(f)}
                accessibilityRole="radio"
                accessibilityState={{ selected: active }}
                accessibilityLabel={`Check ${f}`}
                style={(s) => [styles.freqBtn, active && { backgroundColor: theme.colors.surface3 }, focusRing(s)]}
              >
                <Text style={[styles.freqText, { color: active ? theme.colors.text : theme.colors.textMuted, fontWeight: active ? "700" : "500" }]}>
                  {f === "daily" ? "Daily" : "Weekly"}
                </Text>
              </Pressable>
            );
          })}
        </View>

        <Button title={busy ? "Adding…" : "Watch this page"} onPress={add} disabled={busy} />
        {error ? <InlineNotice tone="danger" title="Couldn't do that" message={error} /> : null}
      </Card>

      {loaded && monitors.length === 0 && !error ? (
        <View style={{ marginTop: 16 }}>
          <EmptyState
            title="Nothing monitored yet"
            message="Add a page above and we'll re-check it automatically, then email you the moment a new accessibility issue appears."
          />
        </View>
      ) : null}

      {monitors.map((m) => (
        <Card key={m.id} variant="data" style={{ gap: 8, marginTop: 12 }}>
          <View style={styles.rowBetween}>
            <Text style={[styles.url, { color: theme.colors.text }]} numberOfLines={1}>
              {m.url}
            </Text>
            <Text style={[styles.badge, { color: m.enabled ? theme.colors.accent : theme.colors.textMuted, borderColor: m.enabled ? theme.colors.accent : theme.colors.border }]}>
              {m.enabled ? m.frequency.toUpperCase() : "PAUSED"}
            </Text>
          </View>
          <Text style={[styles.meta, { color: theme.colors.textMuted }]}>
            {m.lastRunAt ? `Last checked ${whenLabel(m.lastRunAt)} · ${m.lastStatus || `${m.lastIssueCount} issue(s)`}` : "Not checked yet"}
            {m.enabled ? ` · next ${whenLabel(m.nextRunAt)}` : ""}
          </Text>
          {m.notifyEmail ? (
            <Text style={[styles.meta, { color: theme.colors.textMuted }]}>Alerts to {m.notifyEmail}</Text>
          ) : (
            <Text style={[styles.meta, { color: theme.colors.textMuted }]}>
              No alert email set — add one to be notified when something breaks.
            </Text>
          )}
          <View style={styles.actions}>
            <Button title={m.enabled ? "Pause" : "Resume"} variant="secondary" onPress={() => toggle(m)} />
            <Button title="Remove" variant="secondary" onPress={() => remove(m)} />
          </View>
        </Card>
      ))}

      <View style={{ marginTop: 20 }}>
        <Button title="Scan a page now" variant="secondary" onPress={() => router.push("/scan-url")} />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  label: { fontSize: 14, fontWeight: "600" },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 15 },
  freqRow: { flexDirection: "row", borderWidth: 1, borderRadius: 8, overflow: "hidden", alignSelf: "flex-start" },
  freqBtn: { paddingVertical: 8, paddingHorizontal: 16 },
  freqText: { fontSize: 14 },
  rowBetween: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  url: { fontSize: 15, fontWeight: "700", flex: 1 },
  badge: { fontSize: 10, fontWeight: "800", letterSpacing: 0.6, borderWidth: 1, borderRadius: 4, paddingHorizontal: 6, paddingVertical: 2 },
  meta: { fontSize: 12 },
  actions: { flexDirection: "row", gap: 8, marginTop: 4, flexWrap: "wrap" },
});
