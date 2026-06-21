/**
 * SkipToContent — WCAG 2.4.1 "Bypass Blocks" landmark skip link.
 *
 * The first focusable element on every page. Visually hidden until it
 * receives keyboard focus, at which point it slides into view. Activating
 * it (Enter / Space) jumps focus to the element with id="content" so
 * keyboard and screen-reader users can bypass the persistent AppNav and
 * land directly on the page's main landmark.
 *
 * Web-only — native platforms have no analogous landmark navigation.
 */

import React from "react";
import { Platform } from "react-native";

import { useTheme } from "../useTheme";

export function SkipToContent() {
  const theme = useTheme();
  if (Platform.OS !== "web") return null;
  return (
    // @ts-ignore — DOM <a> renders fine on the web target
    <a
      href="#content"
      style={{
        position: "absolute",
        top: 8,
        left: 8,
        padding: "10px 16px",
        background: theme.colors.text,
        color: theme.colors.bg,
        fontWeight: 700,
        fontSize: 14,
        borderRadius: theme.radius.md,
        textDecoration: "none",
        // Hidden until keyboard-focused. Stays in tab order.
        transform: "translateY(-200%)",
        transition: "transform 120ms ease-out",
        zIndex: 10000,
        outlineColor: theme.colors.accent,
        outlineWidth: 2,
        outlineStyle: "solid",
        outlineOffset: 2,
      }}
      onFocus={(e: any) => {
        e.currentTarget.style.transform = "translateY(0)";
      }}
      onBlur={(e: any) => {
        e.currentTarget.style.transform = "translateY(-200%)";
      }}
      onClick={() => {
        // Move keyboard focus into the main landmark, not just scroll.
        const target =
          typeof document !== "undefined" ? document.getElementById("content") : null;
        if (target) {
          // Make it programmatically focusable for screen readers.
          if (!target.hasAttribute("tabindex")) {
            target.setAttribute("tabindex", "-1");
          }
          target.focus();
        }
      }}
    >
      Skip to content
    </a>
  );
}
