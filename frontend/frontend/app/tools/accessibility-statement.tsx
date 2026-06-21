/**
 * Accessibility Statement generator — standalone tool.
 *
 * Every organization that publishes content needs a published accessibility
 * statement (it's a documented best practice under WCAG/EN 301 549 and is
 * expected by ADA Title II). This generates a ready-to-publish statement in the
 * standard W3C structure from a short form — copy or download it, no account
 * needed. A free, sticky value-add that helps the product sell ("they don't
 * just fix the docs — they hand you the compliance paperwork too").
 *
 * Honest by construction: the conformance status is whatever the user selects
 * (we default to "Partially conformant", the truthful choice for most orgs),
 * and the statement says assessment was automated + self-evaluation — never
 * claiming an independent audit that didn't happen.
 */

import { useMemo, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { Button } from "../../src/ui/components/Button";
import { Card } from "../../src/ui/components/Card";
import { Hero } from "../../src/ui/components/Hero";
import { Screen } from "../../src/ui/components/Screen";
import { useToast } from "../../src/ui/toast";
import { useTheme } from "../../src/ui/useTheme";

type Status = "partial" | "full" | "none";

const STATUS_LABEL: Record<Status, string> = {
  full: "Fully conformant",
  partial: "Partially conformant",
  none: "Non-conformant",
};

function _todayISO(): string {
  // Avoid argless Date in non-web; on web this is fine.
  try {
    return new Date().toISOString().slice(0, 10);
  } catch {
    return "";
  }
}

function _statusSentence(status: Status, standard: string, org: string): string {
  if (status === "full") {
    return `${org} is fully conformant with ${standard}. Fully conformant means the content fully meets the standard without any exceptions.`;
  }
  if (status === "partial") {
    return `${org} is partially conformant with ${standard}. Partially conformant means that some parts of the content do not fully meet the standard.`;
  }
  return `${org} is non-conformant with ${standard}. Non-conformant means the content does not yet meet the standard.`;
}

interface StatementInput {
  org: string;
  scope: string; // what the statement covers, e.g. "our website and documents"
  email: string;
  phone: string;
  standard: string;
  status: Status;
  limitations: string;
  date: string;
}

function _buildStatementText(s: StatementInput): string {
  const org = s.org.trim() || "Our organization";
  const scope = s.scope.trim() || "our website and digital documents";
  const standard = s.standard.trim() || "WCAG 2.1 level AA";
  const contactBits: string[] = [];
  if (s.email.trim()) contactBits.push(`E-mail: ${s.email.trim()}`);
  if (s.phone.trim()) contactBits.push(`Phone: ${s.phone.trim()}`);
  const contact = contactBits.length ? contactBits.join("\n") : "[add a contact email]";
  const lim = s.limitations.trim();
  const limSection = lim
    ? `\n\n## Known limitations\n\nDespite our best efforts, some content may not yet be fully accessible. Known limitations:\n\n${lim
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean)
        .map((l) => `- ${l}`)
        .join("\n")}\n\nWe are working to resolve these.`
    : "";

  return `# Accessibility Statement for ${org}

${org} is committed to making ${scope} accessible to everyone, including people with disabilities. We continually work to improve the user experience for all and apply the relevant accessibility standards.

## Conformance status

The Web Content Accessibility Guidelines (WCAG) define requirements for designers and developers to improve accessibility for people with disabilities. ${_statusSentence(
    s.status,
    standard,
    org,
  )}${limSection}

## Feedback

We welcome your feedback on the accessibility of ${scope}. Please let us know if you encounter accessibility barriers:

${contact}

We try to respond to feedback within 5 business days.

## Assessment approach

${org} assessed the accessibility of ${scope} by the following approaches:

- Self-evaluation
- Automated testing and remediation using 508 Agent (checks against WCAG 2.1, Section 508, and PDF/UA structural criteria)

## Date

This statement was created on ${s.date || "[date]"}. It will be reviewed and updated as content and standards evolve.`;
}

function _markdownToHtml(md: string): string {
  // Tiny, safe markdown → HTML for the downloadable statement (headings, lists,
  // paragraphs). Escapes all text first so user input can't inject markup.
  const esc = (t: string) =>
    t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = md.split("\n");
  const out: string[] = [];
  let inList = false;
  const closeList = () => {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (line.startsWith("# ")) {
      closeList();
      out.push(`<h1>${esc(line.slice(2))}</h1>`);
    } else if (line.startsWith("## ")) {
      closeList();
      out.push(`<h2>${esc(line.slice(3))}</h2>`);
    } else if (line.startsWith("- ")) {
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${esc(line.slice(2))}</li>`);
    } else if (line.trim() === "") {
      closeList();
    } else {
      closeList();
      out.push(`<p>${esc(line)}</p>`);
    }
  }
  closeList();
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Accessibility Statement</title>
<style>body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1A1A1A;max-width:760px;margin:48px auto;padding:0 24px;line-height:1.6}
h1{font-size:28px}h2{font-size:19px;margin-top:28px}ul{padding-left:22px}li{margin:4px 0}</style></head>
<body>${out.join("\n")}</body></html>`;
}

function _saveBlob(content: string, mime: string, name: string) {
  if (Platform.OS !== "web" || typeof document === "undefined") return;
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function AccessibilityStatementTool() {
  const theme = useTheme();
  const toast = useToast();
  const [org, setOrg] = useState("");
  const [scope, setScope] = useState("our website and digital documents");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [status, setStatus] = useState<Status>("partial");
  const [limitations, setLimitations] = useState("");
  const [date, setDate] = useState(_todayISO());

  const standard = "WCAG 2.1 level AA";
  const input: StatementInput = { org, scope, email, phone, standard, status, limitations, date };
  const text = useMemo(() => _buildStatementText(input), [org, scope, email, phone, status, limitations, date]);

  const copy = async () => {
    if (Platform.OS !== "web") return;
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Statement copied to clipboard");
    } catch {
      toast.error("Couldn't copy. Select the text and copy manually.");
    }
  };

  const inputStyle = [
    styles.input,
    { borderColor: theme.colors.border, color: theme.colors.text, backgroundColor: theme.colors.surface, borderRadius: theme.radius.xs },
  ];

  return (
    <Screen scroll title="Accessibility statement">
      <Hero
        eyebrow="Free tool"
        title="Accessibility Statement generator"
        subtitle="Every site and document library should publish an accessibility statement. Fill in a few fields and get a ready-to-publish statement in the standard format (copy it or download it). No account needed."
      />

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text, marginBottom: 12 }]}>Your details</Text>

        <Text style={[styles.label, { color: theme.colors.textMuted }]}>Organization or product name</Text>
        <TextInput value={org} onChangeText={setOrg} placeholder="Acme Corporation" placeholderTextColor={theme.colors.textMuted} style={inputStyle} accessibilityLabel="Organization or product name" />

        <Text style={[styles.label, { color: theme.colors.textMuted }]}>What does this cover?</Text>
        <TextInput value={scope} onChangeText={setScope} placeholder="our website and digital documents" placeholderTextColor={theme.colors.textMuted} style={inputStyle} accessibilityLabel="Scope of the statement" />

        <Text style={[styles.label, { color: theme.colors.textMuted }]}>Feedback contact email</Text>
        <TextInput value={email} onChangeText={setEmail} placeholder="accessibility@acme.com" placeholderTextColor={theme.colors.textMuted} autoCapitalize="none" keyboardType="email-address" style={inputStyle} accessibilityLabel="Feedback contact email" />

        <Text style={[styles.label, { color: theme.colors.textMuted }]}>Feedback phone (optional)</Text>
        <TextInput value={phone} onChangeText={setPhone} placeholder="+1 555 123 4567" placeholderTextColor={theme.colors.textMuted} style={inputStyle} accessibilityLabel="Feedback phone" />

        <Text style={[styles.label, { color: theme.colors.textMuted }]}>Conformance status</Text>
        <View style={styles.statusRow}>
          {(["partial", "full", "none"] as Status[]).map((s) => {
            const selected = status === s;
            return (
              <Pressable
                key={s}
                accessibilityRole="radio"
                accessibilityState={{ selected }}
                accessibilityLabel={STATUS_LABEL[s]}
                onPress={() => setStatus(s)}
                style={[
                  styles.statusOpt,
                  {
                    borderColor: selected ? theme.colors.accent : theme.colors.border,
                    backgroundColor: selected ? theme.colors.accent + "14" : theme.colors.surface,
                    borderRadius: theme.radius.sm,
                  },
                ]}
              >
                <Text style={[theme.typography.body, { color: selected ? theme.colors.accent : theme.colors.text, fontWeight: "700", fontSize: 13 }]}>
                  {STATUS_LABEL[s]}
                </Text>
              </Pressable>
            );
          })}
        </View>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 4 }]}>
          Most organizations should choose “Partially conformant”: it's the honest status while remediation is ongoing, and claiming full conformance you can't back up creates legal risk.
        </Text>

        <Text style={[styles.label, { color: theme.colors.textMuted, marginTop: 12 }]}>Known limitations (optional, one per line)</Text>
        <TextInput
          value={limitations}
          onChangeText={setLimitations}
          placeholder={"Some older PDFs are not yet tagged\nThird-party embedded videos lack captions"}
          placeholderTextColor={theme.colors.textMuted}
          multiline
          numberOfLines={3}
          style={[inputStyle, { minHeight: 72, textAlignVertical: "top" }]}
          accessibilityLabel="Known accessibility limitations"
        />

        <Text style={[styles.label, { color: theme.colors.textMuted, marginTop: 12 }]}>Statement date</Text>
        <TextInput value={date} onChangeText={setDate} placeholder="YYYY-MM-DD" placeholderTextColor={theme.colors.textMuted} style={inputStyle} accessibilityLabel="Statement date" />
      </Card>

      <Card>
        <View style={styles.previewHeader}>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Preview</Text>
          <View style={{ flexDirection: "row", gap: 8 }}>
            <Button title="Copy text" onPress={copy} variant="secondary" />
            <Button
              title="Download HTML"
              onPress={() => _saveBlob(_markdownToHtml(text), "text/html", "accessibility-statement.html")}
              variant="ghost"
            />
            <Button
              title="Download .md"
              onPress={() => _saveBlob(text, "text/markdown", "accessibility-statement.md")}
              variant="ghost"
            />
          </View>
        </View>
        <View style={[styles.preview, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2, borderRadius: theme.radius.md }]}>
          <Text style={[theme.typography.mono, { color: theme.colors.text, fontSize: 12, lineHeight: 18 }]} selectable>
            {text}
          </Text>
        </View>
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  label: { fontSize: 12, fontWeight: "700", letterSpacing: 0.3, marginTop: 10, marginBottom: 4, textTransform: "uppercase" },
  input: { borderWidth: 1, paddingHorizontal: 12, paddingVertical: 10, fontSize: 15 },
  statusRow: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  statusOpt: { borderWidth: 1, paddingHorizontal: 12, paddingVertical: 10 },
  previewHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8, marginBottom: 12 },
  preview: { borderWidth: 1, padding: 14 },
});
