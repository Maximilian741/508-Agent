/**
 * Web-only HTML shell for the static export (Expo Router `+html`).
 *
 * This wraps the server-rendered HTML for *every* web route, so it's the one
 * place to put crawlable, share-card metadata that doesn't depend on client
 * JavaScript running. Per-tab titles are still set client-side by `Screen`
 * (`document.title = "<page> · 508 Agent"`); the <title> here is the static
 * default a crawler or social unfurler sees before hydration.
 *
 * Note: og:url / og:image are intentionally omitted — the canonical public
 * domain isn't pinned in this repo, and unfurlers fall back to the shared link
 * for the URL. A summary card renders fine from site_name + title + description.
 */
import { ScrollViewStyleReset } from "expo-router/html";
import { type PropsWithChildren } from "react";

const TITLE = "508 Agent — Automated document accessibility & remediation";
const DESCRIPTION =
  "Automatically find and fix WCAG 2.1, Section 508 & PDF/UA accessibility issues in PDF, Word and PowerPoint files — including AI-written alt text — and get a conformance report. First audits free.";
// Brand primary (light theme `accent`, burnt-orange ember).
const THEME_COLOR = "#C2410C";

export default function Root({ children }: PropsWithChildren) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
        <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no" />

        <title>{TITLE}</title>
        <meta name="description" content={DESCRIPTION} />
        <meta name="theme-color" content={THEME_COLOR} />

        {/* Open Graph — Slack / LinkedIn / Facebook share cards */}
        <meta property="og:type" content="website" />
        <meta property="og:site_name" content="508 Agent" />
        <meta property="og:title" content={TITLE} />
        <meta property="og:description" content={DESCRIPTION} />

        {/* Twitter / X */}
        <meta name="twitter:card" content="summary" />
        <meta name="twitter:title" content={TITLE} />
        <meta name="twitter:description" content={DESCRIPTION} />

        {/*
          Disable body scrolling on web so ScrollView components work as
          expected. Without this, each ScrollView renders a native scrollbar.
        */}
        <ScrollViewStyleReset />
      </head>
      <body>{children}</body>
    </html>
  );
}
