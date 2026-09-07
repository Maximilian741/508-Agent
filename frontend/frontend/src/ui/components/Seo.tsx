/**
 * Seo — static, per-route <head> tags for the pages Google should rank.
 *
 * Why this exists: the site-wide description used to live in app/+html.tsx and
 * the per-route title was set at RUNTIME via document.title (Screen.tsx) — so
 * every page in the static export shipped the SAME title and description.
 * Search engines index the static HTML; with identical meta on every route,
 * Google picks one page and invents snippets for the rest. expo-router's
 * <Head> renders during static export, so each public route gets its own
 * title, description and og tags — and because all of them are helmet-managed,
 * a route-level <Seo> cleanly REPLACES the default one in app/_layout.tsx
 * instead of duplicating it (which is why +html.tsx must never set these).
 *
 * JSON-LD deliberately does NOT go through <Head>: a <script> child there is
 * silently dropped from the static export (verified against a real
 * `expo export` — 0 occurrences shipped). Site-wide SoftwareApplication data
 * is a literal tag in +html.tsx; page-specific data (e.g. the FAQ's FAQPage)
 * uses useJsonLd(), which injects at runtime — Google executes JS and honors
 * runtime-injected structured data.
 *
 * Use ONLY on public marketing/tool pages. Private app state (dashboard,
 * settings, billing…) is disallowed in robots.txt and needs none of this.
 */
import Head from "expo-router/head";
import { useFocusEffect } from "expo-router";
import { useCallback } from "react";
import { Platform } from "react-native";

export interface SeoProps {
  /** Full <title>. Write it for the search result, not the tab: lead with
   *  what a stranger would type ("Free PDF accessibility checker…"). */
  title: string;
  /** 140–160 chars, plain language, states the benefit. */
  description: string;
}

export function Seo({ title, description }: SeoProps) {
  return (
    <Head>
      <title>{title}</title>
      <meta name="description" content={description} />
      <meta property="og:title" content={title} />
      <meta property="og:description" content={description} />
      <meta name="twitter:title" content={title} />
      <meta name="twitter:description" content={description} />
    </Head>
  );
}

/** Inject page-specific JSON-LD at runtime (see the module docstring for why
 *  this cannot ride in <Head>). Keyed to route FOCUS, not mount: Expo
 *  Router's stack keeps previous screens mounted behind the current one, so
 *  an unmount cleanup never fires on client navigation and the FAQ's
 *  structured data would linger on every page visited afterwards (observed
 *  in the browser, not theorized). Blur removes the tag; refocus re-adds it. */
export function useJsonLd(data: object): void {
  const serialized = JSON.stringify(data);
  useFocusEffect(
    useCallback(() => {
      if (Platform.OS !== "web" || typeof document === "undefined") return;
      const el = document.createElement("script");
      el.type = "application/ld+json";
      el.text = serialized;
      document.head.appendChild(el);
      return () => {
        try {
          document.head.removeChild(el);
        } catch {
          /* already gone */
        }
      };
    }, [serialized]),
  );
}
