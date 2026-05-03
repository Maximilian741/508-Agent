/**
 * Persistent top navigation strip.
 *
 * Replaces the fragmented "buttons sprinkled at the bottom of every screen"
 * pattern.  Logo on the left, current screen indicator, and quick-jump
 * destinations on the right.  Adds an obvious System / Demo Mode badge.
 */

import React from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter, usePathname } from "expo-router";

import { useAppStore } from "../../store/useAppStore";
import { useTheme } from "../useTheme";
import { Chip } from "./Chip";

const ITEMS: { label: string; href: string; key: string }[] = [
  { label: "Home", href: "/", key: "home" },
  { label: "Audit", href: "/audit", key: "audit" },
  { label: "Batch", href: "/batch", key: "batch" },
  { label: "Contrast", href: "/tools/contrast", key: "contrast" },
  { label: "Help", href: "/help", key: "help" },
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
      // @ts-ignore — navigation landmark on web
      accessibilityRole={Platform.OS === "web" ? ("navigation" as any) : undefined}
      style={[
        styles.bar,
        {
          backgroundColor: theme.colors.surface,
          borderBottomColor: theme.colors.border,
        },
      ]}
    >
      <Pressable
        onPress={() => router.push("/")}
        accessibilityLabel="Home"
        style={styles.brand}
      >
        <View style={[styles.logo, { backgroundColor: theme.colors.accent }]}>
          <Text style={styles.logoText}>508</Text>
        </View>
        <Text style={[styles.brandText, { color: theme.colors.text }]}>508 Agent</Text>
      </Pressable>

      <View style={styles.links}>
        {ITEMS.map((item) => {
          const active = pathname === item.href || (item.href === "/" && pathname === "/index");
          return (
            <Pressable
              key={item.key}
              onPress={() => router.push(item.href as any)}
              accessibilityLabel={`Go to ${item.label}`}
              accessibilityState={{ selected: active }}
              style={[
                styles.link,
                {
                  borderColor: active ? theme.colors.accent : "transparent",
                  backgroundColor: active ? theme.colors.accent + "1A" : "transparent",
                },
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
        <Chip
          label={
            mockMode
              ? "Demo data"
              : backendHealth === "ok"
              ? "Live"
              : backendHealth === "error"
              ? "Offline"
              : "Checking…"
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
        />
      </View>
    </View>
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
  logo: {
    width: 28,
    height: 28,
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
  },
  logoText: { color: "#FFFFFF", fontWeight: "800", fontSize: 11, letterSpacing: 0.5 },
  brandText: { fontSize: 16, fontWeight: "800" },
  links: { flexDirection: "row", gap: 4, alignItems: "center", flexShrink: 1, flexWrap: "wrap" },
  link: {
    paddingVertical: 6,
    paddingHorizontal: 10,
    borderRadius: 8,
    borderWidth: 1,
  },
  right: { marginLeft: "auto" },
});
