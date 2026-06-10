/**
 * Public certificate verification page: /verify?cert=<id>
 *
 * Anyone (an auditor, agency, or client) can paste a 508 Agent certificate id
 * here to confirm it is genuine and see what it attests to. Calls the public
 * GET /billing/certificate/{id} endpoint; no sign-in required.
 */

import React, { useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams } from "expo-router";

import { IssuedCertificate, verifyCertificate } from "../src/domain/account";
import { Card } from "../src/ui/components/Card";
import { Hero } from "../src/ui/components/Hero";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

const OK_GREEN = "#16A34A";

export default function VerifyScreen() {
  const theme = useTheme();
  const params = useLocalSearchParams<{ cert?: string | string[] }>();
  const certId = Array.isArray(params.cert) ? params.cert[0] : params.cert;
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
        shader="ember"
        eyebrow="VERIFICATION"
        title="Certificate verification"
        subtitle="Confirm that a 508 Agent remediation certificate is genuine. A certificate records what was detected and fixed — it is not a formal conformance determination."
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

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
  detailRow: { flexDirection: "row", gap: 12, marginTop: 10, alignItems: "flex-start" },
});
