/**
 * Reset password — landing page for the emailed reset link.
 *
 * Opened as /reset-password?token=pr_…  The user picks a new password
 * (8+ chars, confirmed twice); on success we route home and open the
 * sign-in sheet so they can sign in with the new password immediately.
 * Expired/used tokens get a friendly retry path back to "request a new link".
 */
import { useMemo, useState } from "react";
import { StyleSheet, Text, TextInput, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import { confirmPasswordReset } from "../src/domain/account";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Hero } from "../src/ui/components/Hero";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";
import { useToast } from "../src/ui/toast";
import { useTheme } from "../src/ui/useTheme";

export default function ResetPasswordScreen() {
  const theme = useTheme();
  const toast = useToast();
  const router = useRouter();
  const params = useLocalSearchParams<{ token?: string | string[] }>();
  const token = useMemo(() => {
    const t = Array.isArray(params.token) ? params.token[0] : params.token;
    return (t || "").trim();
  }, [params.token]);

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("The two passwords don't match.");
      return;
    }
    setBusy(true);
    try {
      await confirmPasswordReset(token, password);
      setDone(true);
      toast.success("Password updated", {
        description: "Sign in with your new password.",
      });
    } catch (e: any) {
      const msg: string = e?.message || "Could not reset the password.";
      setError(
        msg.includes("token_expired")
          ? "This reset link has expired or was already used. Request a new one from the sign-in sheet."
          : msg,
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen scroll title="Reset password">
      <Hero
        eyebrow="ACCOUNT"
        title="Choose a new password"
        subtitle="Reset links can be used once and expire after an hour."
      />
      <Card>
        {!token ? (
          <InlineNotice
            tone="warning"
            title="Missing reset token"
            message="Open this page from the link in your reset email. To get one, open the sign-in sheet and choose “Forgot password?”."
          />
        ) : done ? (
          <View style={{ gap: 12 }}>
            <InlineNotice
              tone="success"
              title="Password updated"
              message="Your new password is active. Sign in to pick up where you left off."
            />
            <Button title="Go to sign in" onPress={() => router.push("/" as any)} />
          </View>
        ) : (
          <View style={{ gap: 10 }}>
            <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>New password</Text>
            <TextInput
              value={password}
              onChangeText={setPassword}
              secureTextEntry
              autoCapitalize="none"
              placeholder="At least 8 characters"
              placeholderTextColor={theme.colors.textMuted}
              accessibilityLabel="New password"
              style={[styles.input, { borderRadius: theme.radius.xs, borderColor: theme.colors.border, color: theme.colors.text }]}
            />
            <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>Confirm new password</Text>
            <TextInput
              value={confirm}
              onChangeText={setConfirm}
              secureTextEntry
              autoCapitalize="none"
              placeholder="Same password again"
              placeholderTextColor={theme.colors.textMuted}
              accessibilityLabel="Confirm new password"
              style={[styles.input, { borderRadius: theme.radius.xs, borderColor: theme.colors.border, color: theme.colors.text }]}
            />
            {error ? <InlineNotice tone="danger" title="Couldn't reset" message={error} /> : null}
            <View style={{ flexDirection: "row", gap: 8 }}>
              <Button title={busy ? "Saving…" : "Set new password"} onPress={submit} disabled={busy} />
              <Button title="Cancel" variant="ghost" onPress={() => router.push("/" as any)} />
            </View>
          </View>
        )}
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  input: {
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
  },
});
