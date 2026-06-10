/**
 * Security one-pager.
 *
 * Plain-language overview of how the managed deployment of 508 Agent
 * (508-agent.app) handles authentication, encryption, audit logging,
 * retention, and incident response.  Linked from the About page.
 *
 * No marketing fluff — every claim should map to something a reviewer can
 * verify in the source tree or in the deployed configuration.
 */

import { Linking, Pressable, StyleSheet, Text, View } from "react-native";

import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { Hero } from "../src/ui/components/Hero";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

export default function SecurityScreen() {
  const theme = useTheme();

  return (
    <Screen scroll title="Security">
      <Hero
        shader="nebula"
        eyebrow="SECURITY"
        title="How 508-agent.app keeps your documents safe"
        subtitle="Plain-language summary of the controls behind the managed deployment. Every claim below corresponds to code in the open repository or to a configuration item you can ask us to share."
      />

      <Card>
        <Section title="1. Authentication">
          <Para text="You sign in with an email address and password handled by our own backend. Passwords are hashed with salted scrypt (N=2^15) before they touch the database — we store and compare only the hash, never the plaintext, and passwords never appear in logs." />
          <Para text="A successful sign-in issues an HS256-signed session JWT with issuer/audience checks and a 7-day lifetime. Every API request is authenticated against that token; your documents are owned by your account, and requests for someone else's records return 404." />
          <Para text="Cloudflare sits in front of the application for TLS termination and WAF/edge protection. Failed sign-ins and admin actions are recorded in the audit log so operators can spot brute-force attempts. (Private/internal deployments can additionally enable Cloudflare Access SSO; the public service uses app-native accounts.)" />
        </Section>
      </Card>

      <Card>
        <Section title="2. Encryption in transit">
          <Para text="All client traffic terminates at Cloudflare's edge over TLS 1.3. The connection from Cloudflare to the origin backend is also TLS-only." />
          <Para text="Strict-Transport-Security is set with max-age=63072000, includeSubDomains, preload — once your browser has loaded 508-agent.app it will refuse to ever talk to it over plaintext." />
        </Section>
      </Card>

      <Card>
        <Section title="3. Encryption at rest">
          <Para text="Documents and remediated artifacts are stored on Cloudflare R2 with server-side encryption (SSE) enabled by default at the bucket level." />
          <Para text="Audit log rows live in the same SQLite or Postgres database the rest of the application uses. Postgres deployments rely on the cloud provider's at-rest encryption; SQLite deployments rely on the host disk's filesystem encryption." />
          <Para text="Application secrets (HMAC signing key, AI provider tokens) are never written to logs and are sourced from environment variables managed by the deploy platform." />
        </Section>
      </Card>

      <Card>
        <Section title="4. Audit logging">
          <Para text="An append-only audit_log table records analyze, remediate, credit-grant, certificate, download, manual-review-resolve, and auth-fail events." />
          <Para text="Every entry stores the request id (cross-referencable with application logs), the actor's email, the source IP, the doc / job id, and a small JSON metadata blob — never document content or user-supplied text." />
          <Para text="The only mutation path is purge-older-than, gated to operator-defined admin emails, and that purge itself is audited." />
        </Section>
      </Card>

      <Card>
        <Section title="5. Retention">
          <View style={styles.retentionGrid}>
            <Retention label="Source documents" body="Swept from pipeline storage after ~24 hours (PIPELINE_ARTIFACT_TTL); object storage carries a matching 1-day lifecycle rule." />
            <Retention label="Remediated artifacts" body="Same ~24-hour window — download your file promptly; re-upload the source to regenerate it later." />
            <Retention label="Signed download URLs" body="HMAC-signed with an expiry. Tampering or expiry returns 403/410." />
            <Retention label="Account data" body="Email, credit ledger, history metadata — kept until you delete your account from Settings." />
            <Retention label="Audit log" body="Retained for the operator-configured retention window. Admins can purge older entries, and the purge itself is audited." />
          </View>
        </Section>
      </Card>

      <Card>
        <Section title="6. Vendors">
          <View style={styles.vendorRow}>
            <VendorChip name="Cloudflare" subtitle="Edge network, TLS termination, WAF, R2 storage" />
            <VendorChip name="Stripe" subtitle="Payment processing. Card numbers never touch our servers; we store only Stripe's customer/subscription identifiers." />
            <VendorChip name="Anthropic" subtitle="OPTIONAL. Used only when AI alt-text / link-text is enabled. Receives only the snippet the request needs." />
            <VendorChip name="OpenAI" subtitle="OPTIONAL. Same scope as Anthropic. Disabled unless OPENAI_API_KEY is configured." />
          </View>
          <Para text="No analytics SDKs, no advertising pixels, no third-party feature-flag services. The frontend talks only to the backend and (optionally) to the AI provider you configured." />
        </Section>
      </Card>

      <Card>
        <Section title="7. Incident response">
          <Para text="Suspected security issues should be reported to security@508-agent.app." />
          <Para text="On confirmed incidents, we (a) revoke and rotate any keys that may have been exposed, (b) draft a public timeline of what happened and what we did about it, (c) notify affected accounts within 72 hours, and (d) file a follow-up audit log purge if user data may have leaked." />
          <Para text="The public timeline lives in the GitHub repository under SECURITY.md so the history of disclosures is permanent and reviewable." />
        </Section>
        <Pressable accessibilityRole="button" accessibilityLabel="Email security@508-agent.app" onPress={() => Linking.openURL("mailto:security@508-agent.app")}>
          <Chip label="Email security@508-agent.app" tone="info" />
        </Pressable>
      </Card>
    </Screen>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const theme = useTheme();
  return (
    <View style={styles.section}>
      <Text style={[theme.typography.h2, { color: theme.colors.text }]}>{title}</Text>
      <View style={styles.sectionBody}>{children}</View>
    </View>
  );
}

function Para({ text }: { text: string }) {
  const theme = useTheme();
  return (
    <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8 }]}>
      {text}
    </Text>
  );
}

function Retention({ label, body }: { label: string; body: string }) {
  const theme = useTheme();
  return (
    <View
      style={[
        styles.retentionCard,
        { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 },
      ]}
    >
      <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 14 }]}>{label}</Text>
      <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 4, fontSize: 13 }]}>
        {body}
      </Text>
    </View>
  );
}

function VendorChip({ name, subtitle }: { name: string; subtitle: string }) {
  const theme = useTheme();
  return (
    <View
      style={[
        styles.vendorCard,
        { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 },
      ]}
    >
      <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 14 }]}>{name}</Text>
      <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 4, fontSize: 13 }]}>
        {subtitle}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  section: { gap: 4 },
  sectionBody: { marginTop: 4 },
  retentionGrid: { flexDirection: "row", gap: 10, flexWrap: "wrap", marginTop: 12 },
  retentionCard: {
    flexBasis: "48%",
    flexGrow: 1,
    minWidth: 220,
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
  },
  vendorRow: { flexDirection: "row", gap: 10, flexWrap: "wrap", marginTop: 12 },
  vendorCard: {
    flexBasis: "32%",
    flexGrow: 1,
    minWidth: 220,
    borderWidth: 1,
    borderRadius: 10,
    padding: 12,
  },
});
