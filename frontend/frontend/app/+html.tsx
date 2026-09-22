/**
 * Web-only HTML shell for the static export (Expo Router `+html`).
 *
 * This wraps the server-rendered HTML for *every* web route, so it's the one
 * place to put crawlable, share-card metadata that doesn't depend on client
 * JavaScript running. Per-tab titles are still set client-side by `Screen`
 * (`document.title = "<page> · 508 Agent"`); the <title> here is the static
 * default a crawler or social unfurler sees before hydration.
 *
 * It also carries the site-wide base CSS (fonts, focus ring, selection,
 * reduced motion). That CSS is static and inline, so it is in the first byte
 * of every page: no flash of the wrong font or a white page before hydration.
 *
 * Note: og:url / og:image are intentionally omitted — the canonical public
 * domain isn't pinned in this repo, and unfurlers fall back to the shared link
 * for the URL. A summary card renders fine from site_name + title + description.
 */
import { ScrollViewStyleReset } from "expo-router/html";
import { type PropsWithChildren } from "react";

const DESCRIPTION =
  "Automatically find and fix WCAG 2.1, Section 508 & PDF/UA accessibility issues in PDF, Word, PowerPoint, and HTML files — including AI-written alt text — and get a conformance report. First audits free.";
// The default (Aurora) page colour — the browser chrome matches the app.
const THEME_COLOR = "#060910";

// Inter (variable, 100–900), served from our own origin: the CSP only allows
// font-src 'self', and third-party font hosts would leak every visitor's IP.
// latin-ext only downloads when a page actually uses those characters.
const LATIN =
  "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD";
const LATIN_EXT =
  "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF";

function face(family: string, file: string, range: string): string {
  return `@font-face{font-family:${family};font-style:normal;font-weight:100 900;font-display:swap;src:url(/fonts/${file}) format("woff2");unicode-range:${range}}`;
}

/*
 * Why the second family name: react-native-web gives every <Text> a base class
 * whose font is its system stack, which starts with `-apple-system`. Hundreds
 * of Text elements in the app never set a fontFamily, and a global CSS rule
 * strong enough to beat that class would also beat the explicit monospace
 * families. Declaring Inter under the `-apple-system` name makes that stack
 * resolve to Inter on Chromium/Firefox, while explicit families (mono) are
 * untouched. Safari treats -apple-system as its system-font keyword and shows
 * San Francisco — an equally clean sans, which is the intended fallback.
 */
const BASE_CSS = [
  face("Inter", "inter-latin-wght-normal.woff2", LATIN),
  face("Inter", "inter-latin-ext-wght-normal.woff2", LATIN_EXT),
  face('"-apple-system"', "inter-latin-wght-normal.woff2", LATIN),
  face('"-apple-system"', "inter-latin-ext-wght-normal.woff2", LATIN_EXT),
  "html,body{background:#060910;color:#EAF0F8}",
  "body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}",
  "::selection{background:rgba(94,234,212,0.30)}",
  "*{scrollbar-width:thin;scrollbar-color:rgba(148,163,184,0.35) transparent}",
  // Keyboard focus ring — 2px at 2px offset, colour = the live theme accent
  // (Screen keeps --ui-focus in sync). WCAG 2.4.7; never remove.
  ":focus-visible{outline:2px solid var(--ui-focus,#5EEAD4)!important;outline-offset:2px!important}",
  ":focus:not(:focus-visible){outline:none!important}",
  // Phone widths: the nav links become one horizontally scrolling row under
  // the brand + account row (see AppNav, data-nav-links).
  "@media (max-width:760px){[data-nav-links]{order:3;flex-basis:100%!important;flex-wrap:nowrap!important;overflow-x:auto;scrollbar-width:none;padding-bottom:2px}[data-nav-links]::-webkit-scrollbar{display:none}[data-nav-links]>*{flex-shrink:0}}",
  "@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:0.01ms!important;animation-iteration-count:1!important;transition-duration:0.01ms!important;scroll-behavior:auto!important}}",
].join("\n");

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
        <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
        <link
          rel="preload"
          href="/fonts/inter-latin-wght-normal.woff2"
          as="font"
          type="font/woff2"
          crossOrigin=""
        />
        <style id="base-css" dangerouslySetInnerHTML={{ __html: BASE_CSS }} />

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
