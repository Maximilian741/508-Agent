/**
 * Persistent top navigation strip.
 *
 * Logo on the left, route quick-jump destinations in the middle, account
 * chip + connection chip on the right. The account chip handles three
 * states: signed in (shows display name + credits with a dropdown menu),
 * and signed out (a primary-tinted button that opens SignInModal).
 */
import React, { useEffect, useRef, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter, usePathname } from "expo-router";

import { Account, loadAccount, refreshAccount, signOut } from "../../domain/account";
import { useAppStore } from "../../store/useAppStore";
import { useTheme } from "../useTheme";
import { Chip } from "./Chip";
import { PixelIcon } from "./PixelIcon";
import { PixelLogo } from "./PixelLogo";
import { SignInModal } from "./SignInModal";

const ITEMS: { label: string; href: string; key: string }[] = [
  { label: "Home", href: "/", key: "home" },
  { label: "Audit", href: "/audit", key: "audit" },
  { label: "Dashboard", href: "/dashboard", key: "dashboard" },
  { label: "Insights", href: "/insights", key: "insights" },
  { label: "Batch", href: "/batch", key: "batch" },
  { label: "Contrast", href: "/tools/contrast", key: "contrast" },
  { label: "Help", href: "/help", key: "help" },
  { label: "Achievements", href: "/achievements", key: "achievements" },
  { label: "About", href: "/about", key: "about" },
  { label: "Settings", href: "/settings", key: "settings" },
];

export function AppNav() {
  const theme = useTheme();
  const router = useRouter();
  const pathname = usePathname();
  const mockMode = useAppStore((s) => s.mockMode);
  const backendHealth = useAppStore((s) => s.backendHealth);

  return (
    <View
      // @ts-ignore
      accessibilityRole={Platform.OS === "web" ? ("navigation" as any) : undefined}
      style={[
        styles.bar,
        {
          backgroundColor: theme.colors.surface,
          borderBottomColor: theme.colors.border,
        },
      ]}
    >
      <Pressable accessibilityRole="button"
        onPress={() => router.push("/" as any)}
        // Must contain the visible wordmark (WCAG 2.5.3): a speech-input user
        // says what they see — "508 Agent" — not "Home".
        accessibilityLabel="508 Agent — home"
        style={({ focused }: any) => [styles.brand, focused ? ({ outlineColor: theme.colors.accent, outlineWidth: 2, outlineStyle: "solid", outlineOffset: 2 } as any) : null]}
      >
        <View style={[styles.logoFrame, { borderRadius: theme.radius.none }]}>
          <PixelLogo size={3} color={theme.colors.accent} />
        </View>
        <Text style={[theme.typography.pixel, styles.brandText, { color: theme.colors.text }]}>508 · AGENT</Text>
      </Pressable>

      <View
        accessibilityRole={Platform.OS === "web" ? ("separator" as any) : undefined}
        // @ts-ignore - aria-hidden on web
        aria-hidden={true}
        style={[styles.brandDivider, { backgroundColor: theme.colors.border }]}
      />

      <View style={styles.links}>
        {ITEMS.map((item) => {
          const active = pathname === item.href || (item.href === "/" && pathname === "/index");
          return (
            <Pressable accessibilityRole="button"
              key={item.key}
              onPress={() => router.push(item.href as any)}
              accessibilityLabel={"Go to " + item.label}
              accessibilityState={{ selected: active }}
              style={({ focused }: any) => [
                styles.link,
                {
                  borderRadius: theme.radius.none,
                  borderColor: active ? theme.colors.accent : "transparent",
                  backgroundColor: active ? theme.colors.accent + "1A" : "transparent",
                },
                focused ? ({ outlineColor: theme.colors.accent, outlineWidth: 2, outlineStyle: "solid", outlineOffset: 2 } as any) : null,
              ]}
            >
              <Text
                style={{
                  color: active ? theme.colors.accent : theme.colors.text,
                  fontWeight: active ? "700" : "500",
                  fontSize: 13,
                }}
              >
                {item.label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <View style={styles.right}>
        <AccountChip />
        <Chip
          label={
            mockMode
              ? "Demo data"
              : backendHealth === "ok"
              ? "Live"
              : backendHealth === "error"
              ? "Offline"
              : "Checking"
          }
          tone={
            mockMode
              ? "warning"
              : backendHealth === "ok"
              ? "success"
              : backendHealth === "error"
              ? "danger"
              : "default"
          }
          icon={
            <View
              style={{
                width: 6,
                height: 6,
                borderRadius: 0,
                backgroundColor: mockMode
                  ? theme.colors.warning
                  : backendHealth === "ok"
                  ? theme.colors.success
                  : backendHealth === "error"
                  ? theme.colors.danger
                  : theme.colors.textMuted,
              }}
            />
          }
        />
      </View>
    </View>
  );
}

function AccountChip() {
  const theme = useTheme();
  const router = useRouter();
  const [account, setAccount] = useState<Account | null>(() => loadAccount());
  const [open, setOpen] = useState(false);
  const [signInOpen, setSignInOpen] = useState(false);
  const chipRef = useRef<any>(null);
  const [anchor, setAnchor] = useState<{ top: number; right: number } | null>(null);

  const refresh = () => {
    setAccount(loadAccount());
  };

  useEffect(() => {
    let cancelled = false;
    refreshAccount()
      .then((fresh) => {
        if (cancelled) return;
        if (fresh) setAccount(fresh);
      })
      .catch((e) => console.warn("[AccountChip] refresh failed", e));
    return () => {
      cancelled = true;
    };
  }, []);

  const measureAnchor = () => {
    if (Platform.OS !== "web" || typeof window === "undefined") return;
    const el: any = chipRef.current;
    const node = el && (el.getBoundingClientRect ? el : el._node || el);
    const rect = node && node.getBoundingClientRect ? node.getBoundingClientRect() : null;
    if (rect) {
      setAnchor({
        top: rect.bottom + 6,
        right: Math.max(8, window.innerWidth - rect.right),
      });
    }
  };

  useEffect(() => {
    if (!open || Platform.OS !== "web") return;
    const onMove = () => measureAnchor();
    window.addEventListener("resize", onMove);
    window.addEventListener("scroll", onMove, true);
    return () => {
      window.removeEventListener("resize", onMove);
      window.removeEventListener("scroll", onMove, true);
    };
  }, [open]);

  const toggle = () => {
    refresh();
    if (!open) measureAnchor();
    setOpen((v) => !v);
  };

  if (!account) {
    return (
      <>
        <Pressable
          onPress={() => {
            setSignInOpen(true);
          }}
          accessibilityLabel="Sign in"
          accessibilityRole="button"
          style={({ focused, hovered, pressed }: any) => [
            styles.signInBtn,
            {
              borderRadius: theme.radius.pill,
              backgroundColor: pressed
                ? theme.colors.accentSecondary
                : hovered
                ? theme.colors.accent
                : theme.colors.accent,
              opacity: pressed ? 0.9 : 1,
            },
            focused ? ({ outlineColor: theme.colors.accent, outlineWidth: 2, outlineStyle: "solid", outlineOffset: 2 } as any) : null,
          ]}
        >
          <Text style={{ color: "#FFFFFF", fontWeight: "700", fontSize: 12 }}>Sign in</Text>
        </Pressable>
        <SignInModal open={signInOpen} onCancel={() => setSignInOpen(false)} />
      </>
    );
  }

  const initial = (account.displayName || account.email || "?").charAt(0).toUpperCase();
  const creditsLabel = account.credits + "cr";

  const onProfile = () => {
    setOpen(false);
    router.push("/account" as any);
  };
  const onBuy = () => {
    setOpen(false);
    router.push("/billing" as any);
  };
  const onTeam = () => {
    setOpen(false);
    router.push("/team" as any);
  };
  const onSignOut = () => {
    setOpen(false);
    signOut();
    if (Platform.OS === "web" && typeof window !== "undefined") {
      window.location.reload();
    } else {
      setAccount(null);
    }
  };

  return (
    <>
      <Pressable
        ref={chipRef}
        onPress={toggle}
        accessibilityLabel={"Account menu for " + account.displayName + ", " + account.credits + " credits"}
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        style={({ focused }: any) => [
          styles.acctChip,
          {
            borderRadius: theme.radius.pill,
            backgroundColor: theme.colors.surface2,
            borderColor: open ? theme.colors.accent : theme.colors.border,
          },
          focused ? ({ outlineColor: theme.colors.accent, outlineWidth: 2, outlineStyle: "solid", outlineOffset: 2 } as any) : null,
        ]}
      >
        <View style={[styles.avatar, { backgroundColor: theme.colors.accent }]}>
          <PixelIcon name="user" size={3} color="#FFFFFF" />
        </View>
        <Text
          style={{ color: theme.colors.text, fontWeight: "700", fontSize: 12 }}
          numberOfLines={1}
        >
          {account.displayName}
        </Text>
        <View style={[styles.creditPill, { borderRadius: theme.radius.pill, backgroundColor: theme.colors.accent + "22" }]}>
          <Text style={[theme.typography.pixel, { color: theme.colors.accent, fontSize: 11 }]}>
            {creditsLabel}
          </Text>
        </View>
        <Text style={{ color: theme.colors.textMuted, fontSize: 11 }}>v</Text>
      </Pressable>

      {open ? (
        <>
          <Pressable accessibilityRole="button"
            accessibilityLabel="Close account menu"
            onPress={() => setOpen(false)}
            style={styles.acctBackdrop}
          />
          <View
            style={[
              styles.acctMenu,
              {
                borderRadius: theme.radius.md,
                backgroundColor: theme.colors.surface,
                borderColor: theme.colors.border,
                top: anchor?.top ?? 56,
                right: anchor?.right ?? 16,
              },
            ]}
          >
            <View style={styles.acctMenuHeader}>
              <Text
                style={{ color: theme.colors.text, fontWeight: "700", fontSize: 13 }}
                numberOfLines={1}
              >
                {account.displayName}
              </Text>
              <Text
                style={{ color: theme.colors.textMuted, fontSize: 11 }}
                numberOfLines={1}
              >
                {account.email}
              </Text>
            </View>
            <View style={[styles.acctMenuDivider, { backgroundColor: theme.colors.border }]} />
            <MenuItem label="View profile" onPress={onProfile} />
            <MenuItem label="Buy credits" onPress={onBuy} />
            <MenuItem label="Team" onPress={onTeam} />
            <View style={[styles.acctMenuDivider, { backgroundColor: theme.colors.border }]} />
            <MenuItem label="Sign out" onPress={onSignOut} tone="danger" />
          </View>
        </>
      ) : null}
    </>
  );
}

function MenuItem({
  label,
  onPress,
  tone,
}: {
  label: string;
  onPress: () => void;
  tone?: "danger";
}) {
  const theme = useTheme();
  const color = tone === "danger" ? theme.colors.danger : theme.colors.text;
  return (
    <Pressable
      onPress={onPress}
      accessibilityLabel={label}
      accessibilityRole="button"
      style={({ hovered, focused }: any) => [
        styles.acctMenuItem,
        hovered ? { backgroundColor: theme.colors.surface2 } : null,
        focused ? ({ outlineColor: theme.colors.accent, outlineWidth: 2, outlineStyle: "solid", outlineOffset: 2 } as any) : null,
      ]}
    >
      <Text style={{ color, fontWeight: "600", fontSize: 13 }}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: 1,
    gap: 16,
    flexWrap: "wrap",
  },
  brand: { flexDirection: "row", alignItems: "center", gap: 10 },
  brandDivider: { width: 1, height: 22, marginHorizontal: 12, opacity: 0.6 },
  logoFrame: { padding: 4 },
  logo: {
    width: 28,
    height: 28,
    borderRadius: 4,
    alignItems: "center",
    justifyContent: "center",
  },
  logoText: { color: "#FFFFFF", fontWeight: "800", fontSize: 11, letterSpacing: 0.5 },
  brandText: { fontSize: 13, fontWeight: "800", letterSpacing: 1.4 },
  links: { flexDirection: "row", gap: 4, alignItems: "center", flexShrink: 1, flexWrap: "wrap" },
  link: {
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderWidth: 1,
  },
  right: {
    marginLeft: "auto",
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  signInBtn: {
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  acctChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 8,
    paddingVertical: 5,
    borderWidth: 1,
    maxWidth: 240,
  },
  avatar: {
    width: 24,
    height: 24,
    borderRadius: 0,
    alignItems: "center",
    justifyContent: "center",
  },
  avatarText: { color: "#FFFFFF", fontWeight: "800", fontSize: 11 },
  creditPill: {
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  acctBackdrop: {
    position: (Platform.OS === "web" ? "fixed" : "absolute") as any,
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "transparent",
    zIndex: 9998,
  },
  acctMenu: {
    position: (Platform.OS === "web" ? "fixed" : "absolute") as any,
    minWidth: 220,
    borderWidth: 1,
    paddingVertical: 4,
    zIndex: 9999,
    // @ts-ignore
    boxShadow: "0 8px 24px rgba(31, 20, 10, 0.18)",
  },
  acctMenuHeader: {
    paddingVertical: 8,
    paddingHorizontal: 12,
    gap: 2,
  },
  acctMenuItem: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 8,
    paddingHorizontal: 12,
  },
  acctMenuDivider: {
    height: 1,
    marginVertical: 4,
  },
});
