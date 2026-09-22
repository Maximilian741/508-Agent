/**
 * Account profile screen.
 *
 * Hero with display name, big credit-balance panel, recent activity rows
 * (hairline-separated, not card grid), and a Sign out at the bottom.
 *
 * If the user is signed out, a centered empty state offers Sign in.
 */

import React, { useEffect, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useRouter } from "expo-router";

import {
  Account,
  HistoryEntry,
  deleteAccount,
  exportData,
  loadAccount,
  refreshAccount,
  requestEmailVerification,
  setPassword as setAccountPassword,
  signOut,
  updateProfile,
} from "../src/domain/account";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { EmptyState } from "../src/ui/components/EmptyState";
import { Hero } from "../src/ui/components/Hero";
import { Icon, IconName } from "../src/ui/components/Icon";
import { Screen } from "../src/ui/components/Screen";
import { Spinner } from "../src/ui/components/Spinner";
import { SignInModal } from "../src/ui/components/SignInModal";
import { useToast } from "../src/ui/toast";
import { useTheme } from "../src/ui/useTheme";

/** The backend's re-auth codes, in words a person can act on. */
function authErrorText(message?: string): string {
  const msg = message || "";
  if (msg.includes("current_password_required")) {
    return "Enter your current password to make this change.";
  }
  if (msg.includes("invalid_current_password")) {
    return "That current password is not right.";
  }
  return msg;
}

export default function AccountScreen() {
  const theme = useTheme();
  const router = useRouter();
  const toast = useToast();
  const [account, setAccount] = useState<Account | null>(() => loadAccount());
  const [signInOpen, setSignInOpen] = useState(false);

  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [editCurrentPw, setEditCurrentPw] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);

  const [exporting, setExporting] = useState(false);

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [deleting, setDeleting] = useState(false);

  const [pwOpen, setPwOpen] = useState(false);
  const [pwValue, setPwValue] = useState("");
  const [pwCurrent, setPwCurrent] = useState("");
  const [pwConfirm, setPwConfirm] = useState("");
  const [pwSaving, setPwSaving] = useState(false);

  const [verifyRequesting, setVerifyRequesting] = useState(false);
  const [verifyHint, setVerifyHint] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState<boolean>(() => loadAccount() !== null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const fresh = await refreshAccount();
        if (cancelled) return;
        if (fresh) setAccount(fresh);
      } catch (e: any) {
        if (cancelled) return;
        console.warn("[account] refresh failed", e);
        toast.error("Account refresh failed", {
          description: e?.message ?? "Could not reach the account service.",
        });
      } finally {
        if (!cancelled) setRefreshing(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [toast]);

  if (!account && refreshing) {
    return (
      <Screen scroll title="Account">
        <View style={styles.emptyWrap}>
          <Spinner />
          <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 12 }]}>
            Looking you up...
          </Text>
        </View>
      </Screen>
    );
  }

  if (!account) {
    return (
      <Screen scroll title="Account">
        <View style={styles.emptyWrap}>
          <EmptyState
            icon="user"
            title="Not signed in"
            message="Sign in to track audits, manage credits, and resume work across sessions."
            actionLabel="Sign in"
            onAction={() => setSignInOpen(true)}
          />
        </View>
        <SignInModal open={signInOpen} onCancel={() => setSignInOpen(false)} />
      </Screen>
    );
  }

  const memberSince = formatMemberSince(account.createdAt);

  const onSignOut = () => {
    signOut();
    if (Platform.OS === "web" && typeof window !== "undefined") {
      window.location.reload();
    } else {
      setAccount(null);
    }
  };

  return (
    <Screen scroll title="Account">
      <Hero
        eyebrow="ACCOUNT"
        title={account.displayName}
        subtitle={account.email + "  -  Member since " + memberSince}
      />

      <Card>
        <View style={styles.balanceRow}>
          <View style={{ flex: 1 }}>
            <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
              Balance
            </Text>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 10, marginTop: 4 }}>
              <Icon name="credits" size={26} color={theme.colors.accent} />
              <Text
                style={[
                  theme.typography.stat,
                  { color: theme.colors.text },
                ]}
              >
                {account.credits}
              </Text>
            </View>
            <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 2 }}>
              credits remaining
            </Text>
          </View>
          <View style={styles.balanceCta}>
            <Button
              title="Buy more"
              variant="primary"
              onPress={() => router.push("/billing" as any)}
            />
          </View>
        </View>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text, marginBottom: 12 }]}>
          Recent activity
        </Text>
        {account.history.length === 0 ? (
          <Text style={{ color: theme.colors.textMuted, fontSize: 13 }}>
            No activity yet. Buy credits or run an audit to get started.
          </Text>
        ) : (
          <View>
            {account.history.map((h, idx) => (
              <ActivityRow
                key={h.id}
                entry={h}
                isLast={idx === account.history.length - 1}
              />
            ))}
          </View>
        )}
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text, marginBottom: 4 }]}>
          Manage account
        </Text>
        <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginBottom: 14 }}>
          Edit your profile, export your data, or delete your account.
        </Text>

        {!editOpen ? (
          <View style={styles.manageRow}>
            <Button
              title="Edit profile"
              variant="secondary"
              onPress={() => {
                setEditName(account.displayName);
                setEditEmail(account.email);
                setEditOpen(true);
              }}
            />
            <Button
              title={exporting ? "Exporting..." : "Export my data"}
              variant="secondary"
              disabled={exporting}
              onPress={async () => {
                if (exporting) return;
                setExporting(true);
                try {
                  const blob = await exportData();
                  if (Platform.OS === "web" && typeof window !== "undefined") {
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = "508-account-export-" + Date.now() + ".json";
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    setTimeout(() => URL.revokeObjectURL(url), 1000);
                  }
                  toast.success("Export ready", { description: "Your data download is starting." });
                } catch (err: any) {
                  toast.error("Export failed", {
                    description: err?.message || "Could not export your data.",
                  });
                } finally {
                  setExporting(false);
                }
              }}
            />
            <Button
              title="Delete account"
              variant="danger"
              onPress={() => {
                // Opens the in-card "type DELETE to confirm" box — that IS the
                // confirmation. (A separate window.confirm here was a redundant
                // double-prompt.)
                setDeleteConfirm("");
                setDeleteOpen(true);
              }}
            />
          </View>
        ) : (
          <View style={styles.editForm}>
            <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginBottom: 4 }}>
              Display name
            </Text>
            <TextInput
              value={editName}
              onChangeText={setEditName}
              placeholder="Your name"
              placeholderTextColor={theme.colors.textMuted}
              style={[
                styles.input,
                {
                  borderColor: theme.colors.border,
                  color: theme.colors.text,
                  backgroundColor: theme.colors.surface,
                },
              ]}
            />
            <Text
              style={{
                color: theme.colors.textMuted,
                fontSize: 12,
                marginTop: 12,
                marginBottom: 4,
              }}
            >
              Email
            </Text>
            <TextInput
              value={editEmail}
              onChangeText={setEditEmail}
              autoCapitalize="none"
              keyboardType="email-address"
              placeholder="you@example.com"
              placeholderTextColor={theme.colors.textMuted}
              style={[
                styles.input,
                {
                  borderColor: theme.colors.border,
                  color: theme.colors.text,
                  backgroundColor: theme.colors.surface,
                },
              ]}
            />
            {account.hasPassword ? (
              <>
                <Text
                  style={{
                    color: theme.colors.textMuted,
                    fontSize: 12,
                    marginTop: 12,
                    marginBottom: 4,
                  }}
                >
                  Current password — needed to change your email
                </Text>
                <TextInput
                  value={editCurrentPw}
                  onChangeText={setEditCurrentPw}
                  secureTextEntry
                  autoCapitalize="none"
                  placeholder="Your current password"
                  placeholderTextColor={theme.colors.textMuted}
                  accessibilityLabel="Current password"
                  style={[
                    styles.input,
                    {
                      borderColor: theme.colors.border,
                      color: theme.colors.text,
                      backgroundColor: theme.colors.surface,
                    },
                  ]}
                />
              </>
            ) : null}
            <View style={styles.editButtonsRow}>
              <Button
                title={savingProfile ? "Saving..." : "Save"}
                variant="primary"
                disabled={savingProfile}
                onPress={async () => {
                  if (savingProfile) return;
                  const nameTrim = editName.trim();
                  const emailTrim = editEmail.trim();
                  if (!nameTrim && !emailTrim) {
                    toast.warning("Nothing to save", { description: "Pick a name or email." });
                    return;
                  }
                  setSavingProfile(true);
                  try {
                    const patch: {
                      displayName?: string;
                      email?: string;
                      currentPassword?: string;
                    } = {};
                    if (nameTrim && nameTrim !== account.displayName) patch.displayName = nameTrim;
                    if (emailTrim && emailTrim !== account.email) patch.email = emailTrim;
                    if (!patch.displayName && !patch.email) {
                      toast.info("No changes", { description: "Profile is already up to date." });
                      setEditOpen(false);
                      setSavingProfile(false);
                      return;
                    }
                    // Only an email change needs it; a rename does not.
                    if (patch.email && editCurrentPw.trim()) {
                      patch.currentPassword = editCurrentPw.trim();
                    }
                    await updateProfile(patch);
                    setEditCurrentPw("");
                    const fresh = await refreshAccount();
                    if (fresh) setAccount(fresh);
                    toast.success("Profile updated");
                    setEditOpen(false);
                  } catch (err: any) {
                    toast.error("Update failed", {
                      description:
                        authErrorText(err?.message) || "Could not save your changes.",
                    });
                  } finally {
                    setSavingProfile(false);
                  }
                }}
              />
              <Button
                title="Cancel"
                variant="ghost"
                disabled={savingProfile}
                onPress={() => setEditOpen(false)}
              />
            </View>
          </View>
        )}

        {deleteOpen && (
          <View
            style={[
              styles.deleteBox,
              { borderColor: theme.colors.danger ?? theme.colors.warning },
            ]}
          >
            <Text style={{ color: theme.colors.text, fontWeight: "700", marginBottom: 6 }}>
              Type DELETE to continue
            </Text>
            <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginBottom: 10 }}>
              This permanently removes your profile, credits, and history.
            </Text>
            <TextInput
              value={deleteConfirm}
              onChangeText={setDeleteConfirm}
              autoCapitalize="characters"
              placeholder="DELETE"
              placeholderTextColor={theme.colors.textMuted}
              style={[
                styles.input,
                {
                  borderColor: theme.colors.border,
                  color: theme.colors.text,
                  backgroundColor: theme.colors.surface,
                },
              ]}
            />
            <View style={styles.editButtonsRow}>
              <Button
                title={deleting ? "Deleting..." : "Permanently delete"}
                variant="danger"
                disabled={deleting || deleteConfirm.trim().toUpperCase() !== "DELETE"}
                onPress={async () => {
                  if (deleting) return;
                  if (deleteConfirm.trim().toUpperCase() !== "DELETE") {
                    toast.warning("Confirmation required", {
                      description: "Type DELETE to continue.",
                    });
                    return;
                  }
                  setDeleting(true);
                  try {
                    await deleteAccount();
                    toast.success("Account deleted");
                    // deleteAccount() reloads on web; native fallback:
                    setAccount(null);
                  } catch (err: any) {
                    toast.error("Delete failed", {
                      description: err?.message || "Could not delete your account.",
                    });
                  } finally {
                    setDeleting(false);
                  }
                }}
              />
              <Button
                title="Cancel"
                variant="ghost"
                disabled={deleting}
                onPress={() => {
                  setDeleteOpen(false);
                  setDeleteConfirm("");
                }}
              />
            </View>
          </View>
        )}
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text, marginBottom: 4 }]}>
          Security
        </Text>
        <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginBottom: 14 }}>
          Change your password and verify your email address. Verifying your
          email protects account recovery and unlocks the starter credit grant
          on hosted deployments.
        </Text>

        {!pwOpen ? (
          <View style={styles.manageRow}>
            <Button
              title={account.hasPassword ? "Change password" : "Set or change password"}
              variant="secondary"
              onPress={() => {
                setPwValue("");
                setPwConfirm("");
                setPwOpen(true);
              }}
            />
            {!account.emailVerifiedAt ? (
              <Button
                title={verifyRequesting ? "Sending..." : "Verify email"}
                variant="secondary"
                disabled={verifyRequesting}
                onPress={async () => {
                  if (verifyRequesting) return;
                  setVerifyRequesting(true);
                  setVerifyHint(null);
                  try {
                    const queued = await requestEmailVerification();
                    if (queued) {
                      setVerifyHint(
                        "We sent a verification link to your email — click it to finish. (Self-hosted without SMTP: the link prints to the server console.)",
                      );
                      toast.success("Verification sent", {
                        description: "Check your inbox for the link.",
                      });
                    } else {
                      toast.error("Could not request verification");
                    }
                  } catch (err: any) {
                    toast.error("Verification failed", {
                      description: err?.message || "Try again.",
                    });
                  } finally {
                    setVerifyRequesting(false);
                  }
                }}
              />
            ) : (
              <View
                style={{
                  flexDirection: "row",
                  alignItems: "center",
                  gap: 6,
                  paddingHorizontal: 12,
                  paddingVertical: 8,
                }}
              >
                <Icon name="check" size={16} color={theme.colors.success} />
                <Text style={{ color: theme.colors.success, fontSize: 13, fontWeight: "600" }}>
                  Email verified
                </Text>
              </View>
            )}
          </View>
        ) : (
          <View style={styles.editForm}>
            {account.hasPassword ? (
              <>
                <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginBottom: 4 }}>
                  Current password
                </Text>
                <TextInput
                  value={pwCurrent}
                  onChangeText={setPwCurrent}
                  secureTextEntry
                  autoCapitalize="none"
                  placeholder="Your current password"
                  placeholderTextColor={theme.colors.textMuted}
                  accessibilityLabel="Current password"
                  style={[
                    styles.input,
                    {
                      borderColor: theme.colors.border,
                      color: theme.colors.text,
                      backgroundColor: theme.colors.surface,
                    },
                  ]}
                />
              </>
            ) : null}
            <Text
              style={{
                color: theme.colors.textMuted,
                fontSize: 12,
                marginTop: account.hasPassword ? 12 : 0,
                marginBottom: 4,
              }}
            >
              New password
            </Text>
            <TextInput
              value={pwValue}
              onChangeText={setPwValue}
              secureTextEntry
              autoCapitalize="none"
              placeholder="At least 8 characters"
              placeholderTextColor={theme.colors.textMuted}
              style={[
                styles.input,
                {
                  borderColor: theme.colors.border,
                  color: theme.colors.text,
                  backgroundColor: theme.colors.surface,
                },
              ]}
            />
            <Text
              style={{
                color: theme.colors.textMuted,
                fontSize: 12,
                marginTop: 12,
                marginBottom: 4,
              }}
            >
              Confirm password
            </Text>
            <TextInput
              value={pwConfirm}
              onChangeText={setPwConfirm}
              secureTextEntry
              autoCapitalize="none"
              placeholder="Repeat new password"
              placeholderTextColor={theme.colors.textMuted}
              style={[
                styles.input,
                {
                  borderColor: theme.colors.border,
                  color: theme.colors.text,
                  backgroundColor: theme.colors.surface,
                },
              ]}
            />
            <View style={styles.editButtonsRow}>
              <Button
                title={pwSaving ? "Saving..." : "Save password"}
                variant="primary"
                disabled={pwSaving}
                onPress={async () => {
                  if (pwSaving) return;
                  if (!pwValue || pwValue.length < 8) {
                    toast.warning("Password too short", {
                      description: "Use at least 8 characters.",
                    });
                    return;
                  }
                  if (pwValue !== pwConfirm) {
                    toast.warning("Passwords don't match");
                    return;
                  }
                  setPwSaving(true);
                  try {
                    const next = await setAccountPassword(
                      pwValue,
                      account.hasPassword ? pwCurrent : undefined,
                    );
                    setAccount(next);
                    toast.success("Password updated");
                    setPwOpen(false);
                    setPwValue("");
                    setPwCurrent("");
                    setPwConfirm("");
                  } catch (err: any) {
                    toast.error("Could not save password", {
                      description: authErrorText(err?.message) || "Try again.",
                    });
                  } finally {
                    setPwSaving(false);
                  }
                }}
              />
              <Button
                title="Cancel"
                variant="ghost"
                disabled={pwSaving}
                onPress={() => {
                  setPwOpen(false);
                  setPwValue("");
                  setPwCurrent("");
                  setPwConfirm("");
                }}
              />
            </View>
          </View>
        )}

        {verifyHint ? (
          <Text
            style={{
              color: theme.colors.textMuted,
              fontSize: 12,
              marginTop: 12,
            }}
          >
            {verifyHint}
          </Text>
        ) : null}
      </Card>

      <View style={styles.signOutWrap}>
        <Button title="Sign out" variant="ghost" onPress={onSignOut} />
      </View>
    </Screen>
  );
}

function ActivityRow({ entry, isLast }: { entry: HistoryEntry; isLast: boolean }) {
  const theme = useTheme();
  const tone = toneFor(entry.kind);
  const iconColor =
    tone === "success"
      ? theme.colors.success
      : tone === "warning"
      ? theme.colors.warning
      : theme.colors.info;
  // A deferred remediation debit ("spend_once") is a spend like any other to
  // the reader — only the ledger bookkeeping differs.
  const isSpend = entry.kind === "spend" || entry.kind === "spend_once";
  const iconName: IconName =
    entry.kind === "purchase"
      ? "credit-card"
      : isSpend
      ? "zap"
      : entry.kind === "grant"
      ? "gift"
      : entry.kind === "refund"
      ? "rotate-ccw"
      : "credit-card";
  const amountText = (entry.amount > 0 ? "+" : "") + entry.amount;
  const amountColor = isSpend ? theme.colors.warning : theme.colors.success;

  return (
    <View
      style={[
        styles.row,
        !isLast && {
          borderBottomWidth: StyleSheet.hairlineWidth,
          borderBottomColor: theme.colors.border,
        },
      ]}
    >
      <View style={styles.kindIcon}>
        <Icon name={iconName} size={16} color={iconColor} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={{ color: theme.colors.text, fontWeight: "600", fontSize: 13 }}>
          {entry.description}
        </Text>
        <Text style={{ color: theme.colors.textMuted, fontSize: 11, marginTop: 2 }}>
          {relativeTime(entry.at)}
        </Text>
      </View>
      <Text
        style={{
          color: amountColor,
          fontWeight: "800",
          fontSize: 14,
          fontVariant: ["tabular-nums"],
        }}
      >
        {amountText}
      </Text>
    </View>
  );
}

function toneFor(kind: HistoryEntry["kind"]): "success" | "warning" | "info" {
  if (kind === "purchase") return "success";
  if (kind === "spend" || kind === "spend_once") return "warning";
  return "info";
}

function formatMemberSince(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return "recently";
  }
}

function relativeTime(iso: string): string {
  try {
    const then = new Date(iso).getTime();
    const now = Date.now();
    const diff = Math.max(0, now - then);
    const sec = Math.floor(diff / 1000);
    if (sec < 60) return "just now";
    const min = Math.floor(sec / 60);
    if (min < 60) return min + "m ago";
    const hr = Math.floor(min / 60);
    if (hr < 24) return hr + "h ago";
    const day = Math.floor(hr / 24);
    if (day < 30) return day + "d ago";
    const mo = Math.floor(day / 30);
    if (mo < 12) return mo + "mo ago";
    const yr = Math.floor(day / 365);
    return yr + "y ago";
  } catch {
    return "";
  }
}

const styles = StyleSheet.create({
  emptyWrap: {
    paddingVertical: 80,
    alignItems: "center",
    justifyContent: "center",
  },
  balanceRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 16,
  },
  balanceCta: {
    alignItems: "flex-end",
    justifyContent: "center",
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 12,
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
  },
  kindIcon: {
    width: 22,
    alignItems: "center",
    justifyContent: "center",
  },
  signOutWrap: {
    marginTop: 24,
    alignItems: "center",
  },
  manageRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  editForm: {
    gap: 0,
  },
  editButtonsRow: {
    flexDirection: "row",
    gap: 10,
    marginTop: 14,
  },
  input: {
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 10,
    fontSize: 14,
  },
  deleteBox: {
    marginTop: 16,
    borderWidth: 1,
    padding: 14,
  },
});
