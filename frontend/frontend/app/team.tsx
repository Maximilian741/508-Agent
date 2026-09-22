/**
 * Team screen: create a team (active subscribers), invite teammates up to the
 * plan's seat limit, and manage members. Everyone on a team shares the owner's
 * subscription benefit and credit wallet.
 */

import React, { useEffect, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";

import {
  Account,
  Team,
  acceptTeamInvite,
  createTeam,
  disbandTeam,
  getMyTeam,
  inviteTeamMember,
  leaveTeam,
  loadAccount,
  refreshAccount,
  removeTeamMember,
  revokeTeamInvite,
} from "../src/domain/account";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Hero } from "../src/ui/components/Hero";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";
import { useToast } from "../src/ui/toast";

export default function TeamScreen() {
  const theme = useTheme();
  const toast = useToast();
  const router = useRouter();

  const [account, setAccount] = useState<Account | null>(() => loadAccount());
  const [loading, setLoading] = useState(true);
  const [team, setTeam] = useState<Team | null>(null);
  const [canCreate, setCanCreate] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const [newName, setNewName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");

  const reload = async () => {
    const res = await getMyTeam();
    setTeam(res.team);
    setCanCreate(res.canCreate);
    setLoading(false);
  };

  useEffect(() => {
    void refreshAccount().then((a) => a && setAccount(a));
    void reload();
  }, []);

  const myId = account?.id;
  const isOwner = !!team && team.ownerId === myId;
  const isAdmin = !!team && team.role === "admin";

  const onCreate = async () => {
    const name = newName.trim();
    if (!name) {
      toast.error("Give your team a name");
      return;
    }
    setBusy("create");
    try {
      const t = await createTeam(name);
      setTeam(t);
      setCanCreate(false);
      setNewName("");
      toast.success("Team created", { description: `You have ${t.seatLimit} seats to fill.` });
    } catch (e: any) {
      if (e?.status === 402) {
        toast.error("A subscription is required", { description: "Subscribe to a Team or Business plan first." });
        router.push("/billing" as any);
      } else {
        toast.error("Couldn't create team", { description: e?.message || "Try again." });
      }
    } finally {
      setBusy(null);
    }
  };

  const onInvite = async () => {
    const email = inviteEmail.trim().toLowerCase();
    if (!email || !email.includes("@")) {
      toast.error("Enter a valid email");
      return;
    }
    setBusy("invite");
    try {
      const inv = await inviteTeamMember(email);
      setInviteEmail("");
      await reload();
      const link = _absolute(inv.acceptUrl);
      if (link && (await _copy(link))) {
        toast.success("Invite sent", { description: "Invite link copied to your clipboard." });
      } else {
        toast.success("Invite sent", { description: `We emailed ${email}.` });
      }
    } catch (e: any) {
      if (e?.status === 409) {
        toast.error("No seats left", { description: e?.message || "Remove a member or upgrade your plan." });
      } else {
        toast.error("Couldn't send invite", { description: e?.message || "Try again." });
      }
    } finally {
      setBusy(null);
    }
  };

  const onCopyInvite = async (url?: string | null) => {
    const link = _absolute(url);
    if (link && (await _copy(link))) toast.success("Invite link copied");
    else toast.error("Couldn't copy link");
  };

  const onRevoke = async (inviteId: string) => {
    setBusy("revoke:" + inviteId);
    const ok = await revokeTeamInvite(inviteId);
    if (ok) await reload();
    else toast.error("Couldn't revoke invite");
    setBusy(null);
  };

  const onRemove = async (userId: string) => {
    setBusy("remove:" + userId);
    const ok = await removeTeamMember(userId);
    if (ok) await reload();
    else toast.error("Couldn't remove member");
    setBusy(null);
  };

  const onLeave = async () => {
    setBusy("leave");
    const ok = await leaveTeam();
    if (ok) {
      toast.success("You left the team");
      await reload();
    } else {
      toast.error("Couldn't leave the team");
    }
    setBusy(null);
  };

  const onDisband = async () => {
    setBusy("disband");
    const ok = await disbandTeam();
    if (ok) {
      toast.success("Team disbanded");
      setTeam(null);
      await reload();
    } else {
      toast.error("Couldn't disband the team");
    }
    setBusy(null);
  };

  const inputStyle = {
    backgroundColor: theme.colors.surface2,
    borderColor: theme.colors.border,
    borderWidth: 1,
    borderRadius: theme.radius.xs,
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: theme.colors.text,
    fontSize: 14,
    flex: 1,
    minWidth: 200,
  } as const;

  return (
    <Screen scroll title="Team">
      <Hero
        eyebrow="TEAM SEATS"
        title="Your team"
        subtitle="Share one subscription's credits and free certificates across your whole team."
      />

      <View style={{ marginTop: 16, gap: 16 }}>
        {loading ? (
          <Card>
            <Text style={{ color: theme.colors.textMuted }}>Loading your team…</Text>
          </Card>
        ) : team ? (
          <>
            {/* Overview */}
            <Card>
              <View style={styles.spread}>
                <View style={{ flex: 1 }}>
                  <Text style={[theme.typography.stat, { color: theme.colors.text }]}>{team.name}</Text>
                  <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 4 }}>
                    {team.seatsUsed} of {team.seatLimit} seats used · you are {team.role === "admin" ? "an admin" : "a member"}.
                  </Text>
                </View>
                {isOwner ? (
                  <Button
                    title={busy === "disband" ? "Disbanding…" : "Disband team"}
                    variant="danger"
                    onPress={onDisband}
                    loading={busy === "disband"}
                    disabled={!!busy}
                  />
                ) : (
                  <Button
                    title={busy === "leave" ? "Leaving…" : "Leave team"}
                    variant="secondary"
                    onPress={onLeave}
                    loading={busy === "leave"}
                    disabled={!!busy}
                  />
                )}
              </View>
            </Card>

            {/* Members */}
            <Card>
              <Text style={[theme.typography.h2, { color: theme.colors.text, marginBottom: 8 }]}>Members</Text>
              <View style={{ gap: 8 }}>
                {team.members.map((m) => (
                  <View key={m.userId} style={[styles.memberRow, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2, borderRadius: theme.radius.none }]}>
                    <View style={{ flex: 1 }}>
                      <Text style={{ color: theme.colors.text, fontWeight: "700", fontSize: 13 }}>
                        {m.displayName || m.email || m.userId}
                        {m.userId === myId ? "  (you)" : ""}
                      </Text>
                      <Text style={{ color: theme.colors.textMuted, fontSize: 12 }}>
                        {m.email || "—"} · {m.isOwner ? "owner" : m.role}
                      </Text>
                    </View>
                    {isAdmin && !m.isOwner && m.userId !== myId ? (
                      <Button
                        title="Remove"
                        variant="ghost"
                        onPress={() => onRemove(m.userId)}
                        loading={busy === "remove:" + m.userId}
                        disabled={!!busy}
                      />
                    ) : null}
                  </View>
                ))}
              </View>
            </Card>

            {/* Invites (admins only) */}
            {isAdmin ? (
              <Card>
                <Text style={[theme.typography.h2, { color: theme.colors.text, marginBottom: 8 }]}>Invite teammates</Text>
                <View style={styles.inviteRow}>
                  <TextInput
                    value={inviteEmail}
                    onChangeText={setInviteEmail}
                    placeholder="teammate@company.com"
                    placeholderTextColor={theme.colors.textMuted}
                    autoCapitalize="none"
                    keyboardType="email-address"
                    style={inputStyle}
                    onSubmitEditing={onInvite}
                  />
                  <Button
                    title={busy === "invite" ? "Sending…" : "Send invite"}
                    onPress={onInvite}
                    loading={busy === "invite"}
                    disabled={!!busy || team.seatsUsed >= team.seatLimit}
                  />
                </View>
                {team.seatsUsed >= team.seatLimit ? (
                  <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 8 }}>
                    All {team.seatLimit} seats are used. Remove a member or upgrade your plan to invite more.
                  </Text>
                ) : null}

                {team.invites.length > 0 ? (
                  <View style={{ marginTop: 14, gap: 8 }}>
                    <Text style={{ color: theme.colors.textMuted, fontSize: 12, fontWeight: "700" }}>PENDING INVITES</Text>
                    {team.invites.map((inv) => (
                      <View key={inv.id} style={[styles.memberRow, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2, borderRadius: theme.radius.none }]}>
                        <View style={{ flex: 1 }}>
                          <Text style={{ color: theme.colors.text, fontSize: 13 }}>{inv.email}</Text>
                          <Text style={{ color: theme.colors.textMuted, fontSize: 11 }}>invited · {inv.role}</Text>
                        </View>
                        <Pressable onPress={() => onCopyInvite(inv.acceptUrl)} accessibilityRole="button" style={{ paddingHorizontal: 8, paddingVertical: 6 }}>
                          <Text style={{ color: theme.colors.accent, fontSize: 12, fontWeight: "700" }}>Copy link</Text>
                        </Pressable>
                        <Button
                          title="Revoke"
                          variant="ghost"
                          onPress={() => onRevoke(inv.id)}
                          loading={busy === "revoke:" + inv.id}
                          disabled={!!busy}
                        />
                      </View>
                    ))}
                  </View>
                ) : null}
              </Card>
            ) : null}
          </>
        ) : canCreate ? (
          <Card>
            <Text style={[theme.typography.stat, { color: theme.colors.text }]}>Create your team</Text>
            <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 6, marginBottom: 12, lineHeight: 20 }}>
              Your subscription includes multiple seats. Name your team, then invite teammates. Everyone shares your
              monthly credits and gets free certificates.
            </Text>
            <View style={styles.inviteRow}>
              <TextInput
                value={newName}
                onChangeText={setNewName}
                placeholder="Acme Accessibility"
                placeholderTextColor={theme.colors.textMuted}
                style={inputStyle}
                onSubmitEditing={onCreate}
              />
              <Button title={busy === "create" ? "Creating…" : "Create team"} onPress={onCreate} loading={busy === "create"} disabled={!!busy} />
            </View>
          </Card>
        ) : (
          <Card>
            <Text style={[theme.typography.stat, { color: theme.colors.text }]}>Team plans</Text>
            <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 6, marginBottom: 12, lineHeight: 20 }}>
              Team seats come with a Team or Business subscription. Subscribe to share one pool of credits and free
              certificates across up to 10 teammates.
            </Text>
            <Button title="See plans" onPress={() => router.push("/billing" as any)} />
          </Card>
        )}
      </View>
    </Screen>
  );
}

function _absolute(url?: string | null): string | null {
  if (!url) return null;
  if (/^https?:\/\//i.test(url)) return url;
  if (Platform.OS === "web" && typeof window !== "undefined" && url.startsWith("/")) {
    return window.location.origin + url;
  }
  return url;
}

async function _copy(text: string): Promise<boolean> {
  try {
    if (Platform.OS === "web" && typeof navigator !== "undefined" && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through
  }
  return false;
}

const styles = StyleSheet.create({
  spread: { flexDirection: "row", alignItems: "center", gap: 12 },
  memberRow: { flexDirection: "row", alignItems: "center", gap: 8, borderWidth: 1, padding: 10 },
  inviteRow: { flexDirection: "row", gap: 10, alignItems: "center", flexWrap: "wrap" },
});

export { TeamScreen };
