/**
 * Render a page of the person's OWN file (the File they dropped, never an
 * upload) to an image URL, with the bundled pdf.js in public/pdfjs.
 *
 * Why the bundled copy and a <script type="module"> tag rather than
 * `import("pdfjs-dist")` + a CDN worker: the production CSP is
 * `script-src 'self'` / `worker-src 'self' blob:` (scripts/render-nginx-conf.mjs),
 * so a CDN worker is blocked, and Metro would try to bundle a dynamic
 * import. /pdfjs/pdf.mjs sets `globalThis.pdfjsLib` when it runs; its worker
 * is /pdfjs/pdf.worker.mjs on our own origin. No eval (isEvalSupported off).
 *
 * Each page is rendered once per file and width, then reused by every
 * finding on that page (a 40-finding PDF renders only the pages it needs).
 */
import { Platform } from "react-native";

const LIB_SRC = "/pdfjs/pdf.mjs";
const WORKER_SRC = "/pdfjs/pdf.worker.mjs";

let libPromise: Promise<any> | null = null;

function loadPdfJs(): Promise<any> {
  if (Platform.OS !== "web" || typeof window === "undefined" || typeof document === "undefined") {
    return Promise.reject(new Error("pdf preview is web-only"));
  }
  const existing = (window as any).pdfjsLib;
  if (existing?.getDocument) {
    if (!existing.GlobalWorkerOptions.workerSrc) existing.GlobalWorkerOptions.workerSrc = WORKER_SRC;
    return Promise.resolve(existing);
  }
  if (libPromise) return libPromise;
  libPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.type = "module";
    script.src = LIB_SRC;
    script.async = true;
    script.onload = () => {
      const lib = (window as any).pdfjsLib;
      if (!lib?.getDocument) {
        libPromise = null;
        reject(new Error("pdf.js did not load"));
        return;
      }
      lib.GlobalWorkerOptions.workerSrc = WORKER_SRC;
      resolve(lib);
    };
    script.onerror = () => {
      libPromise = null;
      reject(new Error("pdf.js could not be fetched"));
    };
    document.head.appendChild(script);
  });
  return libPromise;
}

const docs = new WeakMap<File, Promise<any>>();
const pageImages = new WeakMap<File, Map<string, Promise<PageImage>>>();

export interface PageImage {
  url: string;
  /** Rendered pixel size (the page as a viewer shows it, /Rotate applied). */
  width: number;
  height: number;
}

function openDoc(file: File): Promise<any> {
  let p = docs.get(file);
  if (!p) {
    p = loadPdfJs().then(async (lib) => {
      const data = new Uint8Array(await file.arrayBuffer());
      return lib.getDocument({ data, isEvalSupported: false, disableFontFace: false }).promise;
    });
    // A failure should not stick forever (e.g. pdf.js fetch hiccup).
    p.catch(() => docs.delete(file));
    docs.set(file, p);
  }
  return p;
}

/** Page count of the file (null when it can't be opened). */
export async function pdfPageCount(file: File): Promise<number | null> {
  try {
    const doc = await openDoc(file);
    return typeof doc?.numPages === "number" ? doc.numPages : null;
  } catch {
    return null;
  }
}

/**
 * Render page `pageNumber` (1-based) at `cssWidth` CSS pixels wide. Resolves
 * to an object URL for a PNG. Rejects when the page can't be drawn.
 */
export function renderPdfPage(file: File, pageNumber: number, cssWidth: number): Promise<PageImage> {
  const width = Math.max(120, Math.min(900, Math.round(cssWidth)));
  let perFile = pageImages.get(file);
  if (!perFile) {
    perFile = new Map();
    pageImages.set(file, perFile);
  }
  const key = `${pageNumber}@${width}`;
  const cached = perFile.get(key);
  if (cached) return cached;
  const job = (async () => {
    const doc = await openDoc(file);
    const n = Math.min(Math.max(1, Math.floor(pageNumber)), doc.numPages);
    const page = await doc.getPage(n);
    const base = page.getViewport({ scale: 1 });
    const dpr = Math.min(2, (typeof window !== "undefined" && window.devicePixelRatio) || 1);
    const viewport = page.getViewport({ scale: (width * dpr) / base.width });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("no 2d context");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvasContext: ctx, viewport }).promise;
    const blob: Blob | null = await new Promise((resolve) => canvas.toBlob((b) => resolve(b), "image/png"));
    if (!blob) throw new Error("could not encode the page");
    return { url: URL.createObjectURL(blob), width: canvas.width, height: canvas.height };
  })();
  job.catch(() => perFile!.delete(key));
  perFile.set(key, job);
  return job;
}

const objectUrls = new WeakMap<File, string>();

/** An object URL for a picture file the person dropped (reused per file). */
export function localImageUrl(file: File): string | null {
  if (Platform.OS !== "web" || typeof URL === "undefined") return null;
  let url = objectUrls.get(file);
  if (!url) {
    try {
      url = URL.createObjectURL(file);
      objectUrls.set(file, url);
    } catch {
      return null;
    }
  }
  return url;
}
