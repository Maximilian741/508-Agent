/**
 * OneStepFixer — drop ANY document, see what's wrong, press one button, get
 * the accessible file back.
 *
 *   drop ──► check (free; anonymous when the backend allows, else an inline
 *            sign-up first) ──► "We found 7 things. We can fix 5
 *            automatically. 2 need a quick look from you."
 *        ──► ONE button carrying the price ("Fix it – uses 3 of your 25
 *            credits"); pressing it is the go-ahead
 *        ──► (in place, only if needed) sign up · confirm email · buy credits,
 *            each of which carries on by itself when it's done
 *        ──► live panel: each planned fix, where it is, "Working…" until the
 *            response arrives, then Fixed / Needs you
 *        ──► "Done – your accessible file is downloading" + every item that
 *            needs a person, SHOWN where it is in their file.
 *
 * HONESTY: the promise comes from src/domain/fixPlan (the backend's
 * autoFixable when present). Only the approved auto-fixable findings are
 * sent; nothing is rejected on the person's behalf. What we report after the
 * run comes from the response alone (`fixed`, `persistedFixes`, `charged`):
 * no fake progress, no row marked fixed before the server says so, and the
 * original bytes are never downloaded as if they were a fix.
 */
import React, { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, useWindowDimensions, View } from "react-native";

import {
  createApiClient,
  PipelineRemediateResult,
  PipelineResponse,
  PipelineViolation,
} from "../../api/client";
import {
  Account,
  grantStarterStatus,
  loadAccount,
  loadToken,
  onAccountChanged,
  refreshAccount,
  SignInResult,
  signOut,
} from "../../domain/account";
import { errorStatus, humanError, readApiError } from "../../domain/apiErrors";
import {
  absoluteUrl,
  buildPlan,
  fileExtension,
  fileSizeLabel,
  fixButtonLabel,
  fixedCount,
  fixedHeadline,
  guideHref,
  isAcceptedFile,
  outcomes,
  plainTitle,
  resultHeadline,
  RowOutcome,
  whatToDo,
  whereLabel,
} from "../../domain/fixPlan";
import { clearPendingFile, loadPendingFile, savePendingFile } from "../../domain/pendingFile";
import { useFileDrop } from "../../hooks/useFileDrop";
import { useAppStore } from "../../store/useAppStore";
import { Button } from "../components/Button";
import { Card } from "../components/Card";
import { Icon } from "../components/Icon";
import { InlineNotice } from "../components/InlineNotice";
import { Spinner } from "../components/Spinner";
import { linkProps } from "../components/linkProps";
import { useTheme } from "../useTheme";
import { BuyCreditsPanel, InlineSignUp, VerifyEmailPanel } from "./AccountGates";
import { DropZone } from "./DropZone";
import { LocationPreview } from "./LocationPreview";

type Stage = "idle" | "checking" | "account-to-check" | "result" | "fixing" | "done" | "error";
type Gate = null | "sign-up" | "verify" | "buy" | "waiting-payment";

export interface HeroSlot {
  /** 0..1: how lively the page's shader should be right now. */
  intensity: number;
  /** What goes inside the hero: the drop zone, or the live status line. */
  children: ReactNode;
}

export interface OneStepFixerProps {
  /** The page renders the hero (the shader lives on the page, not here). */
  renderHero: (slot: HeroSlot) => ReactNode;
  /** Returning from checkout / a reload mid-flow: pick the saved file back up. */
  resume?: { paid: boolean } | null;
  /** Called once the resume request has been read (the page clears the URL). */
  onResumeRead?: () => void;
  /** Tells the page whether a file is in progress (it hides its extras). */
  onBusyChange?: (active: boolean) => void;
}

const MB = 1024 * 1024;

export function OneStepFixer({ renderHero, resume, onResumeRead, onBusyChange }: OneStepFixerProps) {
  const theme = useTheme();
  const { width: winWidth } = useWindowDimensions();
  const apiBaseUrl = useAppStore((s) => s.apiBaseUrl);
  const maxUploadMb = useAppStore((s) => s.maxUploadMb);
  const client = useMemo(() => createApiClient({ baseUrl: apiBaseUrl, mockMode: false }), [apiBaseUrl]);

  const [stage, setStage] = useState<Stage>("idle");
  const [gate, setGate] = useState<Gate>(null);
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<PipelineResponse | null>(null);
  const [account, setAccount] = useState<Account | null>(() => loadAccount());
  const [fixResult, setFixResult] = useState<PipelineRemediateResult | null>(null);
  const [rowOutcome, setRowOutcome] = useState<Record<string, RowOutcome>>({});
  const [error, setError] = useState<{ message: string; retry: "check" | "fix" | null } | null>(null);
  const [status, setStatus] = useState("");
  const [signUpNotice, setSignUpNotice] = useState<string | null>(null);
  const [verificationSent, setVerificationSent] = useState(false);
  const [showAllManual, setShowAllManual] = useState(false);

  // Every async step checks it still belongs to the current file: starting
  // over (or dropping a new file) bumps runId and orphans the old work.
  const runId = useRef(0);
  const fixing = useRef(false);
  const waitForPayment = useRef(false);
  const headingRef = useRef<any>(null);
  const doneRef = useRef<any>(null);

  const plan = useMemo(() => (report ? buildPlan(report, file) : null), [report, file]);
  const busy = stage === "checking" || stage === "fixing";

  useEffect(() => onAccountChanged(() => setAccount(loadAccount())), []);
  useEffect(() => {
    onBusyChange?.(stage !== "idle");
  }, [stage, onBusyChange]);

  // Column widths for previews (useWindowDimensions can report 0 in some
  // embedded views; fall back to a phone-safe width).
  const contentWidth = Math.min(winWidth > 0 ? winWidth : 400, 1180) - 40 - 44;
  const previewWidth = Math.max(200, Math.min(360, contentWidth - 8));
  const compactWidth = 112;

  const focusSoon = (ref: React.MutableRefObject<any>) => {
    if (Platform.OS !== "web") return;
    setTimeout(() => {
      try {
        ref.current?.focus?.();
      } catch {
        /* ignore */
      }
    }, 80);
  };

  const fail = (message: string, retry: "check" | "fix" | null) => {
    setStage("error");
    setError({ message, retry });
    setStatus(message);
  };

  function reset() {
    runId.current += 1;
    fixing.current = false;
    waitForPayment.current = false;
    setStage("idle");
    setGate(null);
    setFile(null);
    setReport(null);
    setFixResult(null);
    setRowOutcome({});
    setError(null);
    setStatus("");
    setSignUpNotice(null);
    setShowAllManual(false);
  }

  // ---- check ---------------------------------------------------------------

  async function runCheck(f: File, opts: { retried?: boolean; thenFix?: boolean } = {}): Promise<void> {
    const id = ++runId.current;
    setFile(f);
    setStage("checking");
    setGate(null);
    setError(null);
    setReport(null);
    setStatus(`Checking ${f.name}…`);
    try {
      const r = await client.runPipeline(f, false);
      if (id !== runId.current) return;
      setReport(r);
      setStage("result");
      const p = buildPlan(r, f);
      const head = resultHeadline(p);
      setStatus(`${head.title} ${head.body}`);
      focusSoon(headingRef);
      // The button's label quotes the balance: make it the current one.
      if (loadToken()) {
        void refreshAccount().then((a) => {
          if (a && id === runId.current) setAccount(a);
        });
      }
      if (opts.thenFix && p.auto.length > 0) void latest.current.startFix(f, r);
    } catch (e) {
      if (id !== runId.current) return;
      if (errorStatus(e) === 401) {
        // A stale sign-in: forget it and try once more without it (the
        // backend may check files without an account).
        if (loadToken() && !opts.retried) {
          signOut();
          setAccount(null);
          return runCheck(f, { ...opts, retried: true });
        }
        setStage("account-to-check");
        setStatus("Create a free account to check this file.");
        return;
      }
      fail(humanError(e), "check");
    }
  }

  function chooseFile(f: File) {
    if (busy) {
      setStatus(`Please wait until ${file?.name ?? "this file"} is finished.`);
      return;
    }
    reset();
    setFile(f);
    const ext = fileExtension(f.name);
    if (!isAcceptedFile(f)) {
      fail(
        ext
          ? `We can't open .${ext} files. Try a PDF, Word, PowerPoint, Excel, web page or picture file.`
          : "That file has no ending (like .pdf), so we can't tell what kind of file it is. Rename it and try again.",
        null,
      );
      return;
    }
    if (f.size === 0) {
      fail("That file is empty. Choose it again, or save it again and try the new copy.", null);
      return;
    }
    if (maxUploadMb && f.size > maxUploadMb * MB) {
      fail(
        `That file is ${fileSizeLabel(f.size)}, and the limit is ${maxUploadMb} MB. Try compressing it, or split it into smaller parts.`,
        null,
      );
      return;
    }
    void runCheck(f);
  }

  // ---- fix -----------------------------------------------------------------

  /** Not enough credits: say why, in place (confirm email, or buy). */
  async function chooseGate(f: File, cost: number): Promise<Gate> {
    const g = await grantStarterStatus();
    const acct = loadAccount();
    setAccount(acct);
    if (acct && acct.credits >= cost) return null; // the starter grant just covered it
    if (g === "verify") {
      void savePendingFile(f, "verify");
      setGate("verify");
      setStatus(`Check your inbox to unlock your free credits. We sent a link to ${acct?.email ?? "your email"}.`);
      return "verify";
    }
    setGate("buy");
    setStatus(`You need ${cost} credits to fix this file. You have ${acct?.credits ?? 0}.`);
    return "buy";
  }

  /** The one button. Pressing it is the go-ahead for an ordinary cost. */
  async function startFix(f: File | null, r: PipelineResponse | null): Promise<void> {
    if (!f || !r || fixing.current) return;
    const p = buildPlan(r, f);
    if (p.auto.length === 0) return;
    if (!loadToken()) {
      setGate("sign-up");
      setStatus("Create a free account to fix this file.");
      return;
    }
    const acct = (await refreshAccount()) ?? loadAccount();
    setAccount(acct);
    if (!acct) {
      setGate("sign-up");
      setStatus("Create a free account to fix this file.");
      return;
    }
    if (acct.credits < p.cost) {
      if (waitForPayment.current) {
        setGate("waiting-payment");
        setStatus("Waiting for your payment to arrive…");
        return;
      }
      const g = await chooseGate(f, p.cost);
      if (g !== null) return;
    }
    await runFix(f, r);
  }

  async function runFix(f: File, r: PipelineResponse): Promise<void> {
    if (fixing.current) return;
    const p = buildPlan(r, f);
    if (p.auto.length === 0) return;
    fixing.current = true;
    const id = runId.current;
    setGate(null);
    setStage("fixing");
    setStatus(`Fixing ${p.auto.length} ${p.auto.length === 1 ? "thing" : "things"} in ${f.name}…`);
    try {
      const res = await client.runPipelineRemediate(
        f,
        p.auto.map((v) => v.id),
        [], // never reject on the person's behalf; manual items are simply not sent
        loadToken() ?? undefined,
      );
      if (id !== runId.current) return;
      const oc = outcomes(p.auto, res);
      const fixedN = fixedCount(p.total, oc, res);
      const url = res.downloadUrl ? absoluteUrl(res.downloadUrl, apiBaseUrl) : "";
      setFixResult({ ...res, downloadUrl: url });
      setRowOutcome(oc);
      setStage("done");
      // Only a file with a real fix in it is handed over as "your accessible
      // file". With nothing persisted the server returns the original bytes;
      // downloading those would look like a fix.
      const persisted = typeof res.persistedFixes === "number" ? res.persistedFixes : fixedN;
      if (persisted > 0 && url) triggerDownload(url, res.filename);
      setStatus(
        persisted > 0
          ? `Done. ${fixedHeadline(fixedN, p.total)} Your accessible file is downloading.`
          : "We couldn't safely fix anything in this file. We left it as it was and didn't charge you.",
      );
      focusSoon(doneRef);
      void clearPendingFile();
      void refreshAccount().then((a) => {
        if (a) setAccount(a);
      });
    } catch (e) {
      if (id !== runId.current) return;
      const st = errorStatus(e);
      if (st === 402) {
        setStage("result");
        fixing.current = false;
        await chooseGate(f, p.cost);
        return;
      }
      if (st === 401) {
        signOut();
        setAccount(null);
        setStage("result");
        setGate("sign-up");
        setSignUpNotice("Your sign-in ran out. Please sign in again to fix this file.");
        setStatus("Please sign in again to fix this file.");
        return;
      }
      const info = readApiError(e);
      fail(
        info.network
          ? `We lost the connection while fixing ${f.name}. If the fix finished, it's in your recent files, and you won't be charged twice for it.`
          : humanError(e),
        info.network ? null : "fix",
      );
    } finally {
      fixing.current = false;
    }
  }

  // ---- account gates --------------------------------------------------------

  function afterSignUp(res: SignInResult) {
    setAccount(res.user);
    setVerificationSent(res.verificationSent);
    setSignUpNotice(null);
    if (!file) return;
    if (stage === "account-to-check") {
      // They asked for the check; whether to fix is still their choice.
      void runCheck(file);
      return;
    }
    // They pressed "Fix it" before signing up: carry on.
    void startFix(file, report);
  }

  // Async continuations (a poll, a resumed check) call the LATEST closures.
  const latest = useRef({ startFix, runFix, runCheck, chooseFile });
  latest.current = { startFix, runFix, runCheck, chooseFile };

  // One window-wide drop handler for the page (it also stops a file dropped
  // mid-fix from making the browser navigate away to it).
  const onDrop = useCallback((f: File) => latest.current.chooseFile(f), []);
  const { isDragging: dragging } = useFileDrop(onDrop);

  // Paid on the checkout page: credits land by webhook, usually within
  // seconds. Poll, then carry on with the fix by ourselves.
  useEffect(() => {
    if (gate !== "waiting-payment" || !file || !report) return;
    const cost = buildPlan(report, file).cost;
    let tries = 0;
    const timer = setInterval(async () => {
      tries += 1;
      const acct = await refreshAccount();
      if (acct) setAccount(acct);
      if (acct && acct.credits >= cost) {
        clearInterval(timer);
        waitForPayment.current = false;
        void latest.current.runFix(file, report);
      } else if (tries >= 30) {
        clearInterval(timer);
        waitForPayment.current = false;
        setGate("buy");
        setStatus("We haven't seen your payment yet. If you paid, it can take a minute: refresh this page to check again.");
      }
    }, 4000);
    return () => clearInterval(timer);
  }, [gate, file, report]);

  // ---- resume (back from checkout, or a reload while confirming email) -----

  useEffect(() => {
    if (!resume) return;
    let alive = true;
    onResumeRead?.();
    void loadPendingFile().then((pending) => {
      if (!alive || !pending) return;
      waitForPayment.current = resume.paid;
      void latest.current.runCheck(pending.file, { thenFix: true });
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resume]);

  // ---- render --------------------------------------------------------------

  const intensity = stage === "fixing" ? 1 : stage === "checking" ? 0.6 : dragging ? 0.55 : stage === "done" ? 0.3 : stage === "idle" ? 0.12 : 0.22;

  const heroChildren =
    stage === "idle" ? (
      <DropZone
        onFile={chooseFile}
        dragging={dragging}
        maxUploadMb={maxUploadMb}
      />
    ) : (
      <StatusLine file={file} stage={stage} onStartOver={busy ? undefined : reset} />
    );

  return (
    <View style={{ gap: theme.spacing.xl }}>
      {renderHero({ intensity, children: heroChildren })}
      {/* The live region: every state change is announced once, politely. */}
      <Text
        accessibilityLiveRegion="polite"
        {...({ role: "status", "aria-live": "polite" } as any)}
        style={styles.srOnly}
      >
        {status}
      </Text>

      {stage === "checking" ? (
        <Card>
          <View style={styles.rowCenter}>
            <Spinner size={20} />
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]} numberOfLines={2}>
                Checking {file?.name}…
              </Text>
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>This usually takes a few seconds. Checking is free.</Text>
            </View>
          </View>
        </Card>
      ) : null}

      {stage === "account-to-check" && file ? (
        <InlineSignUp purpose="check" onDone={afterSignUp} />
      ) : null}

      {stage === "error" && error ? (
        <Card>
          <View style={{ gap: 12 }}>
            <InlineNotice tone="danger" title="That didn't work" message={error.message} />
            <View style={styles.buttonRow}>
              {error.retry && file ? (
                <Button
                  title="Try again"
                  onPress={() => (error.retry === "fix" && report ? void startFix(file, report) : void runCheck(file))}
                />
              ) : null}
              <DropZone onFile={chooseFile} dragging={dragging} compact label="Choose a different file" />
            </View>
          </View>
        </Card>
      ) : null}

      {(stage === "result" || stage === "fixing" || stage === "done") && report && plan && file ? (
        <Card featured>
          <View style={{ gap: 16 }}>
            {stage === "done" && fixResult ? (
              <DoneSummary
                refEl={doneRef}
                result={fixResult}
                total={plan.total}
                fixedN={fixedCount(plan.total, rowOutcome, fixResult)}
                cost={plan.cost}
                onFixAnother={reset}
              />
            ) : (
              <ResultSummary
                refEl={headingRef}
                plan={plan}
                report={report}
              />
            )}

            {stage === "result" && plan.auto.length > 0 ? (
              <FixAction
                plan={plan}
                account={account}
                gate={gate}
                file={file}
                signUpNotice={signUpNotice}
                verificationSent={verificationSent}
                onFix={() => void startFix(file, report)}
                onSignedUp={afterSignUp}
                onVerified={() => void startFix(file, report)}
                onBought={() => void startFix(file, report)}
                beforeRedirect={async () => {
                  await savePendingFile(file, "buy");
                }}
              />
            ) : null}

            {plan.auto.length > 0 ? (
              <PlannedFixes
                items={plan.auto}
                stage={stage}
                outcome={rowOutcome}
                file={file}
                report={report}
                width={compactWidth}
              />
            ) : null}
          </View>
        </Card>
      ) : null}

      {(stage === "result" || stage === "fixing" || stage === "done") && report && plan && file ? (
        <NeedsYou
          items={needsYouItems(plan.manual, plan.auto, stage === "done" ? rowOutcome : null)}
          triedIds={stage === "done" ? new Set(plan.auto.filter((v) => rowOutcome[v.id] === "needs-you").map((v) => v.id)) : new Set()}
          file={file}
          report={report}
          width={previewWidth}
          showAll={showAllManual}
          onShowAll={() => setShowAllManual(true)}
        />
      ) : null}

      {(stage === "result" || stage === "done") && file ? (
        <DropZone onFile={chooseFile} dragging={dragging} compact />
      ) : null}
    </View>
  );
}

function needsYouItems(
  manual: PipelineViolation[],
  auto: PipelineViolation[],
  oc: Record<string, RowOutcome> | null,
): PipelineViolation[] {
  if (!oc) return manual;
  return [...auto.filter((v) => oc[v.id] === "needs-you"), ...manual];
}

/** Same-tab anchor (a window.open after an await is popup-blocked). The
 *  server answers with Content-Disposition: attachment, so the page stays. */
function triggerDownload(url: string, filename?: string) {
  if (Platform.OS !== "web" || typeof document === "undefined") return;
  const a = document.createElement("a");
  a.href = url;
  if (filename) a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// ---------------------------------------------------------------------------
// Pieces
// ---------------------------------------------------------------------------

function StatusLine({
  file,
  stage,
  onStartOver,
}: {
  file: File | null;
  stage: Stage;
  onStartOver?: () => void;
}) {
  const theme = useTheme();
  const working = stage === "checking" || stage === "fixing";
  const line =
    stage === "checking"
      ? "Checking your file…"
      : stage === "fixing"
        ? "Fixing your file…"
        : stage === "done"
          ? "Finished."
          : stage === "account-to-check"
            ? "One quick step before we check it."
            : stage === "error"
              ? "That didn't work."
              : "Checked.";
  return (
    <View style={[styles.statusLine, { backgroundColor: theme.colors.surface, borderColor: theme.colors.glassBorder, borderRadius: theme.radius.lg }]}>
      <View style={[styles.fileIcon, { backgroundColor: theme.colors.accentSoft, borderRadius: theme.radius.md }]}>
        {working ? <Spinner size={20} /> : <Icon name="file-text" size={20} color={theme.colors.accent} />}
      </View>
      <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
        <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "700" }]} numberOfLines={1}>
          {file?.name ?? "Your file"}
        </Text>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]} numberOfLines={2}>
          {file ? `${fileSizeLabel(file.size)} · ` : ""}
          {line}
        </Text>
      </View>
      {onStartOver ? <Button title="Start over" variant="ghost" onPress={onStartOver} accessibilityHint="Clears this file so you can choose another." /> : null}
    </View>
  );
}

function ResultSummary({
  refEl,
  plan,
  report,
}: {
  refEl: React.MutableRefObject<any>;
  plan: ReturnType<typeof buildPlan>;
  report: PipelineResponse;
}) {
  const theme = useTheme();
  const head = resultHeadline(plan);
  return (
    <View style={{ gap: 8 }}>
      <Text
        ref={refEl}
        {...({ tabIndex: -1 } as any)}
        accessibilityRole="header"
        {...({ "aria-level": 2 } as any)}
        style={[theme.typography.h1, { color: theme.colors.text }]}
      >
        {head.title}
      </Text>
      <Text style={[theme.typography.body, { color: theme.colors.text, fontSize: 17, lineHeight: 26 }]}>{head.body}</Text>
      {report.intake?.note ? (
        <InlineNotice tone="info" title="We changed the file type to check it" message={report.intake.note} />
      ) : null}
      {report.summary?.pagesAnalyzed ? (
        <InlineNotice
          tone="warning"
          title="We only checked part of this file"
          message={`It's long, so we checked the first ${report.summary.pagesAnalyzed} pages. Split it into smaller files to check the rest.`}
        />
      ) : null}
    </View>
  );
}

function FixAction({
  plan,
  account,
  gate,
  file,
  signUpNotice,
  verificationSent,
  onFix,
  onSignedUp,
  onVerified,
  onBought,
  beforeRedirect,
}: {
  plan: ReturnType<typeof buildPlan>;
  account: Account | null;
  gate: Gate;
  file: File;
  signUpNotice: string | null;
  verificationSent: boolean;
  onFix: () => void;
  onSignedUp: (r: SignInResult) => void;
  onVerified: () => void;
  onBought: () => void;
  beforeRedirect: () => Promise<void>;
}) {
  const theme = useTheme();
  if (gate === "sign-up") {
    return <InlineSignUp purpose="fix" cost={plan.cost} notice={signUpNotice} onDone={onSignedUp} />;
  }
  if (gate === "verify" && account) {
    return <VerifyEmailPanel email={account.email} alreadySent={verificationSent} fileName={file.name} onVerified={onVerified} />;
  }
  if (gate === "buy" && account) {
    return <BuyCreditsPanel cost={plan.cost} credits={account.credits} beforeRedirect={beforeRedirect} onBought={onBought} />;
  }
  if (gate === "waiting-payment") {
    return (
      <View style={styles.rowCenter}>
        <Spinner size={18} />
        <Text style={[theme.typography.body, { color: theme.colors.text }]}>
          Waiting for your payment to arrive… We'll fix your file as soon as it does.
        </Text>
      </View>
    );
  }
  const signedIn = !!account && !!loadToken();
  const label = fixButtonLabel(plan.cost, signedIn ? account!.credits : null);
  return (
    <View style={{ gap: 8 }}>
      <Button
        title={label}
        onPress={onFix}
        accessibilityHint={`Fixes ${plan.auto.length} ${plan.auto.length === 1 ? "thing" : "things"} and downloads the new file. You're only charged if at least one fix is made.`}
        style={{ alignSelf: "stretch", minHeight: 52 }}
      />
      <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
        You only pay if we actually fix something. We give you a new copy; your own file isn't changed.
      </Text>
    </View>
  );
}

function PlannedFixes({
  items,
  stage,
  outcome,
  file,
  report,
  width,
}: {
  items: PipelineViolation[];
  stage: Stage;
  outcome: Record<string, RowOutcome>;
  file: File;
  report: PipelineResponse;
  width: number;
}) {
  const theme = useTheme();
  const [expanded, setExpanded] = useState(false);
  const LIMIT = 8;
  const shown = expanded ? items : items.slice(0, LIMIT);
  const heading =
    stage === "done" ? "What we did" : stage === "fixing" ? "Working on it" : `What we'll fix (${items.length})`;
  return (
    <View style={{ gap: 10 }}>
      <Text style={[theme.typography.h2, { color: theme.colors.text }]} accessibilityRole="header" {...({ "aria-level": 3 } as any)}>
        {heading}
      </Text>
      <View style={{ gap: 8 }}>
        {shown.map((v) => {
          const o = outcome[v.id];
          const where = whereLabel(v, report ? (report.summary?.sourceFormat as any) : null);
          return (
            <View
              key={v.id}
              style={[styles.planRow, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface, borderRadius: theme.radius.md }]}
            >
              <View style={{ width, flexShrink: 0 }}>
                <LocationPreview v={v} file={file} fmt={(report.summary?.sourceFormat as any) ?? null} summary={report.summary} width={width} compact />
              </View>
              <View style={{ flex: 1, minWidth: 140, gap: 4 }}>
                <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>{plainTitle(v)}</Text>
                {where ? <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>{where}</Text> : null}
                <RowStatus stage={stage} outcome={o} />
              </View>
            </View>
          );
        })}
      </View>
      {items.length > LIMIT && !expanded ? (
        <Button title={`Show all ${items.length}`} variant="ghost" onPress={() => setExpanded(true)} style={{ alignSelf: "flex-start" }} />
      ) : null}
    </View>
  );
}

function RowStatus({ stage, outcome }: { stage: Stage; outcome?: RowOutcome }) {
  const theme = useTheme();
  if (stage === "fixing") {
    return (
      <View style={styles.rowCenter}>
        <Spinner size={14} />
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>Working…</Text>
      </View>
    );
  }
  if (stage === "done" && outcome === "fixed") {
    return (
      <View style={styles.rowCenter}>
        <Icon name="check-circle" size={16} color={theme.colors.success} />
        <Text style={[theme.typography.caption, { color: theme.colors.success, fontWeight: "700" }]}>Fixed</Text>
      </View>
    );
  }
  if (stage === "done") {
    return (
      <View style={styles.rowCenter}>
        <Icon name="arrow-down-circle" size={16} color={theme.colors.warning} />
        <Text style={[theme.typography.caption, { color: theme.colors.warning, fontWeight: "700" }]}>Needs you (see below)</Text>
      </View>
    );
  }
  return <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>We'll fix this for you.</Text>;
}

function DoneSummary({
  refEl,
  result,
  total,
  fixedN,
  cost,
  onFixAnother,
}: {
  refEl: React.MutableRefObject<any>;
  result: PipelineRemediateResult;
  total: number;
  fixedN: number;
  cost: number;
  onFixAnother: () => void;
}) {
  const theme = useTheme();
  const persisted = typeof result.persistedFixes === "number" ? result.persistedFixes : fixedN;
  const ok = persisted > 0;
  const money = result.chargePending
    ? `${cost} credits are taken when you download it.`
    : result.charged === true
      ? `${cost} credits used.`
      : result.charged === false
        ? "You were not charged."
        : "";
  const left = total - fixedN;
  return (
    <View style={{ gap: 12 }}>
      <View
        ref={refEl}
        {...({ tabIndex: -1 } as any)}
        style={[
          styles.doneBox,
          {
            borderColor: ok ? theme.colors.success : theme.colors.warning,
            backgroundColor: ok ? theme.colors.successSoft : theme.colors.warningSoft,
            borderRadius: theme.radius.md,
          },
        ]}
      >
        <Icon name={ok ? "check-circle" : "alert-triangle"} size={26} color={ok ? theme.colors.success : theme.colors.warning} />
        <View style={{ flex: 1, minWidth: 0, gap: 6 }}>
          <Text accessibilityRole="header" {...({ "aria-level": 2 } as any)} style={[theme.typography.h1, { color: theme.colors.text }]}>
            {ok ? "Done – your accessible file is downloading." : "We couldn't safely fix anything in this file."}
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.text, fontSize: 17, lineHeight: 26 }]}>
            {ok
              ? `${fixedHeadline(fixedN, total)} ${money}`
              : "We left it exactly as it was and didn't charge you. Here's what a person needs to do."}
          </Text>
          {ok && left > 0 ? (
            <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
              {left} {left === 1 ? "thing needs" : "things need"} a quick look from you. We show you where below.
            </Text>
          ) : null}
        </View>
      </View>
      <View style={styles.buttonRow}>
        {ok && result.downloadUrl ? (
          <Button title="Download again" variant="secondary" onPress={() => triggerDownload(result.downloadUrl, result.filename)} icon={<Icon name="download" size={16} />} />
        ) : null}
        <Button title="Fix another file" variant="ghost" onPress={onFixAnother} />
      </View>
    </View>
  );
}

function NeedsYou({
  items,
  triedIds,
  file,
  report,
  width,
  showAll,
  onShowAll,
}: {
  items: PipelineViolation[];
  triedIds: Set<string>;
  file: File;
  report: PipelineResponse;
  width: number;
  showAll: boolean;
  onShowAll: () => void;
}) {
  const theme = useTheme();
  if (items.length === 0) return null;
  const LIMIT = 12;
  const shown = showAll ? items : items.slice(0, LIMIT);
  const fmt = (report.summary?.sourceFormat as any) ?? null;
  return (
    <Card>
      <View style={{ gap: 14 }}>
        <View style={{ gap: 4 }}>
          <Text style={[theme.typography.h1, { color: theme.colors.text }]} accessibilityRole="header" {...({ "aria-level": 2 } as any)}>
            {items.length === 1 ? "1 thing needs a quick look from you" : `${items.length} things need a quick look from you`}
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
            These need a person's judgment, so we don't guess. Here's where each one is and what to do.
          </Text>
        </View>
        {shown.map((v, i) => {
          const where = whereLabel(v, fmt);
          const title = plainTitle(v);
          return (
            <View
              key={v.id}
              style={[styles.needRow, { borderTopColor: theme.colors.border, borderTopWidth: i === 0 ? 0 : StyleSheet.hairlineWidth }]}
            >
              <View style={{ flex: 1, minWidth: 220, gap: 6 }}>
                <Text style={[theme.typography.h2, { color: theme.colors.text }]} accessibilityRole="header" {...({ "aria-level": 3 } as any)}>
                  {title}
                </Text>
                {where ? <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>{where}</Text> : null}
                {triedIds.has(v.id) ? (
                  <Text style={[theme.typography.caption, { color: theme.colors.warning, fontWeight: "600" }]}>
                    We tried, but couldn't do this one safely.
                  </Text>
                ) : null}
                <Text style={[theme.typography.body, { color: theme.colors.text }]}>
                  <Text style={{ fontWeight: "700" }}>What to do: </Text>
                  {whatToDo(v)}
                </Text>
                <GuideLink href={guideHref(v.ruleId)} title={title} />
              </View>
              <View style={{ width, maxWidth: "100%" }}>
                <LocationPreview v={v} file={file} fmt={fmt} summary={report.summary} width={width} />
              </View>
            </View>
          );
        })}
        {items.length > LIMIT && !showAll ? (
          <Button title={`Show all ${items.length}`} variant="ghost" onPress={onShowAll} style={{ alignSelf: "flex-start" }} />
        ) : null}
      </View>
    </Card>
  );
}

/** Opens the step-by-step guide in a new tab, so this page (and the file
 *  in it) stays exactly where it is. */
function GuideLink({ href, title }: { href: string; title: string }) {
  const theme = useTheme();
  return (
    <Pressable
      {...linkProps(href)}
      {...({ hrefAttrs: { target: "_blank", rel: "noopener" } } as any)}
      accessibilityLabel={`How to fix it: ${title} (opens in a new tab)`}
      style={({ focused, hovered }: any) => [
        styles.guideLink,
        { borderColor: theme.colors.border, borderRadius: theme.radius.sm },
        hovered ? { borderColor: theme.colors.accent, backgroundColor: theme.colors.accentSoft } : null,
        focused
          ? ({ outlineColor: theme.colors.accent, outlineWidth: 2, outlineStyle: "solid", outlineOffset: 2 } as any)
          : null,
      ]}
    >
      <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>How to fix it</Text>
      <Icon name="external-link" size={14} color={theme.colors.textMuted} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  srOnly: {
    position: "absolute",
    width: 1,
    height: 1,
    overflow: "hidden",
    opacity: 0,
  },
  rowCenter: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  buttonRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    alignItems: "center",
  },
  statusLine: {
    flexDirection: "row",
    alignItems: "center",
    flexWrap: "wrap",
    gap: 12,
    borderWidth: 1,
    paddingHorizontal: 14,
    paddingVertical: 12,
    width: "100%",
  },
  fileIcon: {
    width: 44,
    height: 44,
    alignItems: "center",
    justifyContent: "center",
  },
  planRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "center",
    gap: 12,
    borderWidth: 1,
    padding: 10,
  },
  doneBox: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 12,
    borderWidth: 1,
    padding: 16,
  },
  needRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 16,
    paddingTop: 16,
  },
  guideLink: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    alignSelf: "flex-start",
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 8,
    marginTop: 2,
  },
});
