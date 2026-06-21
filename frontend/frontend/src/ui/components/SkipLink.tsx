/**
 * SkipLink — WCAG 2.4.1 "Bypass Blocks".
 *
 * Visually hidden anchor that becomes visible on keyboard focus, allowing
 * screen-reader and keyboard-only users to jump past the persistent AppNav
 * straight to the screen's <main> landmark.
 *
 * Web-only: native platforms have no equivalent landmark navigation, and the
 * underlying behavior (focus a `#main` anchor on click) is a DOM concept.
 * Renders nothing on iOS/Android.
 */

import React from "react";
import { Platform } from "react-native";

export function SkipLink() {
  if (Platform.OS !== "web") return null;
  // @ts-ignore — DOM <a> is fine in a web-only branch
  return (
    // @ts-ignore — RN-Web allows raw DOM nodes via React
    <a
      href="#main"
      style={{
        position: "absolute",
        top: 8,
        left: 8,
        padding: "8px 14px",
        background: "#0F172A",
        color: "#FFFFFF",
        fontWeight: 700,
        fontSize: 14,
        borderRadius: 4,
        textDecoration: "none",
        // Hidden until keyboard-focused.
        transform: "translateY(-200%)",
        transition: "transform 120ms ease-out",
        zIndex: 10000,
      }}
      onFocus={(e: any) => {
        e.currentTarget.style.transform = "translateY(0)";
      }}
      onBlur={(e: any) => {
        e.currentTarget.style.transform = "translateY(-200%)";
      }}
    >
      Skip to main content
    </a>
  );
}
