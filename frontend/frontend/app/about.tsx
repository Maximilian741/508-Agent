/**
 * About / Privacy screen.
 *
 * Concise statement of how the app handles documents, data, and AI usage.
 * Compliance and security reviewers ask for this constantly; making it
 * available in-app saves an email thread.
 */

import { Linking, Pressable, StyleSheet, Text, View } from "react-native";

import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { Hero } from "../src/ui/components/Hero";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

export default function AboutScreen() {
  const theme = useTheme();

  return (
    <Screen scroll title="About">
      <Hero
        eyebrow="About 508 Agent"
        title="Built for remediators, not bureaucrats"
        subtitle="An open-source accessibility auditor that runs on your machine. We don't collect telemetry, we don't ship your documents anywhere, and every fix the agent applies is recorded in an audit log you can export."
      />

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>How your data is handled</Text>
        <View style={styles.list}>
          <Bullet
            label="Local-first"
            body="Documents are parsed and analyzed on the same machine running the backend. No third-party uploads happen unless you've explicitly opted into an AI provider (and even then only the parts of the document needed for the request)."
          />
          <Bullet
            label="No telemetry"
            body="The frontend doesn't phone home. The backend doesn't either. If you see a network request go anywhere other than your own analyzer URL, that's a bug — please report it."
          />
          <Bullet
            label="Storage is yours"
            body="Default storage is a SQLite file in backend/.runtime/. S3 storage is opt-in and configured by you via STORAGE_PROVIDER=s3. Original and remediated artifacts never leave your environment."
          />
          <Bullet
            label="AI is opt-in"
            body="Without ANTHROPIC_API_KEY or OPENAI_API_KEY set in the backend's environment, the app uses local heuristics only. With a key set, alt-text and link-text suggestions are sent to that provider — but only the relevant snippet, never the full document."
          />
          <Bullet
            label="Audit log is exportable"
            body="Every approve / reject / edit decision is recorded with a timestamp. The HTML report you export at the end of an audit is the canonical record — keep a copy with your compliance documentation."
          />
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
          <Pressable onPress={() => Linking.openURL("https://www.w3.org/TR/WCAG21/")}>
            <Chip label="WCAG 2.1 ↗" tone="info" />
          </Pressable>
          <Pressable onPress={() => Linking.openURL("https://www.access-board.gov/ict/")}>
            <Chip label="Section 508 ↗" tone="info" />
          </Pressable>
          <Pressable onPress={() => Linking.openURL("https://www.w3.org/TR/WCAG21-TECHS/pdf.html")}>
            <Chip label="PDF/UA ↗" tone="info" />
          </Pressable>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 12 }]}>
          The full mapping of every flag this tool detects to its underlying WCAG / §508 / PDF/UA
          criterion is on the Help & Glossary screen.
        </Text>
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

const styles = StyleSheet.create({
  list: { gap: 14, marginTop: 12 },
  bullet: { flexDirection: "row", gap: 12, alignItems: "flex-start" },
  bulletDot: { width: 8, height: 8, borderRadius: 4, marginTop: 8 },
  twoCol: { flexDirection: "row", gap: 12, flexWrap: "wrap", marginTop: 12 },
  col: { flex: 1, minWidth: 240, borderWidth: 1, borderRadius: 12, padding: 12, gap: 6 },
  standardsRow: { flexDirection: "row", gap: 6, flexWrap: "wrap", marginTop: 8 },
});
