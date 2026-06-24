/**
 * Canonical public pricing data + derived per-document price math.
 *
 * The numbers here MUST match the backend (app/api/credits.py `_TIER_AMOUNTS`,
 * `_SUB_PLANS`, `_PLAN_PRICE_USD`, `DOC_FORMAT_COSTS`) and the existing
 * billing screen (app/billing.tsx) and creditCosts.ts. The backend is the
 * source of truth for what is charged; this module exists so the public
 * /pricing page can show an HONEST, transparent "from $X per page" figure
 * derived from those same numbers rather than a hand-typed marketing claim.
 */

export type FormatKey = "pdf" | "docx" | "pptx" | "html";

/** Credits charged per remediation, by format. Mirrors creditCosts.ts. */
export const FORMAT_CREDITS: Record<FormatKey, number> = {
  pdf: 5,
  docx: 3,
  pptx: 4,
  html: 3,
};

export const FORMAT_LABELS: Record<FormatKey, string> = {
  pdf: "PDF",
  docx: "Word (.docx)",
  pptx: "PowerPoint (.pptx)",
  html: "HTML",
};

export interface CreditPack {
  key: "starter" | "pro" | "studio";
  name: string;
  priceCents: number;
  credits: number;
  highlight?: boolean;
  blurb: string;
}

/** One-time credit packs (credits never expire). Matches app/billing.tsx TIERS. */
export const CREDIT_PACKS: CreditPack[] = [
  { key: "starter", name: "Starter", priceCents: 500, credits: 50, blurb: "Try it on a handful of documents." },
  { key: "pro", name: "Pro", priceCents: 1500, credits: 250, highlight: true, blurb: "Best per-credit value for regular use." },
  { key: "studio", name: "Studio", priceCents: 5000, credits: 1300, blurb: "Bulk rate for big backlogs." },
];

export interface SubPlan {
  key: "team" | "business";
  name: string;
  monthlyCents: number;
  annualCents: number;
  creditsPerMonth: number;
  seats: number;
  blurb: string;
}

/** Monthly/annual subscriptions. Matches app/billing.tsx PLAN_FAMILIES. */
export const SUB_PLANS: SubPlan[] = [
  { key: "team", name: "Team", monthlyCents: 9900, annualCents: 99000, creditsPerMonth: 1000, seats: 3, blurb: "For small teams and recurring volume." },
  { key: "business", name: "Business", monthlyCents: 49900, annualCents: 499000, creditsPerMonth: 6000, seats: 10, blurb: "For agencies and high volume." },
];

/** Conformance certificate cost (PAYG); included free on subscriptions. */
export const CERT_CREDITS = 2;
/** Free credits granted to a new account (no card). Matches the audit free tier. */
export const FREE_CREDITS = 25;
/** Typical MANUAL remediation rate per page (industry), for the value comparison. */
export const MANUAL_PER_PAGE = { low: 5, high: 25 };

export function dollarsPerCredit(pack: CreditPack): number {
  return pack.priceCents / 100 / pack.credits;
}

/** Effective $ to remediate one document of `format` at a given pack's rate. */
export function pricePerDoc(format: FormatKey, pack: CreditPack): number {
  return dollarsPerCredit(pack) * FORMAT_CREDITS[format];
}

/** "$0.19"-style string. */
export function fmtUsd(n: number, opts?: { cents?: boolean }): string {
  if (opts?.cents || n < 10) return `$${n.toFixed(2)}`;
  return `$${Math.round(n).toLocaleString()}`;
}

/** Cheapest and most-expensive per-document price across packs, for "from $X". */
export function priceRange(format: FormatKey): { low: number; high: number } {
  const prices = CREDIT_PACKS.map((p) => pricePerDoc(format, p));
  return { low: Math.min(...prices), high: Math.max(...prices) };
}

/** The headline "from $X / page" using the cheapest format-agnostic rate (PDF, the priciest format, at the bulk pack). */
export function headlineFromPerPage(): number {
  // PDF is the most expensive format; its bulk (Studio) rate is the honest
  // floor we advertise as "from $X per page".
  const studio = CREDIT_PACKS.find((p) => p.key === "studio")!;
  return pricePerDoc("pdf", studio);
}
