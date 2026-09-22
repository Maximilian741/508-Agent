/**
 * PdfPreview — live in-browser preview of the source document, scoped to the
 * page the current violation is on.
 *
 * Why it exists:
 *   The audit screen is a sequential review.  Without a visual anchor the
 *   user has to flip back to the original PDF in another tab, find page N,
 *   and remember which figure / heading is being flagged.  Having the page
 *   render right next to the IssueCard collapses that context-switch.
 *
 * Scope:
 *   - PDF rendering: handled here via pdfjs-dist (dynamic import, web only).
 *   - DOCX / PPTX: we intentionally do NOT try to render Office formats in
 *     the browser.  pdfjs is single-purpose, and a passable Office renderer
 *     would mean shipping LibreOffice WASM or a server-side rasterizer.
 *     Instead we show a friendly placeholder explaining the limitation and
 *     point the user at the audit findings.
 *   - Native (iOS/Android via Expo Go): we return null.  pdfjs requires a
 *     browser DOM (canvas, Worker, FileReader) and bundling it on RN
 *     pulls in shims we don't want to maintain.
 *
 * Race conditions:
 *   The user can press j/k rapidly to flip through issues.  Each page change
 *   kicks off a getDocument + render pipeline that is async.  We track the
 *   in-flight render task and the current "generation" number; when either
 *   the file or page changes we cancel the old task and ignore its result.
 */

import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Platform, StyleSheet, Text, View } from "react-native";

import { loadPdfJs } from "../flow/pdfPages";
import { useTheme } from "../useTheme";

interface PdfPreviewProps {
  file: File | null;
  page: number | null;
}

// pdf.js comes from our own origin (public/pdfjs, via loadPdfJs). It used to
// load its worker from cdn.jsdelivr.net, which the production CSP
// (script-src/worker-src 'self') blocks: every PDF preview on the Advanced
// audit screen failed in production while working in development.

const isPdfFile = (file: File): boolean => {
  if (file.type === "application/pdf") return true;
  const name = file.name?.toLowerCase() ?? "";
  return name.endsWith(".pdf");
};

const formatLabel = (file: File): string => {
  const name = file.name?.toLowerCase() ?? "";
  if (file.type.includes("wordprocessingml") || name.endsWith(".docx")) return "DOCX";
  if (file.type.includes("presentationml") || name.endsWith(".pptx")) return "PPTX";
  const ext = name.split(".").pop();
  return ext ? ext.toUpperCase() : "this format";
};

export function PdfPreview({ file, page }: PdfPreviewProps) {
  const theme = useTheme();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  // No file → render nothing.  Caller controls layout collapse.
  // Native → pdfjs requires DOM, bail out cleanly.
  const isNoop = !file || Platform.OS !== "web";

  useEffect(() => {
    if (isNoop) return;
    if (!file || !isPdfFile(file)) return;
    if (!page || page < 1) {
      // Some violations have null page (document-level findings).  Show a
      // gentle "no page anchor" message instead of trying to render.
      setStatus("idle");
      setError(null);
      return;
    }

    // Generation token: if the user moves to another issue while we're
    // mid-render we bump this and the stale render's `then` becomes a no-op.
    let cancelled = false;
    let renderTask: { cancel: () => void; promise: Promise<unknown> } | null = null;

    (async () => {
      setStatus("loading");
      setError(null);
      try {
        const pdfjs: any = await loadPdfJs();
        if (cancelled) return;

        const arrayBuffer = await file.arrayBuffer();
        if (cancelled) return;

        // No eval: the CSP forbids it, and pdf.js falls back cleanly.
        const pdf = await pdfjs.getDocument({ data: arrayBuffer, isEvalSupported: false }).promise;
        if (cancelled) {
          // Best effort cleanup — pdf.destroy is sync-ish.
          try { pdf.destroy?.(); } catch { /* swallow */ }
          return;
        }

        const targetPage = Math.min(Math.max(1, page), pdf.numPages);
        const pageObj = await pdf.getPage(targetPage);
        if (cancelled) return;

        // Compute scale to fit a 320–480px sidebar.  We render at scale 1.5
        // for crisp text, then constrain via CSS width so the canvas shrinks
        // to the column.  This keeps text legible on hi-DPI displays without
        // forcing a re-render at every viewport change.
        const viewport = pageObj.getViewport({ scale: 1.5 });
        const canvas = canvasRef.current;
        if (!canvas) return;
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        const ctx = canvas.getContext("2d");
        if (!ctx) {
          throw new Error("Canvas 2D context unavailable.");
        }
        // Clear before rendering — switching pages on the same canvas would
        // otherwise leak the previous frame underneath while the new one paints.
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        renderTask = pageObj.render({ canvasContext: ctx, viewport });
        await renderTask!.promise;
        if (cancelled) return;
        setStatus("ready");
      } catch (err: any) {
        if (cancelled) return;
        // pdfjs throws a "RenderingCancelledException" when we cancel — that
        // is an expected race, not a user-visible error.
        const name = err?.name ?? "";
        if (name === "RenderingCancelledException") return;
        setStatus("error");
        setError(err?.message || "Could not render this page.");
      }
    })();

    return () => {
      cancelled = true;
      try {
        renderTask?.cancel();
      } catch {
        /* swallow — task may already be settled */
      }
    };
  }, [file, page, isNoop]);

  // === Render branches =====================================================

  if (isNoop) return null;

  if (file && !isPdfFile(file)) {
    // DOCX / PPTX placeholder — rendering Office formats in the browser is
    // outside this component's scope.  See file-level comment.
    const fmt = formatLabel(file);
    return (
      <View
        style={[
          styles.container,
          { borderColor: theme.colors.border, backgroundColor: theme.colors.surface },
        ]}
      >
        <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>
          Preview unavailable
        </Text>
        <Text
          style={[
            theme.typography.body,
            { color: theme.colors.textMuted, marginTop: theme.spacing.xs },
          ]}
        >
          Preview is not available for {fmt} files yet — see the audit findings
          to the right.
        </Text>
      </View>
    );
  }

  // PDF branch.
  return (
    <View
      style={[
        styles.container,
        { borderColor: theme.colors.border, backgroundColor: theme.colors.surface },
      ]}
    >
      <View style={styles.header}>
        <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600" }]}>
          Page preview
        </Text>
        {page ? (
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            Page {page}
          </Text>
        ) : null}
      </View>

      {!page ? (
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          This issue is not anchored to a specific page.
        </Text>
      ) : null}

      {status === "loading" ? (
        <View style={styles.loadingRow}>
          <ActivityIndicator size="small" color={theme.colors.accent} />
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            Rendering page {page}…
          </Text>
        </View>
      ) : null}

      {status === "error" ? (
        <View
          style={[
            styles.errorBox,
            { borderColor: theme.colors.danger, backgroundColor: `${theme.colors.danger}1A` },
          ]}
        >
          <Text style={[theme.typography.body, { color: theme.colors.danger }]}>
            Couldn't render this page.
          </Text>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
            {error}
          </Text>
        </View>
      ) : null}

      {/*
        The canvas is always mounted while we have a PDF + page so the ref is
        available when the effect runs.  We hide it visually until the first
        render finishes to avoid a flash of empty white.
       */}
      {page ? (
        <div
          // The wrapper div uses a CSS max-width so the canvas scales down to
          // the sidebar; we keep the intrinsic resolution high for crisp text
          // and let the browser downscale.
          style={{
            width: "100%",
            maxWidth: 480,
            overflow: "auto",
            borderRadius: 4,
            opacity: status === "ready" ? 1 : 0,
            transition: "opacity 120ms ease-out",
          }}
        >
          <canvas
            ref={canvasRef}
            style={{
              width: "100%",
              height: "auto",
              display: "block",
              backgroundColor: "#fff",
            }}
          />
        </div>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    width: "100%",
    maxWidth: 480,
    minWidth: 320,
    padding: 12,
    borderRadius: 4,
    borderWidth: 1,
    gap: 8,
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
  },
  loadingRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 8,
  },
  errorBox: {
    padding: 10,
    borderRadius: 4,
    borderWidth: 1,
    gap: 4,
  },
});
