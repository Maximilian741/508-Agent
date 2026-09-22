/**
 * LocationPreview — SHOW where a finding is in the person's own document.
 *
 *   pdf-region  the page, rendered from the local File with the bundled
 *               pdf.js, with the finding's box drawn on it (PDF user space:
 *               origin bottom-left, scaled by pageSize). A picture upload is
 *               drawn from the local image the same way.
 *   image       the picture's thumbnail (from the API), outlined.
 *   text        the snippet with the offending words marked (underline + tint,
 *               never colour alone) and repeated in words below.
 *   table       a mini grid: the table's real first row, then blank rows.
 *   document    a properties card, with the missing property marked.
 *
 * Nothing here is invented: every word shown comes from the document (the
 * API guarantees `highlight` is a literal substring of `snippet`) or from the
 * scan summary. When a location is missing we show less, never a guess.
 */
import React, { useEffect, useState } from "react";
import { Image, Platform, StyleSheet, Text, View } from "react-native";

import type { PipelineSummary, PipelineViolation } from "../../api/client";
import type { RemediateFormat } from "../../domain/creditCosts";
import { fileExtension, isImageFile, isPdfFile } from "../../domain/fixPlan";
import { Icon } from "../components/Icon";
import { useTheme } from "../useTheme";
import { localImageUrl, PageImage, renderPdfPage } from "./pdfPages";

/** The page-highlight colours: the page itself is always drawn on white. */
const BOX_STROKE = "#C4320A";
const BOX_FILL = "rgba(196, 50, 10, 0.14)";

interface Props {
  v: PipelineViolation;
  file: File | null;
  fmt: RemediateFormat | null;
  summary?: PipelineSummary | null;
  /** Preview width in CSS px (the caller knows its column). */
  width: number;
  /** Smaller, for the live "working on it" list. */
  compact?: boolean;
}

export function LocationPreview({ v, file, fmt, summary, width, compact = false }: Props) {
  const loc = v.location ?? null;
  const page = (loc?.page ?? v.page ?? null) as number | null;
  const bbox = Array.isArray(loc?.bbox) && loc!.bbox!.length === 4 ? (loc!.bbox as number[]) : null;
  const pageSize = Array.isArray(loc?.pageSize) && loc!.pageSize!.length === 2 ? (loc!.pageSize as number[]) : null;
  const canDrawPage = !!file && typeof page === "number" && page > 0 && (isPdfFile(file) || drawableImage(file));

  const kind = loc?.kind ?? fallbackKind(v, fmt);

  if (kind === "pdf-region" && canDrawPage) {
    return (
      <View style={{ gap: 8 }}>
        <PageView file={file!} page={page!} bbox={bbox} pageSize={pageSize} width={width} />
        {!compact && loc?.snippet ? <Snippet snippet={loc.snippet} highlight={loc.highlight ?? null} /> : null}
      </View>
    );
  }
  if (kind === "image") {
    if (loc?.thumbnail) {
      return (
        <View style={{ gap: 8 }}>
          <Thumbnail uri={loc.thumbnail} width={compact ? Math.min(width, 120) : Math.min(width, 240)} />
          {!compact && loc.snippet ? <Snippet snippet={loc.snippet} highlight={loc.highlight ?? null} label="Its description now" /> : null}
        </View>
      );
    }
    if (canDrawPage) return <PageView file={file!} page={page!} bbox={bbox} pageSize={pageSize} width={width} />;
    return <Placeholder icon="image" label="A picture" compact={compact} />;
  }
  if (kind === "table") {
    return <MiniTable snippet={loc?.snippet ?? null} compact={compact} width={width} />;
  }
  if (kind === "text" || kind === "pdf-region") {
    const snippet = loc?.snippet ?? evidenceText(v);
    return (
      <View style={{ gap: 8 }}>
        {canDrawPage && !compact ? <PageView file={file!} page={page!} bbox={null} pageSize={pageSize} width={width} /> : null}
        {snippet ? <Snippet snippet={snippet} highlight={loc ? loc.highlight ?? null : snippet} compact={compact} /> : null}
      </View>
    );
  }
  if (kind === "document") {
    // A document-level finding on a PDF with a page (e.g. an unlabeled form
    // field counted on the root) still has somewhere to look.
    if (canDrawPage && bbox) return <PageView file={file!} page={page!} bbox={bbox} pageSize={pageSize} width={width} />;
    return <PropertiesCard v={v} file={file} summary={summary ?? null} compact={compact} />;
  }
  return null;
}

function drawableImage(file: File): boolean {
  // Browsers can't draw TIFF (except Safari) and BMP support is patchy.
  return isImageFile(file) && !["tif", "tiff", "bmp"].includes(fileExtension(file.name));
}

/** Kind for an older backend with no `location`: from the rule and evidence. */
function fallbackKind(v: PipelineViolation, fmt: RemediateFormat | null): string {
  const r = v.ruleId;
  if (/^TABLE_/.test(r) || r === "DATA_RANGE_HEADERS_UNCLEAR") return "table";
  if (/ALT_TEXT|DECORATIVE_IMAGE/.test(r)) return "image";
  if (evidenceText(v)) return "text";
  if (/^DOCUMENT_(TITLE|LANGUAGE)|^PDF_UNTAGGED|^SCANNED_/.test(r)) return "document";
  if (fmt === "pdf" && typeof v.page === "number") return "pdf-region";
  return "none";
}

function evidenceText(v: PipelineViolation): string | null {
  const t = (v.evidence as any)?.text;
  return typeof t === "string" && t.trim() ? t.trim().slice(0, 200) : null;
}

// ---------------------------------------------------------------------------

function PageView({
  file,
  page,
  bbox,
  pageSize,
  width,
}: {
  file: File;
  page: number;
  bbox: number[] | null;
  pageSize: number[] | null;
  width: number;
}) {
  const theme = useTheme();
  const [img, setImg] = useState<PageImage | null>(null);
  const [failed, setFailed] = useState(false);
  const isPicture = !isPdfFile(file);

  useEffect(() => {
    let alive = true;
    setFailed(false);
    if (isPicture) {
      const url = localImageUrl(file);
      if (!url) {
        setFailed(true);
        return;
      }
      // Size comes from the page frame (the picture IS the page).
      const w = pageSize?.[0] ?? 4;
      const h = pageSize?.[1] ?? 3;
      setImg({ url, width: w, height: h });
      return;
    }
    renderPdfPage(file, page, width)
      .then((r) => {
        if (alive) setImg(r);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [file, page, width, isPicture, pageSize?.[0], pageSize?.[1]]);

  const aspect = img ? img.height / img.width : pageSize ? pageSize[1] / pageSize[0] : 1.294;
  const height = Math.round(width * aspect);
  const box = bbox && pageSize ? boxStyle(bbox, pageSize, width, height) : null;
  const label = `Page ${page} of your file${box ? ", with the problem outlined" : ""}.`;

  if (failed) {
    return (
      <View style={[styles.pageFrame, { width, height: 72, borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 }]}>
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted, padding: 10 }]}>
          Page {page} (we couldn't draw a preview of it).
        </Text>
      </View>
    );
  }
  return (
    <View
      accessible
      accessibilityRole="image"
      accessibilityLabel={label}
      style={[styles.pageFrame, { width, height, borderColor: theme.colors.border, backgroundColor: "#FFFFFF" }]}
    >
      {img ? (
        <Image source={{ uri: img.url }} style={{ width, height }} resizeMode="stretch" accessibilityIgnoresInvertColors />
      ) : (
        <View style={[StyleSheet.absoluteFill, { alignItems: "center", justifyContent: "center", backgroundColor: theme.colors.surface2 }]}>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>Drawing page {page}…</Text>
        </View>
      )}
      {img && box ? (
        <View
          pointerEvents="none"
          style={[
            styles.box,
            box,
            Platform.OS === "web" ? ({ boxShadow: "0 0 0 2px #FFFFFF, 0 0 0 4px rgba(0,0,0,0.25)" } as any) : null,
          ]}
        />
      ) : null}
    </View>
  );
}

/** PDF user space (origin bottom-left) -> CSS box in the rendered page. */
function boxStyle(bbox: number[], pageSize: number[], width: number, height: number) {
  const [pw, ph] = pageSize;
  if (!(pw > 0 && ph > 0)) return null;
  let [x0, y0, x1, y1] = bbox;
  if (x1 < x0) [x0, x1] = [x1, x0];
  if (y1 < y0) [y0, y1] = [y1, y0];
  let left = (x0 / pw) * width;
  let top = ((ph - y1) / ph) * height;
  let w = ((x1 - x0) / pw) * width;
  let h = ((y1 - y0) / ph) * height;
  // A tiny box (one word on a full page) is grown around its centre so it
  // can be seen; it never leaves the page.
  const MIN = 14;
  if (w < MIN) {
    left -= (MIN - w) / 2;
    w = MIN;
  }
  if (h < MIN) {
    top -= (MIN - h) / 2;
    h = MIN;
  }
  left = Math.max(0, Math.min(width - w, left));
  top = Math.max(0, Math.min(height - h, top));
  return { left, top, width: Math.min(w, width), height: Math.min(h, height) };
}

function Thumbnail({ uri, width }: { uri: string; width: number }) {
  const theme = useTheme();
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  useEffect(() => {
    let alive = true;
    Image.getSize(
      uri,
      (w, h) => {
        if (alive && w > 0 && h > 0) setSize({ w, h });
      },
      () => undefined,
    );
    return () => {
      alive = false;
    };
  }, [uri]);
  const w = size ? Math.min(width, size.w) : width;
  const h = size ? Math.round((w * size.h) / size.w) : Math.round(width * 0.66);
  return (
    <View
      accessible
      accessibilityRole="image"
      accessibilityLabel="The picture this is about."
      style={[styles.thumbFrame, { width: w + 6, borderColor: BOX_STROKE, backgroundColor: theme.colors.surface2 }]}
    >
      <Image source={{ uri }} style={{ width: w, height: h }} resizeMode="contain" accessibilityIgnoresInvertColors />
    </View>
  );
}

function Snippet({
  snippet,
  highlight,
  label,
  compact = false,
}: {
  snippet: string;
  highlight: string | null;
  label?: string;
  compact?: boolean;
}) {
  const theme = useTheme();
  const text = snippet.slice(0, compact ? 90 : 200);
  const at = highlight ? text.indexOf(highlight) : -1;
  const before = at >= 0 ? text.slice(0, at) : text;
  const marked = at >= 0 ? text.slice(at, at + highlight!.length) : "";
  const after = at >= 0 ? text.slice(at + highlight!.length) : "";
  return (
    <View
      style={[
        styles.snippet,
        { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2, borderLeftColor: theme.colors.accent },
      ]}
    >
      {label ? <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>{label}</Text> : null}
      <Text style={[theme.typography.body, { color: theme.colors.text }]} numberOfLines={compact ? 2 : undefined}>
        “{before}
        {marked ? (
          <Text
            style={{
              backgroundColor: theme.colors.accentSoft,
              color: theme.colors.text,
              fontWeight: "700",
              textDecorationLine: "underline",
              textDecorationStyle: "solid",
            }}
          >
            {marked}
          </Text>
        ) : null}
        {after}”
      </Text>
      {marked && marked !== text && !compact ? (
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>Marked: “{marked}”</Text>
      ) : null}
    </View>
  );
}

function MiniTable({ snippet, compact, width }: { snippet: string | null; compact: boolean; width: number }) {
  const theme = useTheme();
  const cells = (snippet || "")
    .split(" | ")
    .map((c) => c.trim())
    .filter((c) => c.length > 0)
    .slice(0, compact ? 3 : 5);
  const cols = Math.max(cells.length, 3);
  const blank = Array.from({ length: cols });
  return (
    <View
      accessible
      accessibilityRole="image"
      accessibilityLabel={cells.length ? `A table whose first row reads: ${cells.join(", ")}.` : "A table."}
      style={{ width: Math.min(width, 360), gap: 6 }}
    >
      <View style={[styles.grid, { borderColor: theme.colors.border }]}>
        <View style={[styles.gridRow, { borderColor: BOX_STROKE, borderWidth: 2 }]}>
          {(cells.length ? cells : blank.map(() => "")).map((c, i) => (
            <View key={i} style={[styles.gridCell, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 }]}>
              <Text style={[theme.typography.caption, { color: theme.colors.text, fontWeight: "600" }]} numberOfLines={1}>
                {c || " "}
              </Text>
            </View>
          ))}
        </View>
        {[0, 1].map((r) => (
          <View key={r} style={styles.gridRow}>
            {blank.map((_, i) => (
              <View key={i} style={[styles.gridCell, { borderColor: theme.colors.border }]}>
                <View style={{ height: 6, width: "60%", borderRadius: 3, backgroundColor: theme.colors.surface3 }} />
              </View>
            ))}
          </View>
        ))}
      </View>
      {!compact && cells.length ? (
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>The table's first row (outlined).</Text>
      ) : null}
    </View>
  );
}

function PropertiesCard({
  v,
  file,
  summary,
  compact,
}: {
  v: PipelineViolation;
  file: File | null;
  summary: PipelineSummary | null;
  compact: boolean;
}) {
  const theme = useTheme();
  const rows: Array<{ k: string; val: string; bad: boolean }> = [];
  if (file) rows.push({ k: "File", val: file.name, bad: false });
  const title = (summary?.title || "").trim();
  const lang = (summary?.language || "").trim();
  rows.push({ k: "Title", val: title || "none", bad: v.ruleId === "DOCUMENT_TITLE_MISSING" });
  rows.push({ k: "Language", val: lang || "not set", bad: v.ruleId === "DOCUMENT_LANGUAGE_MISSING" });
  if (v.ruleId === "PDF_UNTAGGED") rows.push({ k: "Reading structure", val: "none", bad: true });
  if (v.ruleId === "SCANNED_DOCUMENT_NO_TEXT") rows.push({ k: "Text you can select", val: "none", bad: true });
  if (v.ruleId === "DOCUMENT_NO_HEADINGS") rows.push({ k: "Headings", val: "none", bad: true });
  if (summary && summary.pageCount > 0 && (summary.sourceFormat === "pdf" || summary.sourceFormat === "pptx")) {
    rows.push({ k: summary.sourceFormat === "pptx" ? "Slides" : "Pages", val: String(summary.pageCount), bad: false });
  }
  const shown = compact ? rows.filter((r) => r.bad).slice(0, 1) : rows;
  if (shown.length === 0) return <Placeholder icon="file-text" label="The whole file" compact={compact} />;
  return (
    <View style={[styles.props, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 }]}>
      {!compact ? (
        <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>File properties</Text>
      ) : null}
      {shown.map((r) => (
        <View key={r.k} style={styles.propRow}>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted, minWidth: 92 }]}>{r.k}</Text>
          <Text
            style={[
              theme.typography.body,
              { color: theme.colors.text, flexShrink: 1 },
              r.bad
                ? {
                    fontWeight: "700",
                    textDecorationLine: "underline",
                    backgroundColor: theme.colors.accentSoft,
                  }
                : null,
            ]}
            numberOfLines={1}
          >
            {r.val}
            {r.bad ? " ← this" : ""}
          </Text>
        </View>
      ))}
    </View>
  );
}

function Placeholder({ icon, label, compact }: { icon: "image" | "file-text"; label: string; compact: boolean }) {
  const theme = useTheme();
  return (
    <View style={[styles.placeholder, { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2, height: compact ? 40 : 56 }]}>
      <Icon name={icon} size={18} color={theme.colors.textMuted} />
      <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  pageFrame: {
    position: "relative",
    overflow: "hidden",
    borderWidth: 1,
    borderRadius: 6,
  },
  box: {
    position: "absolute",
    borderWidth: 3,
    borderColor: BOX_STROKE,
    backgroundColor: BOX_FILL,
    borderRadius: 3,
  },
  thumbFrame: {
    borderWidth: 3,
    borderRadius: 6,
    padding: 0,
    alignItems: "center",
    overflow: "hidden",
  },
  snippet: {
    borderWidth: 1,
    borderLeftWidth: 3,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 4,
  },
  grid: {
    borderWidth: 1,
    borderRadius: 6,
    overflow: "hidden",
  },
  gridRow: {
    flexDirection: "row",
  },
  gridCell: {
    flex: 1,
    minWidth: 0,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 6,
    paddingVertical: 6,
    justifyContent: "center",
  },
  props: {
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    gap: 6,
  },
  propRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  placeholder: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 12,
  },
});
