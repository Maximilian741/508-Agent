/**
 * linkProps — make a Pressable a real, crawlable `<a href>` on web.
 *
 * Every in-app navigation used to be `onPress={() => router.push(...)}` on a
 * Pressable, which react-native-web renders as `<div role="button">`. Search
 * engines only follow `<a href>`, so the exported HTML had zero internal
 * links: the fix guides, tools and pricing pages were reachable by Google
 * through the sitemap alone, with no link equity flowing between them. It was
 * also wrong for people: a button can't be middle-clicked into a new tab, and
 * a screen reader announced destinations as actions.
 *
 * Spread the result into a Pressable (react-native-web renders any View with
 * `href` as an anchor). A plain left-click or Enter is intercepted and routed
 * client-side, so navigation stays instant; a modified click (ctrl/cmd/shift,
 * middle button) is left to the browser so "open in new tab" works.
 *
 * Keyboard: RN-web's press responder skips its own Enter handling for anchors
 * and lets the browser's native click fire, so there is exactly one
 * navigation per keypress — no double push.
 */
import { router } from "expo-router";
import { GestureResponderEvent, Platform } from "react-native";

export interface LinkPressProps {
  href?: string;
  accessibilityRole: "link";
  onPress: (e: GestureResponderEvent) => void;
}

/** True when the browser should handle this click itself (new tab/window). */
function browserShouldHandle(e: any): boolean {
  if (!e || e.defaultPrevented) return true;
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return true;
  if (e.button != null && e.button !== 0) return true;
  const target = e.currentTarget?.getAttribute?.("target");
  return Boolean(target && target !== "_self");
}

export function linkProps(href: string, onNavigate?: () => void): LinkPressProps {
  return {
    ...(Platform.OS === "web" ? { href } : null),
    accessibilityRole: "link",
    onPress: (e) => {
      if (Platform.OS === "web") {
        if (browserShouldHandle(e)) return;
        (e as any).preventDefault?.();
      }
      onNavigate?.();
      router.push(href as any);
    },
  };
}
