/**
 * Web portal.
 *
 * Renders children into a dedicated node appended to <body> on web, so
 * full-screen overlays (modals, confirm dialogs) escape any ancestor that
 * establishes a containing block for `position: fixed` — a transform,
 * filter, or `will-change` anywhere up the tree (our hero shaders use them)
 * makes `fixed` resolve against THAT ancestor instead of the viewport,
 * which collapses a backdrop into a thin strip. Portaling to <body> is the
 * standard fix.
 *
 * On native this is a passthrough (RN's own Modal/absolute layout already
 * covers the screen).
 */

import React, { ReactNode, useEffect, useState } from "react";
import { Platform } from "react-native";

export function Portal({ children }: { children: ReactNode }) {
  if (Platform.OS !== "web") return <>{children}</>;
  return <WebPortal>{children}</WebPortal>;
}

function WebPortal({ children }: { children: ReactNode }) {
  const [host, setHost] = useState<HTMLElement | null>(null);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const el = document.createElement("div");
    el.setAttribute("data-portal", "508-overlay");
    document.body.appendChild(el);
    setHost(el);
    return () => {
      try {
        document.body.removeChild(el);
      } catch {
        // already detached
      }
    };
  }, []);

  if (!host) return null;
  // require() keeps react-dom out of the native bundle.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { createPortal } = require("react-dom");
  return createPortal(children, host);
}
