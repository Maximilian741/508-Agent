/**
 * The three things that can stand between a person and their fixed file,
 * each shown IN PLACE (no modal, no page change, the file stays put):
 *
 *   InlineSignUp       email + password, one form for new and returning people.
 *   VerifyEmailPanel   "Check your inbox to unlock your free credits": sends the
 *                      link, polls /auth/me every 5 s, and hands control back
 *                      the moment the email is confirmed (the fix continues on
 *                      its own).
 *   BuyCreditsPanel    out of credits: the packs right here. With Stripe, the
 *                      checkout returns to this page and the fix continues.
 */
import React, { useEffect, useRef, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import {
  Account,
  getBillingConfig,
  loadAccount,
  purchaseTier,
  refreshAccount,
  requestEmailVerification,
  requestPasswordReset,
  signIn,
  SignInResult,
  startCreditCheckout,
} from "../../domain/account";
import { humanError } from "../../domain/apiErrors";
import { CREDIT_PACKS, FREE_CREDITS } from "../../domain/pricing";
import { Button } from "../components/Button";
import { Spinner } from "../components/Spinner";
import { useTheme } from "../useTheme";

// ---------------------------------------------------------------------------
// Inline sign-up
// ---------------------------------------------------------------------------

export function InlineSignUp({
  purpose,
  cost,
  notice,
  onDone,
}: {
  /** "check": the scan itself needs an account; "fix": the fix does. */
  purpose: "check" | "fix";
  cost?: number;
  /** An extra line above the form (e.g. "Please sign in again"). */
  notice?: string | null;
  onDone: (result: SignInResult) => void;
}) {
  const theme = useTheme();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resetSent, setResetSent] = useState(false);
  const passwordRef = useRef<TextInput | null>(null);

  const title = purpose === "check" ? "Create a free account to check this file" : "Create a free account to fix it";
  const button = purpose === "check" ? "Create account and check my file" : "Create account and fix it";

  const submit = async () => {
    if (busy) return;
    const e = email.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e)) {
      setError("Enter your email address, like name@example.com.");
      return;
    }
    if (password.length < 8) {
      setError("Use at least 8 characters for your password.");
      passwordRef.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await signIn(e, "", password, true);
      onDone(result);
    } catch (err) {
      setError(humanError(err));
    } finally {
      setBusy(false);
    }
  };

  const forgot = async () => {
    const e = email.trim();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e)) {
      setError("Type your email address first, then choose “Forgot your password?”.");
      return;
    }
    try {
      await requestPasswordReset(e);
      setResetSent(true);
      setError(null);
    } catch (err) {
      setError(humanError(err));
    }
  };

  const input = [
    styles.input,
    { borderColor: theme.colors.border, backgroundColor: theme.colors.surface, color: theme.colors.text, borderRadius: theme.radius.sm },
  ];

  return (
    <View style={[styles.panel, { borderColor: theme.colors.glassBorder, backgroundColor: theme.colors.surface2, borderRadius: theme.radius.md }]}>
      <Text style={[theme.typography.h2, { color: theme.colors.text }]} accessibilityRole="header" {...({ "aria-level": 3 } as any)}>
        {title}
      </Text>
      {notice ? <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>{notice}</Text> : null}
      <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
        {purpose === "fix" && cost
          ? `Fixing this file uses ${cost} credits. New accounts get ${FREE_CREDITS} free credits.`
          : `It's free. New accounts also get ${FREE_CREDITS} free credits for fixing files.`}{" "}
        Already have an account? Use the same form.
      </Text>

      <View style={{ gap: 6 }}>
        <Text nativeID="fix-signup-email" style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>
          Email
        </Text>
        <TextInput
          value={email}
          onChangeText={setEmail}
          autoFocus
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          textContentType="emailAddress"
          autoComplete="email"
          inputMode="email"
          accessibilityLabel="Email"
          {...({ "aria-labelledby": "fix-signup-email" } as any)}
          returnKeyType="next"
          onSubmitEditing={() => passwordRef.current?.focus()}
          style={input}
        />
      </View>
      <View style={{ gap: 6 }}>
        <Text nativeID="fix-signup-password" style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>
          Password (8 or more characters)
        </Text>
        <TextInput
          ref={(el) => {
            passwordRef.current = el;
          }}
          value={password}
          onChangeText={setPassword}
          secureTextEntry
          autoCapitalize="none"
          autoCorrect={false}
          textContentType="newPassword"
          autoComplete="new-password"
          accessibilityLabel="Password (8 or more characters)"
          {...({ "aria-labelledby": "fix-signup-password" } as any)}
          returnKeyType="go"
          onSubmitEditing={submit}
          style={input}
        />
      </View>

      {error ? (
        <Text
          accessibilityRole="alert"
          accessibilityLiveRegion="assertive"
          style={[theme.typography.body, { color: theme.colors.danger, fontWeight: "600" }]}
        >
          {error}
        </Text>
      ) : null}

      <Button title={button} onPress={submit} loading={busy} style={{ alignSelf: "stretch", minHeight: 48 }} />

      {resetSent ? (
        <Text accessibilityLiveRegion="polite" style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
          If that email has an account, we sent a link to reset the password. It works for 1 hour.
        </Text>
      ) : (
        <Pressable
          accessibilityRole="button"
          onPress={forgot}
          style={({ focused }: any) => [styles.textLink, focused ? focusRing(theme) : null]}
        >
          <Text style={[theme.typography.body, { color: theme.colors.accent, textDecorationLine: "underline" }]}>
            Forgot your password?
          </Text>
        </Pressable>
      )}
    </View>
  );
}

// ---------------------------------------------------------------------------
// Verify email
// ---------------------------------------------------------------------------

/** Emails we already asked the server to send a link to, this page visit. */
const linkRequested = new Set<string>();

export function VerifyEmailPanel({
  email,
  alreadySent,
  fileName,
  onVerified,
}: {
  email: string;
  /** The server mailed the link as part of sign-up: don't send a second one. */
  alreadySent: boolean;
  fileName: string;
  /** Called once, when /auth/me shows the email confirmed. */
  onVerified: (account: Account) => void;
}) {
  const theme = useTheme();
  const [sentNote, setSentNote] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(false);
  const [checking, setChecking] = useState(false);
  const done = useRef(false);

  // Make sure a link is on its way (belt and braces with the server's own
  // send at sign-up), once per address per visit.
  useEffect(() => {
    const key = email.toLowerCase();
    if (alreadySent) {
      linkRequested.add(key);
      return;
    }
    if (linkRequested.has(key)) return;
    linkRequested.add(key);
    void requestEmailVerification();
  }, [email, alreadySent]);

  const check = async () => {
    if (done.current) return;
    const acct = await refreshAccount();
    if (acct?.emailVerifiedAt && !done.current) {
      done.current = true;
      onVerified(acct);
    }
  };

  // Poll every 5 s, and at once when the person comes back to this tab.
  useEffect(() => {
    const id = setInterval(() => void check(), 5000);
    const onVisible = () => {
      if (typeof document !== "undefined" && document.visibilityState === "visible") void check();
    };
    if (Platform.OS === "web" && typeof document !== "undefined") document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      if (Platform.OS === "web" && typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const resend = async () => {
    if (cooldown) return;
    setCooldown(true);
    const ok = await requestEmailVerification();
    setSentNote(ok ? `We sent a new link to ${email}.` : "We couldn't send a new link just now. Please try again in a minute.");
    setTimeout(() => setCooldown(false), 30000);
  };

  const checkNow = async () => {
    setChecking(true);
    await check();
    setChecking(false);
    if (!done.current) setSentNote("Not confirmed yet. Open the link in the email, then we'll carry on by ourselves.");
  };

  return (
    <View
      style={[styles.panel, { borderColor: theme.colors.glassBorder, backgroundColor: theme.colors.surface2, borderRadius: theme.radius.md }]}
    >
      <Text style={[theme.typography.h2, { color: theme.colors.text }]} accessibilityRole="header" {...({ "aria-level": 3 } as any)}>
        Check your inbox to unlock your {FREE_CREDITS} free credits
      </Text>
      <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
        We sent a link to <Text style={{ color: theme.colors.text, fontWeight: "600" }}>{email}</Text>. Open it, and we'll
        fix {fileName} right here. You don't need to come back and click anything.
      </Text>
      <View style={styles.waitRow}>
        <Spinner size={16} />
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>Waiting for you to confirm your email…</Text>
      </View>
      {sentNote ? (
        <Text accessibilityLiveRegion="polite" style={[theme.typography.body, { color: theme.colors.text }]}>
          {sentNote}
        </Text>
      ) : null}
      <View style={styles.buttonRow}>
        <Button title="I clicked the link" variant="secondary" onPress={checkNow} loading={checking} />
        <Button title="Send the link again" variant="ghost" onPress={resend} disabled={cooldown} />
      </View>
      <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
        Can't find it? Check your spam or junk folder.
      </Text>
    </View>
  );
}

// ---------------------------------------------------------------------------
// Buy credits
// ---------------------------------------------------------------------------

export function BuyCreditsPanel({
  cost,
  credits,
  beforeRedirect,
  onBought,
}: {
  cost: number;
  credits: number;
  /** Runs before leaving for checkout (keeps the file so we can come back). */
  beforeRedirect: () => Promise<void>;
  /** Credits were added without leaving the page (development billing). */
  onBought: (account: Account) => void;
}) {
  const theme = useTheme();
  const [stripe, setStripe] = useState<boolean | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void getBillingConfig().then((c) => {
      if (alive) setStripe(c.enabled);
    });
    return () => {
      alive = false;
    };
  }, []);

  const buy = async (key: "starter" | "pro" | "studio") => {
    if (busy) return;
    setBusy(key);
    setError(null);
    try {
      if (stripe) {
        await beforeRedirect();
        const url = await startCreditCheckout(key, "/?resume=fix&paid=1", "/?resume=fix");
        if (Platform.OS === "web" && typeof window !== "undefined") window.location.href = url;
        return;
      }
      await purchaseTier(key);
      const fresh = (await refreshAccount()) ?? loadAccount();
      if (fresh) onBought(fresh);
    } catch (err) {
      setError(humanError(err));
    } finally {
      setBusy(null);
    }
  };

  const need = Math.max(0, cost - credits);
  return (
    <View
      style={[styles.panel, { borderColor: theme.colors.glassBorder, backgroundColor: theme.colors.surface2, borderRadius: theme.radius.md }]}
    >
      <Text style={[theme.typography.h2, { color: theme.colors.text }]} accessibilityRole="header" {...({ "aria-level": 3 } as any)}>
        You need {cost} credits to fix this file
      </Text>
      <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
        You have {credits} {credits === 1 ? "credit" : "credits"}, so you're {need} short. Pick a pack and we'll fix your file
        as soon as it's paid. Credits never expire.
      </Text>
      <View style={styles.packs}>
        {CREDIT_PACKS.map((p) => (
          <Button
            key={p.key}
            title={`${p.credits} credits – $${(p.priceCents / 100).toFixed(0)}`}
            variant={p.highlight ? "primary" : "secondary"}
            loading={busy === p.key}
            disabled={stripe === null || (busy !== null && busy !== p.key)}
            onPress={() => void buy(p.key)}
            style={{ flexGrow: 1, minWidth: 150 }}
          />
        ))}
      </View>
      {error ? (
        <Text accessibilityRole="alert" style={[theme.typography.body, { color: theme.colors.danger, fontWeight: "600" }]}>
          {error}
        </Text>
      ) : null}
      <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
        {stripe ? "You'll pay on a secure checkout page, then come straight back here. " : ""}
        You're only charged credits if we actually fix something.
      </Text>
    </View>
  );
}

// ---------------------------------------------------------------------------

function focusRing(theme: ReturnType<typeof useTheme>) {
  return {
    outlineColor: theme.colors.accent,
    outlineWidth: theme.focus.outlineWidth,
    outlineStyle: "solid",
    outlineOffset: theme.focus.outlineOffset,
  } as any;
}

const styles = StyleSheet.create({
  panel: {
    borderWidth: 1,
    padding: 18,
    gap: 12,
  },
  input: {
    borderWidth: 1,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 16,
    minHeight: 48,
  },
  textLink: {
    alignSelf: "flex-start",
    paddingVertical: 4,
  },
  waitRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  buttonRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  packs: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
});
