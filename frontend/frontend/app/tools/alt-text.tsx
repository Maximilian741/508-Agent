/**
 * Free standalone tool: AI alt-text generator.
 *
 * Drop in a single image, get a ready-to-paste description — no document upload
 * required. Directly removes the most common piece of accessibility busywork
 * ("no one wants to look at an image to write alt text"). Free for signed-in
 * users; the backend enforces auth + rate limits to bound vision-AI cost.
 */
import { useState } from "react";
import { Image, Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { generateAltTextForImage, AltTextResult } from "../../src/domain/altText";
import { loadToken } from "../../src/domain/account";
import { Button } from "../../src/ui/components/Button";
import { Card } from "../../src/ui/components/Card";
import { Hero } from "../../src/ui/components/Hero";
import { Screen } from "../../src/ui/components/Screen";
import { SignInModal } from "../../src/ui/components/SignInModal";
import { useTheme } from "../../src/ui/useTheme";
import { useToast } from "../../src/ui/toast";

const MAX_MB = 10;

export default function AltTextToolScreen() {
  const theme = useTheme();
  const router = useRouter();
  const toast = useToast();
  const isWeb = Platform.OS === "web";

  const [signedIn, setSignedIn] = useState<boolean>(() => !!loadToken());
  const [signInOpen, setSignInOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [previewUri, setPreviewUri] = useState<string | null>(null);
  const [result, setResult] = useState<AltTextResult | null>(null);
  const [busy, setBusy] = useState(false);

  const pickImage = () => {
    if (!isWeb || typeof document === "undefined") return;
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/png,image/jpeg,image/gif,image/webp,image/bmp,image/*";
    input.onchange = () => {
      const f = input.files && input.files[0];
      if (!f) return;
      if (f.size > MAX_MB * 1024 * 1024) {
        toast.error("Image too large", { description: `Please choose an image under ${MAX_MB} MB.` });
        return;
      }
      setResult(null);
      setFile(f);
      try {
        setPreviewUri(URL.createObjectURL(f));
      } catch {
        setPreviewUri(null);
      }
    };
    input.click();
  };

  const generate = async () => {
    if (!file) return;
    if (!loadToken()) {
      setSignInOpen(true);
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      const r = await generateAltTextForImage(file);
      setResult(r);
      if (!r.aiConfigured) {
        toast.info?.("Heuristic result", {
          description: "No vision AI key is configured on this deployment, so this is a generic placeholder.",
        });
      }
    } catch (e: any) {
      if (e?.status === 401) {
        setSignedIn(false);
        setSignInOpen(true);
      } else {
        toast.error("Couldn't generate alt text", { description: e?.message || "Try again." });
      }
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    if (!result?.altText) return;
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard) {
        await navigator.clipboard.writeText(result.altText);
        toast.success("Copied", { description: "Alt text copied to your clipboard." });
      }
    } catch {
      toast.error("Couldn't copy", { description: "Select the text and copy it manually." });
    }
  };

  return (
    <Screen scroll title="AI alt-text generator">
      <Hero
        eyebrow="FREE TOOL"
        title="AI alt-text generator"
        subtitle="Drop in an image and get a ready-to-paste description in seconds. No document needed (free for signed-in users). For a whole file's images at once, use the audit flow instead."
      />

      {!isWeb ? (
        <Card>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
            This tool is available in the web app.
          </Text>
        </Card>
      ) : !signedIn && !loadToken() ? (
        <Card>
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Sign in to use this tool</Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 6 }]}>
            It's free; signing in just lets us keep the AI usage fair. New accounts get 25 free credits too.
          </Text>
          <View style={{ flexDirection: "row", gap: 12, marginTop: 16, flexWrap: "wrap" }}>
            <Button title="Sign in" onPress={() => setSignInOpen(true)} />
            <Button title="See pricing" variant="ghost" href="/billing" />
          </View>
        </Card>
      ) : (
        <Card>
          <View style={{ flexDirection: "row", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
            <Button title={file ? "Choose a different image" : "Choose an image"} variant="secondary" onPress={pickImage} />
            {file ? (
              <Text style={[theme.typography.body, { color: theme.colors.textMuted, flexShrink: 1 }]}>{file.name}</Text>
            ) : null}
          </View>

          {previewUri ? (
            <Image
              source={{ uri: previewUri }}
              accessibilityLabel="Selected image preview"
              resizeMode="contain"
              style={[styles.preview, { borderRadius: theme.radius.md }]}
            />
          ) : null}

          <View style={{ marginTop: 16 }}>
            <Button
              title={busy ? "Generating…" : "Generate alt text"}
              onPress={generate}
              loading={busy}
              disabled={busy || !file}
            />
          </View>

          {result ? (
            <View style={[styles.resultBox, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2, borderRadius: theme.radius.xs }]}>
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>SUGGESTED ALT TEXT</Text>
              <Text
                accessibilityLabel="Generated alt text"
                selectable
                style={[theme.typography.body, { color: theme.colors.text, marginTop: 6, fontSize: 16, lineHeight: 24 }]}
              >
                {result.altText}
              </Text>
              <View style={{ flexDirection: "row", gap: 12, marginTop: 12, alignItems: "center", flexWrap: "wrap" }}>
                <Button title="Copy" variant="secondary" onPress={copy} />
                <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                  {result.aiConfigured
                    ? `Generated by ${result.provider} · always review before publishing`
                    : "Generic placeholder: no vision AI key is configured on this deployment"}
                </Text>
              </View>
            </View>
          ) : null}
        </Card>
      )}

      <SignInModal
        open={signInOpen}
        onSignedIn={() => {
          setSignedIn(true);
          setSignInOpen(false);
        }}
        onCancel={() => setSignInOpen(false)}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  preview: {
    width: "100%",
    height: 240,
    marginTop: 16,
  },
  resultBox: {
    marginTop: 18,
    borderWidth: 1,
    padding: 16,
  },
});
