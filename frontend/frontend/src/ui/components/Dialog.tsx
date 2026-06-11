/**
 * Confirmation dialog.
 *
 * Renders a centered modal with a title, message, primary action, and a
 * cancel.  Used for destructive operations (Reset all, Clear history,
 * Force-overwrite-existing-decisions).  Pressable backdrop dismisses the
 * dialog the way a user expects from native confirms.
 */

import React, { ReactNode, useEffect } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { useTheme } from "../useTheme";
import { Button } from "./Button";
import { Portal } from "./Portal";

export interface DialogProps {
  open: boolean;
  title: string;
  message?: string | ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function Dialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  onConfirm,
  onCancel,
}: DialogProps) {
  const theme = useTheme();

  // Escape key dismisses on web.
  useEffect(() => {
    if (!open) return;
    if (Platform.OS !== "web") return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      } else if (e.key === "Enter") {
        e.preventDefault();
        onConfirm();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onCancel, onConfirm]);

  if (!open) return null;

  return (
    <Portal>
    <Pressable
      onPress={onCancel}
      // @ts-ignore — ARIA dialog
      accessibilityRole={Platform.OS === "web" ? ("dialog" as any) : undefined}
      accessibilityLabel={title}
      style={[styles.backdrop, { backgroundColor: theme.colors.shadow }]}
    >
      <Pressable accessibilityRole="button" accessibilityLabel="Dialog content"
        onPress={(e) => {
          // @ts-ignore — RN-Web supports stopPropagation via nativeEvent
          if (e?.stopPropagation) e.stopPropagation();
        }}
        style={[
          styles.card,
          {
            backgroundColor: theme.colors.surface,
            borderColor: theme.colors.border,
          },
        ]}
      >
        <Text style={[theme.typography.h1, { color: theme.colors.text }]}>{title}</Text>
        {typeof message === "string" ? (
          <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8 }]}>
            {message}
          </Text>
        ) : (
          message
        )}
        <View style={styles.actions}>
          <Button title={cancelLabel} variant="ghost" onPress={onCancel} />
          <Button
            title={confirmLabel}
            variant={destructive ? "danger" : "primary"}
            onPress={onConfirm}
          />
        </View>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginTop: 12 }]}>
          Esc to cancel · Enter to confirm
        </Text>
      </Pressable>
    </Pressable>
    </Portal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    position: Platform.OS === "web" ? ("fixed" as any) : "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
    zIndex: 10001,
  },
  card: {
    borderWidth: 1,
    borderRadius: 16,
    padding: 24,
    maxWidth: 460,
    width: "100%",
    gap: 6,
  },
  actions: { flexDirection: "row", gap: 12, justifyContent: "flex-end", marginTop: 16 },
});
