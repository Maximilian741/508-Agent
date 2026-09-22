/**
 * Verify email — landing page for the link in the verification email.
 *
 * Opened as /verify-email?token=<32 hex>. The token is the credential, so the
 * page works signed out: people open mail on a different device from the one
 * they signed up on.
 *
 * Why this page exists: the emailed link used to point at /auth/verify-email,
 * an API path the web server answers with 404. With SMTP configured the
 * starter grant requires a verified email, so nobody could verify and nobody
 * got their free credits — the whole signup funnel dead-ended on a 404.
 *
 * Deliberately NOT /verify, which is the public certificate check
 * (/verify?cert=…). Disallowed in robots.txt: it is a one-shot action page,
 * not something to index.
 */
import { useEffect, useMemo, useState } from "react";
import { View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import {
  confirmEmailVerification,
  grantStarterCredits,
  refreshAccount,
  requestEmailVerification,
} from "../src/domain/account";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Hero } from "../src/ui/components/Hero";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";

type State = "missing" | "verifying" | "verified" | "expired" | "error";

export default function VerifyEmailScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{ token?: string | string[] }>();
  const token = useMemo(() => {
    const t = Array.isArray(params.token) ? params.token[0] : params.token;
    return (t || "").trim();
  }, [params.token]);

  const [state, setState] = useState<State>(token ? "verifying" : "missing");
  const [detail, setDetail] = useState<string>("");
  const [resent, setResent] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setState("missing");
      return;
    }
    setState("verifying");
    (async () => {
      try {
        await confirmEmailVerification(token);
        if (cancelled) return;
        setState("verified");
        // Claim the starter credits it unlocks and pick up emailVerifiedAt for
        // a session already signed in here. Harmless when signed out (the
        // fixer tab, if any, claims them itself when it sees the change).
        grantStarterCredits()
          .then(() => refreshAccount())
          .catch(() => undefined);
      } catch (e: any) {
        if (cancelled) return;
        const msg: string = e?.message || "Could not verify this email address.";
        setState(msg.includes("token_expired") ? "expired" : "error");
        setDetail(msg);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const sendAnother = async () => {
    const queued = await requestEmailVerification();
    setResent(
      queued
        ? "Sent. Check your inbox for a fresh link."
        : "Sign in first, then use the banner on your dashboard to send a new link.",
    );
  };

  return (
    <Screen scroll title="Verify email">
      <Hero
        eyebrow="ACCOUNT"
        title="Email verification"
        subtitle="Verification links can be used once and expire after 24 hours."
      />
      <Card>
        {state === "missing" ? (
          <View style={{ gap: 12 }}>
            <InlineNotice
              tone="warning"
              title="Missing verification token"
              message="Open this page from the link in your verification email — the link carries the token that proves the address is yours."
            />
            <Button title="Go to sign in" href="/" variant="secondary" />
          </View>
        ) : state === "verifying" ? (
          <InlineNotice tone="info" title="Verifying…" message="Checking your link." />
        ) : state === "verified" ? (
          <View style={{ gap: 12 }}>
            <InlineNotice
              tone="success"
              title="Email confirmed"
              message="Thanks — your address is confirmed and your free credits are unlocked. If you were fixing a file in another tab, go back to it: it carries on by itself. You can close this tab."
            />
            <Button title="Fix a file" href="/" />
          </View>
        ) : (
          <View style={{ gap: 12 }}>
            <InlineNotice
              tone={state === "expired" ? "warning" : "danger"}
              title={state === "expired" ? "This link has expired or was already used" : "Couldn't verify"}
              message={
                state === "expired"
                  ? "Verification links work once and last 24 hours. Send yourself a new one and open it from the same inbox."
                  : detail
              }
            />
            {resent ? <InlineNotice tone="info" title="New link" message={resent} /> : null}
            <View style={{ flexDirection: "row", gap: 8, flexWrap: "wrap" }}>
              <Button title="Send a new link" onPress={sendAnother} />
              <Button title="Go to sign in" variant="ghost" href="/" />
            </View>
          </View>
        )}
      </Card>
    </Screen>
  );
}
