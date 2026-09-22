/**
 * DropZone — the one big "drop your file here / or choose a file" control.
 *
 * ONE control: the whole zone is a single button (Enter/Space or a click
 * opens the file picker). Dropping a file ANYWHERE on the window also works:
 * the page owns that listener (useFileDrop in OneStepFixer) and passes
 * `dragging` down, so there is exactly one drop handler per page. The hidden
 * <input> lists every type we accept so the picker filters to them.
 *
 * Its accessible name contains the visible words (WCAG 2.5.3).
 */
import React, { useRef, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { ACCEPT_ATTR, ACCEPTED_SENTENCE } from "../../domain/fixPlan";
import { alpha } from "../theme";
import { useTheme } from "../useTheme";
import { Icon } from "../components/Icon";

interface DropZoneProps {
  onFile: (file: File) => void;
  /** A file is being dragged over the window. */
  dragging?: boolean;
  /** While false the zone ignores clicks (a file is already in progress). */
  enabled?: boolean;
  maxUploadMb?: number | null;
  /** Smaller variant: "Check another file". */
  compact?: boolean;
  label?: string;
}

export function DropZone({ onFile, dragging = false, enabled = true, maxUploadMb, compact = false, label }: DropZoneProps) {
  const theme = useTheme();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [hovered, setHovered] = useState(false);

  const open = () => {
    if (!enabled) return;
    inputRef.current?.click();
  };

  const title = label ?? (compact ? "Check another file" : "Drop your file here");
  const sub = compact ? "or drop it anywhere on this page" : "or choose a file";
  const caption = `${ACCEPTED_SENTENCE}${maxUploadMb ? ` Up to ${maxUploadMb} MB.` : ""}`;
  const active = enabled && (dragging || hovered);

  return (
    <View style={compact ? { flexGrow: 1, flexShrink: 1, minWidth: 240 } : { width: "100%" }}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${title}, ${sub}`}
        accessibilityHint={caption}
        accessibilityState={{ disabled: !enabled }}
        disabled={!enabled}
        onPress={open}
        // @ts-ignore - RN-web hover events
        onHoverIn={() => setHovered(true)}
        // @ts-ignore
        onHoverOut={() => setHovered(false)}
        style={({ focused }: any) => [
          styles.zone,
          compact ? styles.zoneCompact : null,
          {
            borderRadius: compact ? theme.radius.md : theme.radius.lg,
            borderColor: active ? theme.colors.accent : alpha(theme.colors.accent, 0.55),
            backgroundColor: theme.colors.surface,
          },
          Platform.OS === "web"
            ? ({
                borderStyle: "dashed",
                transition: "border-color 160ms ease, box-shadow 200ms ease",
                boxShadow: active
                  ? `0 0 0 4px ${alpha(theme.colors.accent, 0.22)}, 0 18px 48px -18px ${alpha(theme.colors.accent, 0.5)}`
                  : "none",
                cursor: enabled ? "pointer" : "default",
              } as any)
            : null,
          focused
            ? ({
                outlineColor: theme.colors.accent,
                outlineWidth: theme.focus.outlineWidth,
                outlineStyle: "solid",
                outlineOffset: theme.focus.outlineOffset,
              } as any)
            : null,
          !enabled ? { opacity: 0.6 } : null,
        ]}
      >
        <View
          style={[
            styles.iconWrap,
            compact ? { width: 36, height: 36 } : null,
            { backgroundColor: theme.colors.accentSoft, borderRadius: theme.radius.pill },
          ]}
        >
          <Icon name={dragging ? "download" : "upload"} size={compact ? 16 : 26} color={theme.colors.accent} />
        </View>
        <View style={{ alignItems: compact ? "flex-start" : "center", gap: compact ? 0 : 4, flexShrink: 1 }}>
          <Text
            style={[
              compact ? [theme.typography.body, { fontWeight: "700" as const }] : theme.typography.h1,
              { color: theme.colors.text, textAlign: compact ? "left" : "center" },
            ]}
          >
            {dragging ? "Let go to check it" : title}
          </Text>
          <Text
            style={[
              compact ? theme.typography.caption : theme.typography.body,
              { color: compact ? theme.colors.textMuted : theme.colors.accent, fontWeight: "600", textAlign: compact ? "left" : "center" },
            ]}
          >
            {sub}
          </Text>
          {!compact ? (
            <Text style={[theme.typography.caption, { color: theme.colors.textMuted, textAlign: "center", marginTop: 4 }]}>
              {caption}
            </Text>
          ) : null}
        </View>
      </Pressable>
      {Platform.OS === "web" ? (
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT_ATTR}
          tabIndex={-1}
          aria-hidden="true"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.currentTarget.files?.[0];
            // Reset so choosing the SAME file again still fires onChange.
            e.currentTarget.value = "";
            if (f && enabled) onFile(f);
          }}
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  zone: {
    borderWidth: 2,
    minHeight: 190,
    paddingHorizontal: 20,
    paddingVertical: 28,
    alignItems: "center",
    justifyContent: "center",
    gap: 14,
  },
  zoneCompact: {
    minHeight: 0,
    paddingVertical: 10,
    paddingHorizontal: 14,
    flexDirection: "row",
    justifyContent: "flex-start",
    gap: 12,
  },
  iconWrap: {
    width: 56,
    height: 56,
    alignItems: "center",
    justifyContent: "center",
  },
});
