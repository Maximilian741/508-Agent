/**
 * RecentRemediations — the recovery path for a lost download.
 *
 * /pipeline/remediate is synchronous, and its response used to be the ONLY
 * place the download URL lived. Close the tab, or have the proxy cut a long
 * request at its timeout, and the fixed file was written, sitting on disk,
 * charged for — and unreachable. GET /pipeline/jobs lists the caller's recent
 * remediations with FRESH signed URLs, and this renders it.
 *
 * It also shows when a charge is still pending: if the client had already
 * disconnected when the server reached the charge, no credit was taken then —
 * it is taken on first download instead, exactly once. Saying so here is the
 * honest thing; the user should never be surprised by a debit.
 *
 * Self-contained (owns its client + fetch) so the dashboard mounts it in two
 * lines and never re-renders on its account.
 */
import { useEffect, useMemo, useState } from "react";
import { Platform, StyleSheet, Text, View } from "react-native";

import { createApiClient, type PipelineJob } from "../../api/client";
import { useAppStore } from "../../store/useAppStore";
import { useTheme } from "../useTheme";
import { Button } from "./Button";
import { Card } from "./Card";
import { Chip } from "./Chip";

function _download(url: string, filename?: string) {
  // Same-tab anchor click, not window.open(): a window.open() after any await
  // is treated as a non-gesture popup and blocked; the file endpoint serves
  // Content-Disposition: attachment, so the anchor downloads in place.
  if (Platform.OS !== "web" || typeof document === "undefined") {
    try {
      window.open(url, "_blank");
    } catch {
      /* native no-op */
    }
    return;
  }
  const a = document.createElement("a");
  a.href = url;
  if (filename) a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function _when(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function RecentRemediations() {
  const theme = useTheme();
  const apiBaseUrl = useAppStore((s) => s.apiBaseUrl);
  const mockMode = useAppStore((s) => s.mockMode);
  const client = useMemo(() => createApiClient({ baseUrl: apiBaseUrl, mockMode }), [apiBaseUrl, mockMode]);
  const [jobs, setJobs] = useState<PipelineJob[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    client
      .listJobs()
      .then((list) => {
        if (!cancelled) setJobs(list);
      })
      .catch((e: Error) => {
        if (!cancelled) {
          setJobs([]);
          setError(e?.message || "Could not load your recent remediations.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [client]);

  // Nothing to show and nothing went wrong: stay out of the way entirely.
  if (jobs !== null && jobs.length === 0 && !error) return null;

  return (
    <Card>
      <View style={styles.headerRow}>
        <View style={{ flexShrink: 1 }}>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Recent remediations</Text>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 2 }]}>
            Fixed files from your last remediations, in case a download did not reach you.
          </Text>
        </View>
      </View>

      {error ? (
        <Text style={[theme.typography.body, { color: theme.colors.danger, marginTop: 10 }]}>{error}</Text>
      ) : null}

      {jobs === null ? (
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 10 }]}>Loading…</Text>
      ) : (
        <View style={{ marginTop: 10, gap: 8 }}>
          {jobs.map((j) => (
            <View
              key={j.jobId}
              style={[styles.row, { borderColor: theme.colors.border, borderRadius: theme.radius.sm }]}
            >
              <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
                <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "700" }]} numberOfLines={1}>
                  {j.filename}
                </Text>
                <View style={styles.metaRow}>
                  <Chip label={(j.sourceFormat || "").toUpperCase() || "FILE"} tone="default" />
                  {_when(j.createdAt) ? (
                    <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>{_when(j.createdAt)}</Text>
                  ) : null}
                  {j.persistedFixes > 0 ? (
                    <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                      {j.persistedFixes} fix{j.persistedFixes === 1 ? "" : "es"} applied
                    </Text>
                  ) : null}
                  {j.chargePending ? (
                    <Chip label="Charged on download" tone="warning" />
                  ) : j.charged ? (
                    <Chip label="Paid" tone="success" />
                  ) : (
                    <Chip label="No charge" tone="default" />
                  )}
                </View>
              </View>
              <Button
                title="Download"
                variant="secondary"
                accessibilityLabel={`Download ${j.filename}`}
                onPress={() => _download(j.downloadUrl, j.filename)}
              />
            </View>
          ))}
        </View>
      )}
    </Card>
  );
}

const styles = StyleSheet.create({
  headerRow: { flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: 12 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  metaRow: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
});
