/**
 * Privacy Policy — what we store, for how long, and what we never do.
 *
 * The substantive claims here must stay true to the code:
 *  - documents: deleted from hosted storage after ~24h (cleanup task +
 *    PIPELINE_ARTIFACT_TTL_SECONDS; R2 lifecycle rule per LAUNCH.md)
 *  - passwords: salted scrypt hashes, never plaintext
 *  - no AI training on customer documents
 *  - GDPR-style self-service: export + delete from Settings
 */
import { StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { Card } from "../src/ui/components/Card";
import { Screen } from "../src/ui/components/Screen";
import { Hero } from "../src/ui/components/Hero";
import { useTheme } from "../src/ui/useTheme";

const LAST_UPDATED = "June 2026";

const SECTIONS: { title: string; body: string[] }[] = [
  {
    title: "Documents you upload",
    body: [
      "Your documents are processed to produce the audit and the remediated copy, then automatically deleted from hosted storage after approximately 24 hours. They are not shared with other customers, not sold, and never used to train AI models.",
      "When AI-assisted suggestions are enabled (for example alt-text generation), the relevant snippet or image is sent to our AI provider (Anthropic) solely to generate the suggestion, subject to their no-training API terms. With AI disabled, everything runs on our servers.",
    ],
  },
  {
    title: "Account data we keep",
    body: [
      "Email address, display name, password hash (salted scrypt, which we cannot read), credit balance and transaction history, subscription state, team membership, issued certificates (document name, score, issue date), and audit-history metadata needed to show your dashboard.",
      "Operational logs (request IDs, timestamps, status codes) are kept for debugging and abuse prevention. We do not log document contents.",
    ],
  },
  {
    title: "Certificates are shareable by design",
    body: [
      "When you issue a conformance certificate, anyone with its verification link can see the certificate's validity, score, claim text, issue date, file type, and a masked version of the issuing email (for example a***@example.com). The full filename and your full email are never shown publicly.",
    ],
  },
  {
    title: "Payments",
    body: [
      "Payments are processed by Stripe. Your card number never touches our servers; we store only Stripe's customer/subscription identifiers and charge outcomes.",
    ],
  },
  {
    title: "Cookies and tracking",
    body: [
      "The app stores your session token in your browser (localStorage or sessionStorage, your choice at sign-in). There are no third-party advertising trackers.",
    ],
  },
  {
    title: "Your controls",
    body: [
      "Export your account data or delete your account entirely from your Account page. Deletion removes your account record, credit ledger, history, and any stored artifacts. Email privacy@508-agent.app for anything else; we respond within 7 days.",
    ],
  },
  {
    title: "Where data lives",
    body: [
      "Hosted infrastructure runs behind Cloudflare (TLS, WAF) with application servers and storage operated by us. Backups of the account database are retained on a rolling basis; expired document artifacts are not included in backups.",
    ],
  },
];

export default function PrivacyScreen() {
  const theme = useTheme();
  const router = useRouter();
  return (
    <Screen scroll title="Privacy">
      <Hero
        eyebrow="LEGAL"
        title="Privacy policy"
        subtitle={`What we store, for how long, and what we never do. Last updated ${LAST_UPDATED}.`}
      />
      {SECTIONS.map((s) => (
        <Card key={s.title}>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>{s.title}</Text>
          {s.body.map((p, i) => (
            <Text
              key={i}
              style={[
                theme.typography.body,
                { color: theme.colors.textMuted, marginTop: i === 0 ? 8 : 6, lineHeight: 21 },
              ]}
            >
              {p}
            </Text>
          ))}
        </Card>
      ))}
      <View style={styles.footer}>
        <Text
          accessibilityRole="button"
          accessibilityLabel="Terms of service"
          onPress={() => router.push("/terms" as any)}
          style={{ color: theme.colors.accent, fontWeight: "600" }}
        >
          Terms of service
        </Text>
        <Text style={{ color: theme.colors.textMuted, opacity: 0.5 }}>·</Text>
        <Text
          accessibilityRole="button"
          accessibilityLabel="Back to home"
          onPress={() => router.push("/" as any)}
          style={{ color: theme.colors.accent, fontWeight: "600" }}
        >
          Home
        </Text>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  footer: {
    flexDirection: "row",
    gap: 12,
    justifyContent: "center",
    paddingTop: 12,
    paddingBottom: 24,
  },
});
