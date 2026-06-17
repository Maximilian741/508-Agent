/**
 * White-label report branding.
 *
 * Agencies and consultants resell the conformance/remediation reports to their
 * own clients — so they want THEIR logo, name, and colour on the deliverable,
 * not ours. This stores a small branding profile (browser-local) and produces
 * safe HTML snippets the report generators inject. A premium, agency-facing
 * selling point ("hand your client a report under your own brand").
 *
 * Honest: branding only changes presentation. The methodology/assessment text
 * still says the analysis was automated by 508 Agent, and the footer always
 * keeps a small "Automated testing by 508 Agent" attribution.
 */

import { Platform } from "react-native";

export interface ReportBranding {
  orgName: string;
  logoDataUrl: string; // data:image/... or https:// URL
  accent: string; // hex like #2D5BFF
  contact: string; // optional contact line (email / phone / url)
}

export const DEFAULT_BRANDING: ReportBranding = {
  orgName: "",
  logoDataUrl: "",
  accent: "",
  contact: "",
};

const KEY = "508-report-branding-v1";

function _isWeb(): boolean {
  return Platform.OS === "web" && typeof window !== "undefined";
}

export function loadBranding(): ReportBranding {
  if (!_isWeb()) return { ...DEFAULT_BRANDING };
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULT_BRANDING };
    const p = JSON.parse(raw);
    return {
      orgName: typeof p.orgName === "string" ? p.orgName : "",
      logoDataUrl: typeof p.logoDataUrl === "string" ? p.logoDataUrl : "",
      accent: typeof p.accent === "string" ? p.accent : "",
      contact: typeof p.contact === "string" ? p.contact : "",
    };
  } catch {
    return { ...DEFAULT_BRANDING };
  }
}

export function saveBranding(b: ReportBranding): void {
  if (!_isWeb()) return;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(b));
  } catch {
    // ignore quota
  }
}

export function hasBranding(b: ReportBranding): boolean {
  return !!(b.orgName.trim() || b.logoDataUrl.trim() || _validAccent(b.accent));
}

function _escAttr(s: string): string {
  return (s || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function _escText(s: string): string {
  return (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** Only accept a safe logo source — a data:image URL or an https URL. */
function _safeLogo(url: string): string | null {
  const u = (url || "").trim();
  if (/^data:image\/(png|jpeg|jpg|gif|webp|svg\+xml);/i.test(u)) return u;
  if (/^https:\/\/[^\s"'<>]+$/i.test(u)) return u;
  return null;
}

function _validAccent(a: string): boolean {
  return /^#[0-9a-fA-F]{6}$/.test((a || "").trim());
}

/** A `<style>` override for the report's --accent, or "" if no custom colour. */
export function brandingAccentCss(b: ReportBranding): string {
  if (!_validAccent(b.accent)) return "";
  return `<style>:root{--accent:${b.accent.trim()} !important;}</style>`;
}

/** A brand bar (logo + org name) for the top of the report hero, or "". */
export function brandingHeroHtml(b: ReportBranding): string {
  const logo = _safeLogo(b.logoDataUrl);
  const name = b.orgName.trim();
  if (!logo && !name) return "";
  const img = logo
    ? `<img src="${_escAttr(logo)}" alt="${_escAttr(name || "Organization logo")}" style="max-height:44px;max-width:220px;object-fit:contain;display:block;" />`
    : "";
  const label = name
    ? `<div style="font-weight:800;font-size:16px;letter-spacing:-0.2px;">${_escText(name)}</div>`
    : "";
  // Neutral border + inherited text colour so the bar reads on both the dark
  // hero (audit reports) and the white batch report.
  return `<div style="display:flex;align-items:center;gap:14px;margin-bottom:18px;padding-bottom:14px;border-bottom:1px solid rgba(128,128,128,0.35);">${img}${label}</div>`;
}

/** Footer text — "Prepared by {org}" + a permanent small 508 Agent attribution. */
export function brandingFooterHtml(b: ReportBranding): string {
  const name = b.orgName.trim();
  const contact = b.contact.trim();
  const prepared = name ? `Prepared by ${_escText(name)}${contact ? ` · ${_escText(contact)}` : ""}. ` : "";
  return `${prepared}Automated testing by 508 Agent.`;
}
