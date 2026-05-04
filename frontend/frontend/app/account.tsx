/**
 * Account profile screen.
 *
 * Hero with display name, big credit-balance panel, recent activity rows
 * (hairline-separated, not card grid), and a Sign out at the bottom.
 *
 * If the user is signed out, a centered empty state offers Sign in.
 */

import React, { useState } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import {
  Account,
  HistoryEntry,
  loadAccount,
  signOut,
} from "../src/domain/account";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { EmptyState } from "../src/ui/components/EmptyState";
import { Hero } from "../src/ui/components/Hero";
import { PixelIcon, PixelGlyph } from "../src/ui/components/PixelIcon";
import { Screen } from "../src/ui/components/Screen";
import { SignInModal } from "../src/ui/components/SignInModal";
import { useTheme } from "../src/ui/useTheme";

export default function AccountScreen() {
  const theme = useTheme();
  const router = useRouter();
  const [account, setAccount] = useState<Account | null>(() => loadAccount());
  const [signInOpen, setSignInOpen] = useState(false);

  if (!account) {
    return (
      <Screen scroll title="Account">
        <View style={styles.emptyWrap}>
          <EmptyState
            icon="account-circle"
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
        shader="maple"
        shaderOpacity={0.45}
        eyebrow="Your account"
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
              <PixelIcon name="coin" size={5} color={theme.colors.accent} />
              <Text
                style={[
                  theme.typography.pixelLarge,
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
  const iconName: PixelGlyph =
    entry.kind === "purchase"
      ? "coin"
      : entry.kind === "spend"
      ? "bolt"
      : entry.kind === "grant"
      ? "spark"
      : entry.kind === "refund"
      ? "arrow_left"
      : "coin";
  const amountText = (entry.amount > 0 ? "+" : "") + entry.amount;
  const amountColor =
    entry.kind === "spend" ? theme.colors.warning : theme.colors.success;

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
        <PixelIcon name={iconName} size={3} color={iconColor} />
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
  if (kind === "spend") return "warning";
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
});
