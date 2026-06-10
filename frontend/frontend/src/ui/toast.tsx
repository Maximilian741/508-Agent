/**
 * Lightweight toast/snackbar system.
 *
 * Why custom: react-native-toast-message and friends pull in a lot for what
 * we need (3 colors, autodismiss, a stack).  This module is < 200 lines and
 * matches our theme exactly.
 *
 * Usage:
 *   const toast = useToast();
 *   toast.success("Approved");
 *   toast.error("Couldn't save", { description: err.message });
 *
 * Mount <ToastHost /> once, near the root of the app.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Animated, Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { useTheme } from "./useTheme";

export type ToastTone = "success" | "error" | "info" | "warning";

export interface ToastInput {
  tone?: ToastTone;
  title: string;
  description?: string;
  /** Override the default 3500ms autodismiss. Pass 0 to disable autodismiss. */
  durationMs?: number;
  /**
   * Optional collision key. When set, a new toast with the same dedupeKey
   * replaces any visible toast with the same key (no stacking). When unset,
   * toasts behave as before.
   */
  dedupeKey?: string;
}

interface ToastRecord extends ToastInput {
  id: string;
  createdAt: number;
}

type Listener = (toasts: ToastRecord[]) => void;

class ToastBus {
  private toasts: ToastRecord[] = [];
  private listeners = new Set<Listener>();
  private nextId = 1;

  push(input: ToastInput): string {
    const record: ToastRecord = {
      id: `toast-${this.nextId++}`,
      createdAt: Date.now(),
      tone: "info",
      ...input,
    };
    if (record.dedupeKey) {
      // Drop any visible toast with the same dedupeKey before pushing.
      this.toasts = this.toasts.filter((t) => t.dedupeKey !== record.dedupeKey);
    }
    this.toasts = [...this.toasts, record];
    this.emit();
    const ttl = record.durationMs ?? 3500;
    if (ttl > 0) {
      setTimeout(() => this.dismiss(record.id), ttl);
    }
    return record.id;
  }

  dismiss(id: string) {
    this.toasts = this.toasts.filter((t) => t.id !== id);
    this.emit();
  }

  clear() {
    this.toasts = [];
    this.emit();
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.toasts);
    return () => {
      this.listeners.delete(listener);
    };
  }

  private emit() {
    for (const listener of this.listeners) listener(this.toasts);
  }
}

const bus = new ToastBus();

export function useToast() {
  return useMemo(
    () => ({
      show: (input: ToastInput) => bus.push(input),
      success: (title: string, opts?: Omit<ToastInput, "title" | "tone">) =>
        bus.push({ ...opts, title, tone: "success" }),
      error: (title: string, opts?: Omit<ToastInput, "title" | "tone">) =>
        bus.push({ ...opts, title, tone: "error" }),
      info: (title: string, opts?: Omit<ToastInput, "title" | "tone">) =>
        bus.push({ ...opts, title, tone: "info" }),
      warning: (title: string, opts?: Omit<ToastInput, "title" | "tone">) =>
        bus.push({ ...opts, title, tone: "warning" }),
      dismiss: (id: string) => bus.dismiss(id),
      clear: () => bus.clear(),
    }),
    [],
  );
}

export function ToastHost() {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  useEffect(() => bus.subscribe(setToasts), []);
  if (toasts.length === 0) return null;
  return (
    <View
      pointerEvents="box-none"
      style={styles.host}
      // Screen readers must hear toast feedback — the approve/reject/error
      // workflow is toast-driven. Polite live region on web (aria-live),
      // accessibilityLiveRegion for native.
      accessibilityLiveRegion="polite"
      {...(Platform.OS === "web" ? ({ "aria-live": "polite", role: "status" } as any) : {})}
    >
      {toasts.map((t) => (
        <ToastView key={t.id} record={t} onDismiss={() => bus.dismiss(t.id)} />
      ))}
    </View>
  );
}

function ToastView({ record, onDismiss }: { record: ToastRecord; onDismiss: () => void }) {
  const theme = useTheme();
  const opacity = useRef(new Animated.Value(0)).current;
  const translateY = useRef(new Animated.Value(8)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(opacity, { toValue: 1, duration: 200, useNativeDriver: true }),
      Animated.timing(translateY, { toValue: 0, duration: 200, useNativeDriver: true }),
    ]).start();
  }, [opacity, translateY]);

  const tone = record.tone ?? "info";
  const accent =
    tone === "success"
      ? theme.colors.success
      : tone === "error"
      ? theme.colors.danger
      : tone === "warning"
      ? theme.colors.warning
      : theme.colors.info;

  return (
    <Animated.View
      style={[
        styles.toast,
        {
          backgroundColor: theme.colors.surface,
          borderColor: accent,
          shadowColor: theme.colors.shadow,
          opacity,
          transform: [{ translateY }],
        },
      ]}
    >
      <View style={[styles.accent, { backgroundColor: accent }]} />
      <Pressable onPress={onDismiss} style={styles.body}>
        <Text style={[styles.title, { color: theme.colors.text }]}>{record.title}</Text>
        {record.description ? (
          <Text style={[styles.description, { color: theme.colors.textMuted }]}>
            {record.description}
          </Text>
        ) : null}
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  host: {
    position: Platform.OS === "web" ? ("fixed" as any) : "absolute",
    right: 16,
    bottom: 16,
    gap: 8,
    maxWidth: 360,
    zIndex: 9999,
  },
  toast: {
    flexDirection: "row",
    borderWidth: 1,
    borderRadius: 12,
    overflow: "hidden",
    minWidth: 240,
    maxWidth: 360,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.18,
    shadowRadius: 12,
    elevation: 6,
  },
  accent: { width: 4 },
  body: { flex: 1, paddingVertical: 10, paddingHorizontal: 12 },
  title: { fontSize: 14, fontWeight: "700" },
  description: { fontSize: 12, marginTop: 2 },
});
