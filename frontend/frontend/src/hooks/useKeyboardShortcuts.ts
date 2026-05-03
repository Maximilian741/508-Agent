/**
 * Web-only keyboard shortcuts hook.
 *
 * Each entry binds a key to an action. The hook is a no-op on native (Expo
 * mobile) so the audit screen still renders fine on iOS/Android even though
 * shortcuts won't fire.
 *
 * It deliberately ignores keystrokes when an editable element has focus
 * (textarea, input, contenteditable) so the user can type without us
 * intercepting "a" → approve.
 */

import { useEffect } from "react";
import { Platform } from "react-native";

export interface Shortcut {
  key: string;
  /** Display label for help overlays / hint pills. */
  label: string;
  /** Description for the help panel. */
  description: string;
  /** Whether the key is in a modifier-required combo (default: false). */
  ctrlOrCmd?: boolean;
  shift?: boolean;
  /** Run when matched. */
  run: () => void;
  /** When true, don't fire if a text input has focus. Defaults to true. */
  guardEditable?: boolean;
}

export function useKeyboardShortcuts(shortcuts: Shortcut[], deps: any[] = []) {
  useEffect(() => {
    if (Platform.OS !== "web") return;
    const listener = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const guardEditable = (s: Shortcut) => s.guardEditable !== false;
      const isEditable =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          (target as HTMLElement).isContentEditable);

      for (const shortcut of shortcuts) {
        if (event.key.toLowerCase() !== shortcut.key.toLowerCase()) continue;
        if (shortcut.ctrlOrCmd && !(event.metaKey || event.ctrlKey)) continue;
        if (!shortcut.ctrlOrCmd && (event.metaKey || event.ctrlKey)) continue;
        if (shortcut.shift && !event.shiftKey) continue;
        if (!shortcut.shift && event.shiftKey && !shortcut.ctrlOrCmd) continue;
        if (isEditable && guardEditable(shortcut)) continue;
        event.preventDefault();
        shortcut.run();
        return;
      }
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
