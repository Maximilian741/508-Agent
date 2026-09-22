#!/usr/bin/env node
/**
 * Smoke for the one-step fixer's pure logic (no browser, no network):
 * the promise ("we can fix N"), the price on the button, what counts as
 * fixed after the run, and the one sentence shown for each kind of error.
 *
 * Compiles src/domain/{fixPlan,apiErrors,creditCosts}.ts with the project's
 * own tsc into a temp dir (CommonJS) and asserts on the result.
 *
 *   node scripts/check-fix-plan.mjs     # exit 1 on any failure
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = mkdtempSync(join(tmpdir(), "fixplan-"));
const tsc = join(root, "node_modules", "typescript", "bin", "tsc");
try {
  execFileSync(
    process.execPath,
    [
      tsc,
      "--outDir", out,
      "--module", "commonjs",
      "--target", "es2020",
      "--moduleResolution", "node",
      "--esModuleInterop",
      "--skipLibCheck",
      "--jsx", "react-jsx",
      "--noEmitOnError", "false",
      join(root, "src", "domain", "fixPlan.ts"),
      join(root, "src", "domain", "apiErrors.ts"),
      join(root, "src", "domain", "creditCosts.ts"),
    ],
    { stdio: "pipe" },
  );
} catch {
  // tsc exits non-zero on type errors in files it pulls in transitively
  // (react-native types); the emit still happens (noEmitOnError=false) and
  // `npm run typecheck` is the type gate. Only a missing emit fails here.
}
writeFileSync(join(out, "package.json"), '{"type":"commonjs"}');
const require = createRequire(join(out, "x.js"));
const find = (name) => {
  for (const p of [join(out, "domain", name), join(out, "src", "domain", name), join(out, name)]) {
    try {
      return require(p);
    } catch (e) {
      if (e.code !== "MODULE_NOT_FOUND") throw e;
    }
  }
  throw new Error(`compiled module not found: ${name}`);
};
const plan = find("fixPlan.js");
const errs = find("apiErrors.js");
const costs = find("creditCosts.js");

let failures = 0;
let passed = 0;
function eq(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) passed += 1;
  else {
    failures += 1;
    console.error(`FAIL ${label}\n   got:  ${JSON.stringify(got)}\n   want: ${JSON.stringify(want)}`);
  }
}

const v = (id, ruleId, actions, extra = {}) => ({
  id,
  ruleId,
  severity: "error",
  description: `${ruleId} description.`,
  nodeId: `n-${id}`,
  page: null,
  standards: { wcag_2_1: [], section_508: [], pdf_ua: [] },
  evidence: {},
  recommendedActions: actions,
  ...extra,
});
const report = (fmt, violations, summary = {}, aiProvider = "heuristic") => ({
  summary: { documentId: "d", sourceFormat: fmt, pageCount: 1, nodeCount: 1, imageCount: 0, tableCount: 0, ...summary },
  violations,
  executions: [],
  score: { initialIssues: violations.length, fixedAutomatically: 0, pendingManual: 0, score: 0, grade: "F" },
  aiProvider,
});

// ---- the promise: backend autoFixable wins ---------------------------------
{
  const r = report(
    "pdf",
    [
      v("a", "DOCUMENT_TITLE_MISSING", ["SET_DOCUMENT_TITLE"], { autoFixable: true }),
      v("b", "MISSING_ALT_TEXT", ["GENERATE_ALT_TEXT"], { autoFixable: false }),
      v("c", "LINK_TEXT_NON_DESCRIPTIVE", ["IMPROVE_LINK_TEXT"], { autoFixable: true }),
    ],
    { total: 3, autoFixable: 2, needsYou: 1, cost: 5 },
  );
  const p = plan.buildPlan(r, { name: "x.pdf" });
  eq("contract: auto ids", p.auto.map((x) => x.id), ["a", "c"]);
  eq("contract: cost from summary", p.cost, 5);
  eq("contract: headline", plan.resultHeadline(p), {
    title: "We found 3 things to fix.",
    body: "We can fix 2 automatically. 1 needs a quick look from you.",
  });
}

// ---- the promise: fallback mirror of _PERSISTED_ACTIONS --------------------
{
  const r = report("pdf", [
    v("t", "DOCUMENT_TITLE_MISSING", ["SET_DOCUMENT_TITLE", "FLAG_FOR_MANUAL_REVIEW"]),
    v("l", "LINK_TEXT_NON_DESCRIPTIVE", ["IMPROVE_LINK_TEXT", "FLAG_FOR_MANUAL_REVIEW"]), // not persisted in PDF
    v("i", "MISSING_ALT_TEXT", ["GENERATE_ALT_TEXT"]), // heuristic draft: not promised
    v("h", "HEADING_TEXT_EMPTY", ["FLAG_FOR_MANUAL_REVIEW"]),
  ]);
  const p = plan.buildPlan(r, { name: "x.pdf" });
  eq("fallback pdf: auto", p.auto.map((x) => x.id), ["t"]);
  eq("fallback pdf: cost from table", p.cost, 5);
  const withAi = plan.buildPlan({ ...r, aiProvider: "claude" }, { name: "x.pdf" });
  eq("fallback pdf + paid AI: alt text promised", withAi.auto.map((x) => x.id), ["t", "i"]);
  const docx = plan.buildPlan(report("docx", [v("l", "LINK_TEXT_NON_DESCRIPTIVE", ["IMPROVE_LINK_TEXT"])]), { name: "x.docx" });
  eq("fallback docx: link text persists", docx.auto.length, 1);
  eq("fallback docx: cost", docx.cost, 3);
}

// ---- nothing fixable costs nothing ----------------------------------------
{
  const p = plan.buildPlan(report("docx", [v("h", "HEADING_TEXT_EMPTY", ["FLAG_FOR_MANUAL_REVIEW"])], { cost: 0 }), { name: "a.docx" });
  eq("manual only: cost 0", p.cost, 0);
  eq("manual only: headline", plan.resultHeadline(p).body, "We can't fix it automatically. It needs a quick look from you. Here's what to do.");
  eq("clean file", plan.resultHeadline(plan.buildPlan(report("pdf", []), null)).title, "Good news: we didn't find anything to fix.");
}

// ---- the button ------------------------------------------------------------
eq("button signed in", plan.fixButtonLabel(3, 25), "Fix it – uses 3 of your 25 credits");
eq("button signed out", plan.fixButtonLabel(5, null), "Fix it – uses 5 credits");

// ---- after the run: only the response says what was fixed ---------------
{
  const planned = [v("a", "DOCUMENT_TITLE_MISSING", ["SET_DOCUMENT_TITLE"]), v("b", "MISSING_ALT_TEXT", ["GENERATE_ALT_TEXT"])];
  const res = {
    persistedFixes: 1,
    executions: [{ actionCode: "GENERATE_ALT_TEXT", targetNodeId: "n-b", status: "success", notes: "" }],
    violations: [
      { ...planned[0], fixed: true },
      { ...planned[1], fixed: false },
    ],
  };
  const oc = plan.outcomes(planned, res);
  eq("contract fixed flag wins over executions", oc, { a: "fixed", b: "needs-you" });
  eq("fixed count", plan.fixedCount(5, oc, res), 1);
  const none = { ...res, persistedFixes: 0 };
  eq("nothing persisted -> nothing fixed", plan.outcomes(planned, none), { a: "needs-you", b: "needs-you" });
  eq("nothing persisted -> 0", plan.fixedCount(5, plan.outcomes(planned, none), none), 0);
  const legacy = { persistedFixes: 1, executions: [{ actionCode: "SET_DOCUMENT_TITLE", targetNodeId: "n-a", status: "success", notes: "" }] };
  const loc = plan.outcomes(planned, legacy);
  eq("legacy: executions decide", loc, { a: "fixed", b: "needs-you" });
  eq("legacy: never more than persisted", plan.fixedCount(2, { a: "fixed", b: "fixed" }, legacy), 1);
  eq("headline", plan.fixedHeadline(5, 7), "We fixed 5 of 7.");
}

// ---- files -----------------------------------------------------------------
for (const n of ["a.pdf", "b.DOCX", "c.pptx", "d.xlsx", "e.htm", "f.doc", "g.xls", "h.ppt", "i.rtf", "j.odt", "k.ods", "l.odp", "m.png", "n.JPG", "o.jpeg", "p.gif", "q.bmp", "r.tiff", "s.webp"]) {
  eq(`accepted ${n}`, plan.isAcceptedFile({ name: n }), true);
}
eq("rejected .xyz", plan.isAcceptedFile({ name: "notes.xyz" }), false);
eq("rejected no extension", plan.isAcceptedFile({ name: "README" }), false);
eq("price: .doc is a docx job", costs.costFor(costs.formatForFile({ name: "old.doc" })), 3);
eq("price: picture is a pdf job", costs.costFor(costs.formatForFile({ name: "scan.png" })), 5);
eq("price: xlsx", costs.costFor("xlsx"), 3);
eq("guide link", plan.guideHref("MISSING_ALT_TEXT"), "/fix/missing-alt-text");
eq("guide link for an unknown rule", plan.guideHref("SOMETHING_NEW"), "/fix");
eq("no rule ids in titles", /_/.test(plan.plainTitle({ ruleId: "SOMETHING_NEW", description: "A new check found a problem. More text." })), false);

// ---- one sentence per error ------------------------------------------------
const httpErr = (status, body) => Object.assign(new Error(typeof body === "string" ? body : JSON.stringify(body)), { status });
eq("coded message wins", errs.humanError(httpErr(422, { detail: "x", code: "password_protected", message: "This PDF is password-protected. You were not charged." })), "This PDF is password-protected. You were not charged.");
eq("legacy machine token", errs.humanError(httpErr(400, { detail: "empty_upload" })), "That file is empty. Choose it again, or save it again and upload the new copy.");
eq("legacy sentence prefix", errs.humanError(httpErr(422, { detail: "Failed to parse document. Ensure it is a valid, uncorrupted PDF, DOCX, PPTX, or HTML file." })), "We couldn't open this file. It may be damaged, or not really the type its name says. You were not charged.");
eq("unsupported names the ending", errs.humanError(httpErr(400, { detail: "Unsupported file type: .xyz" })), "We can't open .xyz files yet. Try a PDF, Word, PowerPoint, Excel, web page or picture file.");
eq("human 413 detail kept", errs.humanError(httpErr(413, { detail: "Without an account you can check files up to 10 MB. Create a free account to check files up to 25 MB.", code: "too_large" })), "Without an account you can check files up to 10 MB. Create a free account to check files up to 25 MB.");
eq("network", errs.humanError(new TypeError("Failed to fetch")), "We can't reach the service right now. Check your internet connection and try again.");
eq("429 with retry", errs.humanError(httpErr(429, { detail: "rate_limited", code: "rate_limited", retryAfter: 30 })), "Too many tries. Wait about 30 seconds, then try again.");
eq("5xx never leaks", errs.humanError(httpErr(500, "Traceback (most recent call last): KeyError 'x'")), "Something went wrong on our side. Please try again.");
eq("validation list never shown", errs.humanError(httpErr(422, { detail: [{ loc: ["body", "file"], msg: "field required" }] })), "We couldn't read this file. It may be damaged. You were not charged.");
eq("raw html body never shown", errs.humanError(httpErr(502, "<html><body>Bad Gateway</body></html>")), "Something went wrong on our side. Please try again.");
eq("account error with serverMessage", errs.humanError(Object.assign(new Error("invalid_credentials"), { status: 401, code: "invalid_credentials" })), "That password doesn't match this email address.");
eq("status of a thrown error", errs.errorStatus(httpErr(402, { detail: "Insufficient credits" })), 402);

rmSync(out, { recursive: true, force: true });
if (failures) {
  console.error(`[fix-plan] ${failures} failed, ${passed} passed`);
  process.exit(1);
}
console.log(`[fix-plan] ${passed} checks passed: promise, price, outcomes, accepted files, error sentences`);
