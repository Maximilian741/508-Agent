/**
 * FAQ — buyer-facing questions a prospect asks *before* signing up.
 *
 * Deliberately distinct from /help (which is a technical WCAG glossary +
 * mechanics FAQ). This page handles purchase objections and trust questions:
 * pricing, privacy/AI-training, accuracy limits, refunds, teams, the API.
 * Every answer must be literally true of the product today — no aspirational
 * claims. Deep technical questions link out to /help.
 */
import { Linking, Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Hero } from "../src/ui/components/Hero";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";
import { Seo, useJsonLd } from "../src/ui/components/Seo";

interface QA {
  q: string;
  a: string;
}
interface Section {
  id: string;
  title: string;
  items: QA[];
}

// Pricing facts mirrored from billing.tsx / help.tsx: analysis free; remediation
// PDF 5 · DOCX 3 · PPTX 4 · HTML 3 credits; certificate 2 (free on Team/Business);
// 25 free credits on signup; Team 3 seats / Business 10 seats, shared wallet.
const SECTIONS: Section[] = [
  {
    id: "getting-started",
    title: "Getting started",
    items: [
      {
        q: "What kinds of files can I fix?",
        a: "PDF, Microsoft Word (.docx), Microsoft PowerPoint (.pptx) and HTML web pages (.html). Upload one at a time, or a whole folder at once on the Batch screen.",
      },
      {
        q: "Do I have to know anything about accessibility?",
        a: "No. Upload a document and click \"Fix everything\": we detect the issues, write the fixes (including AI alt text for your images), and hand back a corrected file plus a plain-English report. You only step in if you want to review or edit a suggestion.",
      },
      {
        q: "Is there really a free tier?",
        a: "Yes. Analysing a document (seeing every WCAG, Section 508 and PDF/UA issue it contains) is always free, with no card required. New accounts also get 25 free credits, enough to download several remediated files before you ever pay.",
      },
      {
        q: "How long does it take?",
        a: "Most documents analyse in a few seconds to a minute depending on size and whether they need OCR. Remediation (writing the fixed file) is typically just as fast.",
      },
    ],
  },
  {
    id: "pricing",
    title: "Pricing & credits",
    items: [
      {
        q: "What is a credit, and what does a fix cost?",
        a: "Analysis is free. Downloading a remediated file costs credits by format: PDF 5, Word 3, PowerPoint 4, HTML 3. A conformance certificate costs 2 credits, or is included free on Team and Business plans. Credits never expire.",
      },
      {
        q: "Do I need a subscription?",
        a: "No. You can buy one-time credit packs with no commitment and use them whenever you like. Subscriptions (Team / Business) are simply the better value if you remediate documents every month, and they include free certificates and shared team seats.",
      },
      {
        q: "What's your refund policy?",
        a: "Unused credit packs are refundable within 14 days; just email support@508-agent.app. Subscriptions can be cancelled anytime from the billing portal and keep access through the period you've already paid for.",
      },
      {
        q: "Can I pay annually?",
        a: "Yes. Annual billing on Team and Business saves roughly 17% (about two months free) versus paying monthly.",
      },
    ],
  },
  {
    id: "accuracy",
    title: "Accuracy & trust",
    items: [
      {
        q: "Is this a legal guarantee of compliance?",
        a: "No, and we're deliberately careful here. 508 Agent automates remediation and produces an honest summary of what was fixed. It is not a formal conformance determination or legal certification. The conformance report marks criteria we can't automatically verify as \"needs manual review\" rather than claiming a pass.",
      },
      {
        q: "What if an automated fix is wrong?",
        a: "Every remediated file is a new copy; your original is never overwritten. You can review and edit any AI suggestion before downloading, and after downloading you can click \"Verify the fix\" to re-audit the corrected file for free. Fixes that need human judgement (like the meaning of an image) are flagged for you rather than applied silently.",
      },
      {
        q: "What does the score actually mean?",
        a: "The score only counts fixes that genuinely persist into the downloaded file. If something can't be auto-fixed, it's listed honestly as pending manual work instead of inflating the number. That's the whole point: a report an auditor can trust.",
      },
      {
        q: "Can someone verify a report I hand them?",
        a: "Yes. Every remediation summary has a public verification link, so an auditor or procurement officer can confirm it came from us and wasn't edited.",
      },
    ],
  },
  {
    id: "privacy",
    title: "Privacy & your data",
    items: [
      {
        q: "Do you train AI on my documents?",
        a: "No. We never use your documents to train AI models. When AI alt text is generated, your image is sent to the configured vision provider only to produce that description, and for no other purpose.",
      },
      {
        q: "How long do you keep my files?",
        a: "Hosted files are automatically deleted after 24 hours. You download your remediated file and report; we don't retain your documents beyond that window.",
      },
      {
        q: "Is my account and data secure?",
        a: "Yes. Sign-in uses password authentication, each document is scoped to its owner so no one else can access it, and we publish a security disclosure policy. For details see our Privacy and Security pages, or email security@508-agent.app.",
      },
    ],
  },
  {
    id: "teams-api",
    title: "Teams & automation",
    items: [
      {
        q: "Can my whole team share one plan?",
        a: "Yes. Team includes 3 seats and Business includes 10, all drawing from one shared credit wallet, so you buy credits once for the group instead of per person.",
      },
      {
        q: "Is there an API for automation?",
        a: "Yes. You can generate an API key under Settings → Developer API and scan documents programmatically, for example from your CI pipeline. Analysis over the API is free; the key can't spend your credits.",
      },
      {
        q: "Can I put my agency's brand on the reports?",
        a: "Yes. On Settings → Report branding you can add your logo and brand colours, and every conformance report and certificate you generate is rendered under your brand, ideal for agencies delivering to clients.",
      },
    ],
  },
];

/** schema.org FAQPage, derived from SECTIONS so Google's rich result can
 *  never drift from what the page actually says. */
function _faqJsonLd(): object {
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: SECTIONS.flatMap((s) =>
      s.items.map((it) => ({
        "@type": "Question",
        name: it.q,
        acceptedAnswer: { "@type": "Answer", text: it.a },
      })),
    ),
  };
}

export default function FaqScreen() {
  const theme = useTheme();
  // Google's FAQ rich result; injected at runtime (see Seo.tsx for why).
  useJsonLd(_faqJsonLd());
  const router = useRouter();
  const isWeb = Platform.OS === "web";

  return (
    <Screen scroll title="FAQ">
      <Seo
        title="FAQ — 508 Agent Accessibility Converter"
        description="What file types we fix, what stays private, how accurate automated 508/WCAG remediation is, what a credit costs, and how team plans work."
      />
      <Hero
        eyebrow="FAQ"
        title="Questions, answered"
        subtitle="Everything a first-time buyer usually asks: pricing, privacy, accuracy and teams. Looking for the accessibility rules themselves? The Help & glossary page covers every check in depth."
      />

      <View style={styles.layout}>
        {/* Left-rail TOC on web, matching the Help page. */}
        {isWeb ? (
          <View
            // @ts-ignore — position: sticky is web-only
            style={[styles.toc, { borderRightColor: theme.colors.border, position: "sticky" as any, top: 24 }]}
          >
            <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginBottom: 14 }]}>
              ON THIS PAGE
            </Text>
            {SECTIONS.map((s) => (
              <TocLink key={s.id} href={s.id} label={s.title} />
            ))}
          </View>
        ) : null}

        <View style={styles.body}>
          {SECTIONS.map((section) => (
            <View
              key={section.id}
              // @ts-ignore — id used as scroll target on web
              nativeID={section.id}
              style={styles.sectionBlock}
            >
              <Text
                style={[
                  theme.typography.displaySmall as any,
                  { color: theme.colors.text, fontSize: 26, marginBottom: 4 },
                ]}
              >
                {section.title}
              </Text>
              {section.items.map((item, i) => (
                <View
                  key={i}
                  style={[
                    styles.faqEntry,
                    i === 0
                      ? null
                      : { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: theme.colors.border },
                  ]}
                >
                  <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 17 }]}>
                    {item.q}
                  </Text>
                  <Text
                    style={[
                      theme.typography.body,
                      { color: theme.colors.text, marginTop: 8, lineHeight: 24, fontSize: 15 },
                    ]}
                  >
                    {item.a}
                  </Text>
                </View>
              ))}
            </View>
          ))}

          {/* Still-stuck CTA */}
          <Card style={{ marginTop: 40 }}>
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Still have a question?</Text>
            <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 6 }]}>
              Email{" "}
              <Text
                accessibilityRole="link"
                onPress={() => Linking.openURL("mailto:support@508-agent.app")}
                style={{ color: theme.colors.accent, fontWeight: "700" }}
              >
                support@508-agent.app
              </Text>{" "}
              and a human will get back to you, or read the in-depth Help &amp; glossary.
            </Text>
            <View style={styles.ctaRow}>
              <Button title="Start free: 25 credits" href="/audit" />
              <Button title="Read the glossary" variant="ghost" href="/help" />
            </View>
          </Card>
        </View>
      </View>
    </Screen>
  );
}

function TocLink({ href, label }: { href: string; label: string }) {
  const theme = useTheme();
  const onPress = () => {
    if (Platform.OS !== "web") return;
    if (typeof document === "undefined") return;
    const el = document.getElementById(href);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`Jump to ${label}`}
      onPress={onPress}
      style={({ hovered }: any) => [styles.tocLink, hovered ? { opacity: 0.7 } : null]}
    >
      <Text style={[theme.typography.body, { color: theme.colors.text, fontSize: 14, fontWeight: "500" }]}>
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  layout: { flexDirection: "row", gap: 48, alignItems: "flex-start", flexWrap: "wrap" },
  toc: { width: 200, paddingRight: 24, borderRightWidth: StyleSheet.hairlineWidth, paddingVertical: 8 },
  tocLink: { paddingVertical: 6 },
  body: { flex: 1, minWidth: 320, maxWidth: 720 },
  sectionBlock: { marginTop: 48 },
  faqEntry: { paddingTop: 22, paddingBottom: 6, marginTop: 0 },
  ctaRow: { flexDirection: "row", gap: 12, marginTop: 16, flexWrap: "wrap" },
});
