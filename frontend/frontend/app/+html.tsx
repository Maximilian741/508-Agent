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

const DESCRIPTION =
  "Automatically find and fix WCAG 2.1, Section 508 & PDF/UA accessibility issues in PDF, Word, PowerPoint, and HTML files — including AI-written alt text — and get a conformance report. First audits free.";
// Brand primary (light theme `accent`, burnt-orange ember).
const THEME_COLOR = "#C2410C";

export default function Root({ children }: PropsWithChildren) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
        <meta name="viewport" content="width=device-width, initial-scale=1, shrink-to-fit=no" />

        {/* title / description / og:title / og:description are NOT set here.
            They come from the <Seo> component: each public route sets its own
            (app/landing.tsx etc.) and app/_layout.tsx provides the site-wide
            default for everything else. Setting them here TOO shipped every
            page with two <meta name="description"> tags — the route one and
            this one — and whichever a crawler picked was luck. Helmet-managed
            tags dedupe against each other; a raw tag here does not. */}
        <meta name="theme-color" content={THEME_COLOR} />

        {/* Open Graph / Twitter statics that are genuinely site-wide */}
        <meta property="og:type" content="website" />
        <meta property="og:site_name" content="508 Agent" />
        <meta name="twitter:card" content="summary" />

        {/* schema.org SoftwareApplication — static so crawlers that don't run
            JS still get it. Site-wide by design (it describes the product,
            not the page); the FAQ page adds its FAQPage data at runtime. */}
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              "@context": "https://schema.org",
              "@type": "SoftwareApplication",
              name: "508 Agent",
              applicationCategory: "BusinessApplication",
              operatingSystem: "Web",
              description: DESCRIPTION,
              offers: {
                "@type": "Offer",
                price: "0",
                priceCurrency: "USD",
                description: "Free accessibility scan; pay only to download fixed files.",
              },
            }),
          }}
        />

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
