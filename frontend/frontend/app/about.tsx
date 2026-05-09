/**
 * About / Privacy screen.
 *
 * Concise statement of how the app handles documents, data, and AI usage.
 * Updated to reflect two operational modes — self-hosted (your machine) and
 * the managed 508-agent.app deployment — so reviewers know exactly which
 * surface they're looking at.
 */

import { Linking, Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { Hero } from "../src/ui/components/Hero";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

export default function AboutScreen() {
  const theme = useTheme();
  const router = useRouter();

  return (
    <Screen scroll title="About">
      <Hero
        shader="aurora"
        eyebrow="ABOUT"
        title="Built for remediators, not bureaucrats"
        subtitle="An accessibility auditor that ships in two flavors: a self-hosted package you run on your own machine, and 508-agent.app - a managed deployment locked behind Cloudflare Access. Both run the same code; they differ in where your files live."
      />

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>How your data is handled</Text>

        <View style={styles.modeRow}>
          <View style={[styles.modeCol, { borderColor: theme.colors.success }]}>
            <Chip label="Self-hosted (your machine)" tone="success" />
            <Bullet
              label="Local-first"
              body="Documents are parsed and analyzed on the same machine running the backend. Nothing leaves your network unless you've explicitly opted into an AI provider."
            />
            <Bullet
              label="Storage is yours"
              body="Default storage is a SQLite file in backend/.runtime/. S3 / R2 storage is opt-in and configured by you."
            />
            <Bullet
              label="No telemetry"
              body="The frontend doesn't phone home. The backend doesn't either. If you see a network request go anywhere other than your own analyzer URL, that's a bug."
            />
          </View>

          <View style={[styles.modeCol, { borderColor: theme.colors.info }]}>
            <Chip label="508-agent.app (managed)" tone="info" />
            <Bullet
              label="Documents on our infrastructure"
              body="When using 508-agent.app, your documents land on Cloudflare R2, encrypted at rest with SSE."
            />
            <Bullet
              label="24-hour retention"
              body="Documents are deleted after 24 hours. Remediated artifacts and analysis metadata may be retained longer for your audit history; the source document goes away."
            />
            <Bullet
              label="Cloudflare Access auth"
              body="Authentication is enforced via Cloudflare Access. We never see your password — Cloudflare hands us a signed JWT on every request."
            />
            <Bullet
              label="Append-only audit log"
              body="Every analyze, remediate, share, view, and download is recorded against your account. You can request a copy at any time, and admins can browse it from the in-app Admin screen."
            />
            <Bullet
              label="AI is opt-in and minimal"
              body="When AI is enabled, the provider only receives the snippet needed for the request — never the full document."
            />
          </View>
        </View>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Trust posture</Text>
        <View style={styles.trustGrid}>
          <TrustClaim label="TLS 1.3 everywhere" body="All managed-mode traffic is TLS 1.3, terminated at Cloudflare's edge in front of the backend." />
          <TrustClaim label="HMAC-signed download URLs" body="Remediated-file URLs are signed with a 1-hour TTL. Tampering or expiry returns 403/410." />
          <TrustClaim label="Strict CSP / HSTS / X-Frame-Options DENY" body="Set on every response by SecurityHeadersMiddleware. No inline scripts; no third-party origins." />
          <TrustClaim label="No third-party analytics" body="Zero pixel trackers. Zero ad networks. Zero feature-flag SDKs phoning home." />
          <TrustClaim label="Source-available" body="Read the code that handles your documents in the GitHub repository." />
        </View>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>What this tool does (and doesn't)</Text>
        <View style={styles.twoCol}>
          <View style={[styles.col, { borderColor: theme.colors.success }]}>
            <Chip label="Does" tone="success" />
            <Text style={[theme.typography.body, { color: theme.colors.text }]}>
              {"• Detects WCAG 2.1, §508, and PDF/UA structural issues."}
            </Text>
            <Text style={[theme.typography.body, { color: theme.colors.text }]}>
              {"• Suggests fixes — deterministic where safe, AI-assisted where context matters."}
            </Text>
            <Text style={[theme.typography.body, { color: theme.colors.text }]}>
              {"• Walks you through every finding with a one-at-a-time review queue."}
            </Text>
            <Text style={[theme.typography.body, { color: theme.colors.text }]}>
              {"• Produces a printable conformance report and a downloadable remediated file."}
            </Text>
          </View>
          <View style={[styles.col, { borderColor: theme.colors.warning }]}>
            <Chip label="Doesn't" tone="warning" />
            <Text style={[theme.typography.body, { color: theme.colors.text }]}>
              {"• Replace human judgement on alt text, link rewrites, or reading order in complex layouts."}
            </Text>
            <Text style={[theme.typography.body, { color: theme.colors.text }]}>
              {"• Catch every accessibility issue — visual contrast, keyboard navigation in interactive PDFs, etc. require additional review."}
            </Text>
            <Text style={[theme.typography.body, { color: theme.colors.text }]}>
              {"• Make legal claims about WCAG conformance on your behalf — the report is a record of work, not a legal certification."}
            </Text>
          </View>
        </View>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Standards we evaluate</Text>
        <View style={styles.standardsRow}>
          <Pressable accessibilityRole="button" accessibilityLabel="Open external link www.w3.org" onPress={() => Linking.openURL("https://www.w3.org/TR/WCAG21/")}>
            <Chip label="WCAG 2.1 ↗" tone="info" />
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Open external link www.access-board.gov" onPress={() => Linking.openURL("https://www.access-board.gov/ict/")}>
            <Chip label="Section 508 ↗" tone="info" />
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Open external link www.w3.org" onPress={() => Linking.openURL("https://www.w3.org/TR/WCAG21-TECHS/pdf.html")}>
            <Chip label="PDF/UA ↗" tone="info" />
          </Pressable>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 12 }]}>
          The full mapping of every flag this tool detects to its underlying WCAG / §508 / PDF/UA
          criterion is on the Help & Glossary screen.
        </Text>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Data subject rights</Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8 }]}>
          {"You can request a copy of your audit log, request deletion of your account's data, or ask a question about how your documents are handled."}
        </Text>
        <View style={[styles.standardsRow, { marginTop: 8 }]}>
          <Pressable accessibilityRole="button" accessibilityLabel="Go to admin" onPress={() => router.push("/admin" as any)}>
            <Chip label="Admins: open Admin screen" tone="info" />
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Email privacy@508-agent.app" onPress={() => Linking.openURL("mailto:privacy@508-agent.app")}>
            <Chip label="Email privacy@508-agent.app" tone="default" />
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel="Go to security" onPress={() => router.push("/security" as any)}>
            <Chip label="Read the Security page" tone="default" />
          </Pressable>
        </View>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Reporting issues</Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Found a bug, a false-positive, or a missing standard? Open an issue in the project
          repository. Include the input document type, the rule code that misfired, and (if
          shareable) a redacted sample.
        </Text>
      </Card>
    </Screen>
  );
}

function Bullet({ label, body }: { label: string; body: string }) {
  const theme = useTheme();
  return (
    <View style={styles.bullet}>
      <View style={[styles.bulletDot, { backgroundColor: theme.colors.accent }]} />
      <View style={{ flex: 1 }}>
        <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 15 }]}>
          {label}
        </Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 4 }]}>
          {body}
        </Text>
      </View>
    </View>
  );
}

function TrustClaim({ label, body }: { label: string; body: string }) {
  const theme = useTheme();
  return (
    <View style={[styles.trustCard, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 }]}>
      <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 14 }]}>{label}</Text>
      <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 4, fontSize: 13 }]}>
        {body}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  list: { gap: 14, marginTop: 12 },
  bullet: { flexDirection: "row", gap: 12, alignItems: "flex-start" },
  bulletDot: { width: 8, height: 8, borderRadius: 4, marginTop: 8 },
  twoCol: { flexDirection: "row", gap: 12, flexWrap: "wrap", marginTop: 12 },
  col: { flex: 1, minWidth: 240, borderWidth: 1, borderRadius: 12, padding: 12, gap: 6 },
  modeRow: { flexDirection: "row", gap: 12, flexWrap: "wrap", marginTop: 12 },
  modeCol: { flex: 1, minWidth: 280, borderWidth: 1, borderRadius: 12, padding: 12, gap: 10 },
  trustGrid: { flexDirection: "row", gap: 10, flexWrap: "wrap", marginTop: 12 },
  trustCard: {
    flexBasis: "48%",
    flexGrow: 1,
    minWidth: 220,
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
  },
  standardsRow: { flexDirection: "row", gap: 6, flexWrap: "wrap", marginTop: 8 },
});
