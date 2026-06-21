/**
 * Terms of Service — plain-English terms for the hosted 508 Agent service.
 *
 * Written to be readable, not lawyer-impressive. The substantive promises in
 * here must stay in sync with what the product actually does:
 *  - credits: non-transferable, never expire (billing.tsx makes this promise)
 *  - certificates: processing records, NOT legal conformance determinations
 *    (mirrors the server-generated claim in stripe_billing.py)
 *  - refunds: unused credit packs within 14 days; subscriptions cancel anytime
 *  - retention: uploaded/remediated artifacts expire after ~24 hours
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
    title: "1. What the service is",
    body: [
      "508 Agent analyzes documents you upload (PDF, Word, PowerPoint) for accessibility issues against WCAG 2.1, Section 508, and PDF/UA criteria, proposes fixes, and (where you approve them) writes a remediated copy of the document for you to download.",
      "Automated checking has limits. The service finds and fixes the issue types listed on each audit report; it does not evaluate every success criterion, and it is not a substitute for human accessibility review or legal advice.",
    ],
  },
  {
    title: "2. Accounts",
    body: [
      "You need an account to run audits beyond your first. You are responsible for activity under your account and for keeping your sign-in credentials private. You must provide a working email address.",
      "You may close your account at any time from Settings; this deletes your stored account data as described in the Privacy page.",
    ],
  },
  {
    title: "3. Credits and subscriptions",
    body: [
      "Remediation runs consume credits (current per-document costs are listed on the Help page). Analysis-only runs are free. Credits are tied to your account, are not transferable, and do not expire.",
      "Subscriptions grant a monthly credit allowance plus plan features (seats, included certificates). Subscriptions renew automatically until cancelled; you can cancel anytime via the billing portal and keep access through the end of the paid period.",
      "If auto top-up (overage) is enabled on your plan, we charge your saved payment method for an additional credit pack when your balance cannot cover a run. You can turn overage off at any time.",
    ],
  },
  {
    title: "4. Refunds",
    body: [
      "Credit packs: refundable within 14 days of purchase if the credits are unused; email support@508-agent.app from your account email.",
      "Subscriptions: cancel anytime; we do not pro-rate partial months, but if you were charged in error or hit a billing bug, contact us and we will make it right.",
      "Charges disputed with your card issuer while credits from that charge have been spent may result in account suspension until resolved.",
    ],
  },
  {
    title: "5. Certificates",
    body: [
      "Conformance certificates are server-generated processing records: they state what the engine detected and remediated for a specific document at a specific time, with a verification link anyone can check.",
      "A certificate is NOT a formal WCAG/Section 508 conformance determination, a legal opinion, or a guarantee that a document is accessible. Do not represent it as one.",
    ],
  },
  {
    title: "6. Your documents",
    body: [
      "You keep all rights to documents you upload. You give us only the limited license needed to process them: parsing, analysis, remediation, and short-term storage so you can download results.",
      "Uploaded and remediated files are automatically deleted from hosted storage after approximately 24 hours. We never use your documents to train AI models.",
      "Do not upload documents you have no right to process, or content that is unlawful to possess or distribute.",
    ],
  },
  {
    title: "7. Acceptable use",
    body: [
      "No attempting to break tenant isolation, probing other accounts' data, reselling the service without an agreement, or automated scraping of the API beyond documented endpoints and rate limits. Security research is welcome under the policy in SECURITY.md — report findings to security@508-agent.app.",
    ],
  },
  {
    title: "8. Service quality and liability",
    body: [
      "The service is provided \"as is\" without warranties. We work hard on accuracy (scores only count fixes that genuinely persist into your file), but we cannot promise the service is error-free or uninterrupted.",
      "To the maximum extent permitted by law, our total liability for any claim related to the service is limited to the amount you paid us in the 12 months before the claim arose. We are not liable for indirect or consequential damages, or for the outcome of any compliance review, audit, or legal proceeding concerning your documents.",
    ],
  },
  {
    title: "9. Changes and termination",
    body: [
      "We may update these terms; material changes will be announced on this page with a new \"last updated\" date, and continued use after that constitutes acceptance.",
      "We may suspend or terminate accounts that violate these terms. If we terminate your account without cause, we will refund unused purchased credits.",
    ],
  },
  {
    title: "10. Contact",
    body: [
      "Questions about these terms: support@508-agent.app. Privacy: privacy@508-agent.app. Security: security@508-agent.app.",
    ],
  },
];

export default function TermsScreen() {
  const theme = useTheme();
  const router = useRouter();
  return (
    <Screen scroll title="Terms of Service">
      <Hero
        eyebrow="LEGAL"
        title="Terms of Service"
        subtitle={`Plain-English terms for the hosted 508 Agent service. Last updated ${LAST_UPDATED}.`}
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
          accessibilityLabel="Privacy policy"
          onPress={() => router.push("/privacy" as any)}
          style={{ color: theme.colors.accent, fontWeight: "600" }}
        >
          Privacy policy
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
