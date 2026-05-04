/**
 * SignInModal - local-only sign in.
 *
 * Two text fields (email + display name), one primary "Sign in" button.
 * On submit the modal calls signIn, grants starter credits, dismisses,
 * and reloads the page so any subscribed UI re-reads the account.
 *
 * No real auth is wired yet. When backend auth lands, swap the body of the
 * onSubmit handler to call your auth endpoint; the modal's surface API is
 * unchanged.
 */

import React, { useEffect, useRef, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { grantStarterCredits, signIn } from "../../domain/account";
import { useTheme } from "../useTheme";
import { Button } from "./Button";
import { PixelIcon } from "./PixelIcon";

export interface SignInModalProps {
  open: boolean;
  onCancel: () => void;
  /** Called after the account has been minted. Defaults to a page reload. */
  onSignedIn?: () => void;
}

export function SignInModal({ open, onCancel, onSignedIn }: SignInModalProps) {
  const theme = useTheme();
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const emailRef = useRef<TextInput | null>(null);

  useEffect(() => {
    if (!open) return;
    setEmail("");
    setDisplayName("");
    setError(null);
    // Slight delay so the input is mounted when we focus it.
    const t = setTimeout(() => {
      try {
        emailRef.current?.focus();
      } catch {
        // ignore
      }
    }, 30);
    return () => clearTimeout(t);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    if (Platform.OS !== "web") return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onCancel]);

  if (!open) return null;

  const submit = () => {
    const trimmed = email.trim();
    if (!trimmed || !trimmed.includes("@")) {
      setError("Enter a valid email address.");
      return;
    }
    try {
      signIn(trimmed, displayName.trim());
      grantStarterCredits();
    } catch (e: any) {
      setError(e?.message || "Could not sign in.");
      return;
    }
    if (onSignedIn) {
      onSignedIn();
    } else if (Platform.OS === "web" && typeof window !== "undefined") {
      window.location.reload();
    }
  };

  return (
    <View
      // @ts-ignore web-only role
      accessibilityRole={Platform.OS === "web" ? ("dialog" as any) : undefined}
      accessibilityLabel="Sign in"
      style={styles.root}
    >
      <Pressable
        accessibilityLabel="Close sign in"
        onPress={onCancel}
        style={[styles.backdrop, { backgroundColor: "rgba(20, 12, 6, 0.55)" }]}
      />
      <View
        style={[
          styles.panel,
          {
            backgroundColor: theme.colors.surface,
            borderColor: theme.colors.border,
          },
        ]}
      >
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
          <PixelIcon name="key" size={4} color={theme.colors.accent} />
          <Text style={[theme.typography.pixelLarge, { color: theme.colors.text }]}>
            Sign in
          </Text>
        </View>
        <Text
          style={{
            color: theme.colors.textMuted,
            fontSize: 13,
            lineHeight: 19,
            marginTop: 6,
            marginBottom: 18,
          }}
        >
          No password yet - sign-in is local for now. Real auth coming soon.
        </Text>

        <Text style={[styles.label, { color: theme.colors.text }]}>Email</Text>
        <TextInput
          ref={(el) => {
            emailRef.current = el;
          }}
          value={email}
          onChangeText={setEmail}
          placeholder="you@example.com"
          placeholderTextColor={theme.colors.textMuted}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          accessibilityLabel="Email address"
          style={[
            styles.input,
            {
              borderColor: theme.colors.border,
              backgroundColor: theme.colors.surface2,
              color: theme.colors.text,
            },
          ]}
        />

        <Text style={[styles.label, { color: theme.colors.text, marginTop: 12 }]}>
          Display name
        </Text>
        <TextInput
          value={displayName}
          onChangeText={setDisplayName}
          placeholder="Optional"
          placeholderTextColor={theme.colors.textMuted}
          accessibilityLabel="Display name"
          style={[
            styles.input,
            {
              borderColor: theme.colors.border,
              backgroundColor: theme.colors.surface2,
              color: theme.colors.text,
            },
          ]}
          onSubmitEditing={submit}
          returnKeyType="go"
        />

        {error ? (
          <Text style={{ color: theme.colors.danger, fontSize: 13, marginTop: 10 }}>
            {error}
          </Text>
        ) : null}

        <View style={styles.actions}>
          <Button title="Cancel" onPress={onCancel} variant="ghost" />
          <Button title="Sign in" onPress={submit} variant="primary" />
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    position: (Platform.OS === "web" ? "fixed" : "absolute") as any,
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    alignItems: "center",
    justifyContent: "center",
    zIndex: 10000,
    padding: 16,
  },
  backdrop: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
  },
  panel: {
    width: "100%",
    maxWidth: 420,
    borderWidth: 1,
    borderRadius: 14,
    padding: 24,
    // @ts-ignore web-only shadow
    boxShadow: "0 16px 40px rgba(31, 20, 10, 0.30)",
  },
  label: {
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.6,
    marginBottom: 6,
  },
  input: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
  },
  actions: {
    flexDirection: "row",
    justifyContent: "flex-end",
    gap: 8,
    marginTop: 20,
  },
});
