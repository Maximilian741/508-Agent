/**
 * Persistent top navigation strip (a glass bar).
 *
 * Brand lockup on the left: the pixel "508" mark followed by the word
 * "Agent", with ONE accessible name, "508 Agent" (the mark is the 508;
 * repeating "508" in text was the clunky double brand). Route pills in the
 * middle, account chip + connection chip on the right. The account chip has
 * two states: signed in (display name + credits with a dropdown menu) and
 * signed out (a primary button that opens SignInModal).
 */
import React, { useEffect, useRef, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter, usePathname } from "expo-router";

import { Account, loadAccount, onAccountChanged, refreshAccount, signOut } from "../../domain/account";
import { useAppStore } from "../../store/useAppStore";
import { useTheme } from "../useTheme";
import { alpha, glassStyle } from "../theme";
import { Chip } from "./Chip";
import { Icon } from "./Icon";
import { PixelLogo } from "./PixelLogo";
import { CONTENT_MAX_WIDTH } from "./Screen";
import { SignInModal } from "./SignInModal";
import { linkProps } from "./linkProps";

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
          ...(glassStyle(theme.colors, 20) as any),
          borderBottomColor: theme.colors.glassBorder,
        },
      ]}
    >
      <View style={styles.inner}>
      <Pressable
        {...linkProps("/")}
        // One name for one brand. It contains the visible word "Agent"
        // (WCAG 2.5.3 Label in Name) and says the mark's "508" aloud.
        accessibilityLabel="508 Agent"
        style={({ focused }: any) => [
          styles.brand,
          { borderRadius: theme.radius.sm },
          focused ? ({ outlineColor: theme.colors.accent, outlineWidth: 2, outlineStyle: "solid", outlineOffset: 2 } as any) : null,
        ]}
      >
        <View
          // The pixel mark IS the "508" of the lockup; the link's name carries it.
          // @ts-ignore - web aria
          aria-hidden={true}
          importantForAccessibility="no-hide-descendants"
          style={styles.logoFrame}
        >
          <PixelLogo size={3} color={theme.colors.accent} showGrid={false} />
        </View>
        <Text style={[styles.brandText, { color: theme.colors.text }]}>Agent</Text>
      </Pressable>

      {/* data-nav-links: on narrow screens the base CSS (app/+html.tsx) turns
          this row into its own full-width, horizontally scrolling strip
          instead of a three-line wall of links. */}
      <View style={styles.links} {...({ dataSet: { navLinks: "1" } } as any)}>
        {ITEMS.map((item) => {
          const active = pathname === item.href || (item.href === "/" && pathname === "/index");
          return (
            <Pressable
              key={item.key}
              {...linkProps(item.href)}
              accessibilityLabel={"Go to " + item.label}
              // A link to the current page is aria-current, not aria-selected
              // (selected is only valid on tabs/options/grid cells).
              {...({ "aria-current": active ? "page" : undefined } as any)}
              style={({ focused, hovered }: any) => [
                styles.link,
                {
                  borderRadius: theme.radius.pill,
                  borderColor: active ? alpha(theme.colors.accent, 0.35) : "transparent",
                  backgroundColor: active ? theme.colors.accentSoft : hovered ? theme.colors.surface2 : "transparent",
                },
                focused ? ({ outlineColor: theme.colors.accent, outlineWidth: 2, outlineStyle: "solid", outlineOffset: 2 } as any) : null,
              ]}
            >
              <Text
                style={{
                  color: active ? theme.colors.accent : theme.colors.textMuted,
                  fontWeight: active ? "600" : "500",
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
                borderRadius: 3,
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
    // A sign-up or credit change made inline on a page (the home fixer)
    // shows up here at once.
    const off = onAccountChanged(() => {
      if (!cancelled) setAccount(loadAccount());
    });
    return () => {
      cancelled = true;
      off();
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
          <Text style={{ color: theme.colors.onAccent, fontWeight: "700", fontSize: 12 }}>Sign in</Text>
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
          <Icon name="user" size={14} color={theme.colors.onAccent} />
        </View>
        <Text
          style={{ color: theme.colors.text, fontWeight: "700", fontSize: 12 }}
          numberOfLines={1}
        >
          {account.displayName}
        </Text>
        <View style={[styles.creditPill, { borderRadius: theme.radius.pill, backgroundColor: theme.colors.accent + "22" }]}>
          <Text style={{ color: theme.colors.accent, fontSize: 11, fontWeight: "700", fontVariant: ["tabular-nums"] }}>
            {creditsLabel}
          </Text>
        </View>
        <Icon name="chevron-down" size={14} color={theme.colors.textMuted} />
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
                borderColor: theme.colors.glassBorder,
                // @ts-ignore web shadow
                boxShadow: theme.isDark ? theme.shadows.far.webDark : theme.shadows.far.web,
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
        { borderRadius: theme.radius.sm },
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
    borderBottomWidth: 1,
    zIndex: 10,
  },
  inner: {
    width: "100%",
    maxWidth: CONTENT_MAX_WIDTH,
    alignSelf: "center",
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 20,
    paddingVertical: 10,
    gap: 18,
    flexWrap: "wrap",
  },
  brand: { flexDirection: "row", alignItems: "center", gap: 9, paddingVertical: 4, paddingRight: 4 },
  logoFrame: { paddingVertical: 2 },
  brandText: { fontSize: 17, fontWeight: "700", letterSpacing: -0.3, lineHeight: 20 },
  links: { flexDirection: "row", gap: 2, alignItems: "center", flexShrink: 1, flexWrap: "wrap" },
  link: {
    paddingVertical: 6,
    paddingHorizontal: 12,
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
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
  },
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
    padding: 4,
    zIndex: 9999,
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
