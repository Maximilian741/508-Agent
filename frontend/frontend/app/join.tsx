/**
 * Accept a team invitation: /join?token=<token>
 *
 * The invite is addressed to a specific email, so the visitor must be signed in
 * as that address. Signed-out visitors are prompted to sign in (or sign up)
 * with the invited email first.
 */

import React, { useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";

import { acceptTeamInvite, loadAccount, loadToken } from "../src/domain/account";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Hero } from "../src/ui/components/Hero";
import { Screen } from "../src/ui/components/Screen";
import { SignInModal } from "../src/ui/components/SignInModal";
import { useTheme } from "../src/ui/useTheme";

const OK_GREEN = "#16A34A";

export default function JoinScreen() {
  const theme = useTheme();
  const router = useRouter();
  const params = useLocalSearchParams<{ token?: string | string[] }>();
  const token = Array.isArray(params.token) ? params.token[0] : params.token;

  const [signInOpen, setSignInOpen] = useState(false);
  const [state, setState] = useState<"idle" | "joining" | "joined" | "error">("idle");
  const [teamName, setTeamName] = useState<string>("");
  const [errorMsg, setErrorMsg] = useState<string>("");

  const signedIn = !!loadToken();
  const account = loadAccount();

  const onAccept = async () => {
    if (!token) return;
    setState("joining");
    try {
      const team = await acceptTeamInvite(token);
      setTeamName(team.name);
      setState("joined");
    } catch (e: any) {
      let msg = e?.message || "Could not accept this invitation.";
      if (e?.status === 403) msg = "This invite was sent to a different email address. Sign in as that address to accept it.";
      else if (e?.status === 409) msg = "You're already on a team, or the team is full.";
      else if (e?.status === 404) msg = "This invitation is no longer valid.";
      setErrorMsg(msg);
      setState("error");
    }
  };

  return (
    <Screen scroll title="Join a team">
      <Hero
        eyebrow="TEAM INVITE"
        title="Join a team"
        subtitle="Accept your invitation to share a 508 Agent subscription."
      />

      <View style={{ marginTop: 16 }}>
        {!token ? (
          <Card style={{ borderColor: theme.colors.danger, borderWidth: 2 }}>
            <Text style={[theme.typography.stat, { color: theme.colors.danger }]}>Missing invite link</Text>
            <Text style={{ color: theme.colors.textMuted, marginTop: 8 }}>
              This page needs an invitation token. Use the link from your invite email.
            </Text>
          </Card>
        ) : state === "joined" ? (
          <Card style={{ borderColor: OK_GREEN, borderWidth: 2 }}>
            <Text style={[theme.typography.stat, { color: OK_GREEN }]}>You're in</Text>
            <Text style={{ color: theme.colors.textMuted, marginTop: 8, marginBottom: 12 }}>
              You joined {teamName ? `“${teamName}”` : "the team"}. You now share the team's credits and free certificates.
            </Text>
            <Button title="Go to your team" onPress={() => router.push("/team" as any)} />
          </Card>
        ) : state === "error" ? (
          <Card style={{ borderColor: theme.colors.danger, borderWidth: 2 }}>
            <Text style={[theme.typography.stat, { color: theme.colors.danger }]}>Couldn't accept invite</Text>
            <Text style={{ color: theme.colors.textMuted, marginTop: 8, marginBottom: 12 }}>{errorMsg}</Text>
            <Button title="Try again" variant="secondary" onPress={() => setState("idle")} />
          </Card>
        ) : signedIn ? (
          <Card>
            <Text style={[theme.typography.stat, { color: theme.colors.text }]}>Accept your invitation</Text>
            <Text style={{ color: theme.colors.textMuted, marginTop: 8, marginBottom: 12, lineHeight: 20 }}>
              You're signed in as {account?.email || "your account"}. The invite must match this email address.
            </Text>
            <Button
              title={state === "joining" ? "Joining…" : "Accept invitation"}
              onPress={onAccept}
              loading={state === "joining"}
              disabled={state === "joining"}
            />
          </Card>
        ) : (
          <Card>
            <Text style={[theme.typography.stat, { color: theme.colors.text }]}>Sign in to accept</Text>
            <Text style={{ color: theme.colors.textMuted, marginTop: 8, marginBottom: 12, lineHeight: 20 }}>
              Sign in (or create an account) with the email your invitation was sent to, then accept.
            </Text>
            <Button title="Sign in" onPress={() => setSignInOpen(true)} />
            <SignInModal open={signInOpen} onCancel={() => setSignInOpen(false)} />
          </Card>
        )}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({});
