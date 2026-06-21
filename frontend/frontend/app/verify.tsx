/**
 * Public certificate verification page: /verify?cert=<id>
 *
 * Anyone (an auditor, agency, or client) can paste a 508 Agent certificate id
 * here to confirm it is genuine and see what it attests to. Calls the public
 * GET /billing/certificate/{id} endpoint; no sign-in required.
 */

import React, { useEffect, useState } from "react";
import { ActivityIndicator, Image, Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams } from "expo-router";

import { getBackendUrlInfo } from "../src/config/backendUrl";
import { IssuedCertificate, verifyCertificate } from "../src/domain/account";
import { Card } from "../src/ui/components/Card";
import { Hero } from "../src/ui/components/Hero";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";
import { useToast } from "../src/ui/toast";

const OK_GREEN = "#16A34A";

export default function VerifyScreen() {
  const theme = useTheme();
  const toast = useToast();
  const params = useLocalSearchParams<{ cert?: string | string[] }>();
  const certId = Array.isArray(params.cert) ? params.cert[0] : params.cert;

  const copy = (text: string) => {
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard) {
        void navigator.clipboard.writeText(text);
        toast.success("Copied", { description: "Embed snippet copied to your clipboard." });
      }
    } catch {
      toast.error("Couldn't copy", { description: "Select the snippet and copy it manually." });
    }
  };
  const [status, setStatus] = useState<"loading" | "valid" | "invalid">("loading");
  const [cert, setCert] = useState<IssuedCertificate | null>(null);

  useEffect(() => {
    let alive = true;
    if (!certId) {
      setStatus("invalid");
      return;
    }
    setStatus("loading");
    void verifyCertificate(certId).then((c) => {
      if (!alive) return;
      if (c) {
        setCert(c);
        setStatus("valid");
      } else {
        setStatus("invalid");
      }
    });
    return () => {
      alive = false;
    };
  }, [certId]);

  return (
    <Screen scroll title="Verify certificate">
      <Hero
        eyebrow="VERIFICATION"
        title="Certificate verification"
        subtitle="Confirm that a 508 Agent remediation certificate is genuine. A certificate records what was detected and fixed; it is not a formal conformance determination."
      />

      <View style={{ marginTop: 16 }}>
        {status === "loading" ? (
          <Card>
            <View style={styles.row}>
              <ActivityIndicator color={theme.colors.accent} />
              <Text style={{ color: theme.colors.textMuted }}>Checking certificate…</Text>
            </View>
          </Card>
        ) : null}

        {status === "invalid" ? (
          <Card style={{ borderColor: theme.colors.danger, borderWidth: 2 }}>
            <Text style={[theme.typography.pixelLarge, { color: theme.colors.danger }]}>✗ Not a valid certificate</Text>
            <Text style={{ color: theme.colors.textMuted, marginTop: 8, lineHeight: 20 }}>
              {certId
                ? `No certificate was found for id "${certId}".`
                : "No certificate id was provided in the link."}{" "}
              It may be mistyped or was never issued.
            </Text>
          </Card>
        ) : null}

        {status === "valid" && cert ? (
          <>
            <Card style={{ borderColor: OK_GREEN, borderWidth: 2 }}>
              <Text style={[theme.typography.pixelLarge, { color: OK_GREEN }]}>✓ Genuine certificate</Text>
              <Text style={{ color: theme.colors.textMuted, marginTop: 6, marginBottom: 6, lineHeight: 20 }}>
                This certificate was issued by 508 Agent and has not been altered.
              </Text>
              <Row label="Certificate ID" value={cert.certificateId} theme={theme} mono />
              <Row label="Issued to" value={cert.issuedTo || "—"} theme={theme} />
              <Row label="Issued on" value={new Date(cert.issuedAt).toLocaleString()} theme={theme} />
              <Row label="Document" value={cert.filename} theme={theme} mono />
              <Row label="Summary" value={cert.conformanceClaim} theme={theme} />
              <Row label="Score" value={`${cert.score} / 100`} theme={theme} />
              <Row label="Fixes applied" value={String(cert.fixedCount)} theme={theme} />
              <Row label="Items remaining" value={String(cert.remainingCount)} theme={theme} />
            </Card>
            <EmbedBadge certId={cert.certificateId} theme={theme} onCopy={copy} />
          </>
        ) : null}
      </View>
    </Screen>
  );
}

function Row({
  label,
  value,
  theme,
  mono,
}: {
  label: string;
  value: string;
  theme: ReturnType<typeof useTheme>;
  mono?: boolean;
}) {
  return (
    <View style={styles.detailRow}>
      <Text style={{ color: theme.colors.textMuted, fontSize: 12, width: 130 }}>{label}</Text>
      <Text style={{ color: theme.colors.text, fontSize: 13, flex: 1, fontFamily: mono ? "monospace" : undefined }}>
        {value}
      </Text>
    </View>
  );
}

function EmbedBadge({
  certId,
  theme,
  onCopy,
}: {
  certId: string;
  theme: ReturnType<typeof useTheme>;
  onCopy: (text: string) => void;
}) {
  const base = getBackendUrlInfo().url;
  const badgeUrl = `${base}/billing/certificate/${certId}/badge.svg`;
  const origin =
    typeof window !== "undefined" && window.location && window.location.origin
      ? window.location.origin
      : "";
  const verifyUrl = `${origin}/verify?cert=${certId}`;
  const alt = "Accessibility report by 508 Agent";
  const html = `<a href="${verifyUrl}"><img src="${badgeUrl}" alt="${alt}"></a>`;
  const md = `[![${alt}](${badgeUrl})](${verifyUrl})`;

  return (
    <Card style={{ marginTop: 16 }}>
      <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Show it off: embed this badge</Text>
      <Text style={{ color: theme.colors.textMuted, marginTop: 6, lineHeight: 20 }}>
        Add this badge to your website, README, or email signature. It links back to this verification page
        so anyone can confirm the report is genuine.
      </Text>
      {Platform.OS === "web" ? (
        <Image
          source={{ uri: badgeUrl }}
          resizeMode="contain"
          accessibilityLabel="Accessibility badge preview"
          style={{ width: 150, height: 22, marginTop: 14 }}
        />
      ) : null}
      <Snippet label="HTML" value={html} theme={theme} onCopy={onCopy} />
      <Snippet label="Markdown" value={md} theme={theme} onCopy={onCopy} />
    </Card>
  );
}

function Snippet({
  label,
  value,
  theme,
  onCopy,
}: {
  label: string;
  value: string;
  theme: ReturnType<typeof useTheme>;
  onCopy: (text: string) => void;
}) {
  return (
    <View style={{ marginTop: 14 }}>
      <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
        <Text style={{ color: theme.colors.textMuted, fontSize: 12, fontWeight: "700" }}>{label}</Text>
        <Pressable accessibilityRole="button" accessibilityLabel={`Copy ${label} snippet`} onPress={() => onCopy(value)}>
          <Text style={{ color: theme.colors.accent, fontSize: 13, fontWeight: "700" }}>Copy</Text>
        </Pressable>
      </View>
      <View style={[styles.code, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2, borderRadius: theme.radius.xs }]}>
        <Text selectable style={{ color: theme.colors.text, fontFamily: "monospace", fontSize: 12 }}>
          {value}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
  detailRow: { flexDirection: "row", gap: 12, marginTop: 10, alignItems: "flex-start" },
  code: { borderWidth: 1, padding: 10, marginTop: 6 },
});
