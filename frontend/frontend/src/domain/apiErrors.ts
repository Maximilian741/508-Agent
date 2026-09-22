/**
 * Turn any failure from the API into ONE plain sentence a person can act on.
 *
 * The backend's coded errors (app/api/errors.py) send
 *   {"detail": <legacy>, "code": "empty_upload", "message": "That file is empty. ..."}
 * and `message` is written for customers, so it wins. An older backend only
 * sends `detail` — a machine token ("verify_email_first"), a sentence, or
 * FastAPI's validation list — so we map the known tokens and sentence
 * prefixes ourselves and fall back to a sentence per HTTP status.
 *
 * Never shown to a person: raw JSON, a stack, an exception name, an env var,
 * or a rule id. Anything that looks like one is replaced by the generic line.
 */

export interface ApiErrorInfo {
  /** HTTP status, or null for a network failure / unknown throw. */
  status: number | null;
  /** Stable machine code when the backend (or our mapping) knows one. */
  code: string | null;
  /** The backend's customer-facing sentence, when it sent one. */
  message: string | null;
  /** The raw `detail` when it was a string. */
  detail: string | null;
  /** Seconds to wait (429). */
  retryAfter: number | null;
  /** True when the request never got an HTTP answer (offline, DNS, CORS). */
  network: boolean;
}

/** Codes -> the sentence we show when the backend did not send `message`. */
const SENTENCES: Record<string, string> = {
  empty_upload: "That file is empty. Choose it again, or save it again and upload the new copy.",
  file_content_mismatch:
    "That file's contents don't match its name (for example, a PDF renamed to .docx). Save it in its real format and try again.",
  invalid_ooxml:
    "That Word or PowerPoint file is damaged or isn't really an Office file. Open it, save a new copy and try that.",
  too_large: "That file is too big for us to check. Try compressing it, or split it into smaller parts.",
  unsupported_type:
    "We can't open that type of file. Try a PDF, Word, PowerPoint, Excel, web page or picture file.",
  upload_failed: "Something went wrong while sending your file. Please try again.",
  password_protected:
    "This PDF has a password, so we can't read it. Save a copy without the password and try again. You were not charged.",
  invalid_pdf:
    "We couldn't open this PDF. It may be damaged or only partly downloaded. Save or export it again and try again. You were not charged.",
  invalid_document:
    "We couldn't open this file. It may be damaged, or not really the type its name says. You were not charged.",
  unreadable_document: "We couldn't fix this file. It may be locked or damaged. You were not charged.",
  write_failed: "We couldn't write the fixed file. You were not charged. Please try again.",
  save_failed: "We couldn't save the fixed file, so there's nothing to give you. You were not charged. Please try again.",
  content_loss_refused:
    "We stopped before writing this file: the fixed copy came out with less text than yours, and we won't hand you a file that loses words. You were not charged.",
  insufficient_credits: "You don't have enough credits for this file. You were not charged.",
  authentication_required: "Please sign in to do that.",
  missing_account: "Please sign in to do that.",
  unknown_account: "We couldn't find your account. Please sign in again.",
  invalid_credentials: "That password doesn't match this email address.",
  password_required: "Choose a password with at least 8 characters.",
  too_many_attempts: "Too many tries. Wait a minute, then try again.",
  verify_email_first: "Confirm your email address first. We sent you a link.",
  rate_limited: "Too many tries. Wait a minute, then try again.",
  billing_not_configured: "Buying credits isn't switched on for this site yet.",
  internal_error: "Something went wrong on our side. Please try again.",
};

/** Legacy `detail` sentences -> code (matched by prefix, like the backend). */
const DETAIL_PREFIXES: Array<[string, string]> = [
  ["file_content_mismatch", "file_content_mismatch"],
  ["invalid_ooxml", "invalid_ooxml"],
  ["upload_rejected", "too_large"],
  ["upload_io_failure", "upload_failed"],
  ["Unsupported file type", "unsupported_type"],
  ["Insufficient credits", "insufficient_credits"],
  ["Failed to parse document", "invalid_document"],
  ["Failed to write the remediated file", "write_failed"],
  ["Could not remediate this file", "unreadable_document"],
];

const STATUS_SENTENCES: Record<number, string> = {
  400: "We couldn't use that file. Please check it and try again.",
  401: "Please sign in to do that.",
  402: "You don't have enough credits for this file. You were not charged.",
  403: "You don't have access to that.",
  404: "We couldn't find that. It may have expired.",
  408: "That took too long. Please try again.",
  413: "That file is too big for us to check. Try compressing it, or split it into smaller parts.",
  415: "We can't open that type of file. Try a PDF, Word, PowerPoint, Excel, web page or picture file.",
  422: "We couldn't read this file. It may be damaged. You were not charged.",
  429: "Too many tries. Wait a minute, then try again.",
};

const GENERIC = "Something went wrong. Please try again.";
const OFFLINE = "We can't reach the service right now. Check your internet connection and try again.";

const MACHINE_RE = /^[a-z][a-z0-9_]{1,63}$/;

/** A sentence we may show verbatim: words, no JSON, no stack, not too long. */
function looksHuman(text: string): boolean {
  const t = text.trim();
  if (!t || t.length > 400) return false;
  if (!t.includes(" ")) return false;
  if (/^[[{<]/.test(t)) return false;
  if (/Traceback|Exception|Error:|\bat [\w.]+ \(|_API_KEY|stack|undefined|null\b/.test(t)) return false;
  return true;
}

/** Read status / code / message out of whatever was thrown. Never throws. */
export function readApiError(err: unknown): ApiErrorInfo {
  const info: ApiErrorInfo = {
    status: null,
    code: null,
    message: null,
    detail: null,
    retryAfter: null,
    network: false,
  };
  const e = err as any;
  if (e && typeof e.status === "number") info.status = e.status;
  if (e && typeof e.code === "string" && MACHINE_RE.test(e.code)) info.code = e.code;
  // account.ts attaches the backend's customer sentence here.
  if (e && typeof e.serverMessage === "string" && e.serverMessage.trim()) info.message = e.serverMessage.trim();
  if (e && typeof e.retryAfter === "number") info.retryAfter = e.retryAfter;
  const raw: string = typeof e?.message === "string" ? e.message : typeof err === "string" ? err : "";

  // fetch() rejects with a TypeError ("Failed to fetch", "NetworkError when
  // attempting to fetch resource", "Load failed") when no HTTP answer came.
  if (info.status === null && (e instanceof TypeError || /failed to fetch|networkerror|load failed|network request failed/i.test(raw))) {
    info.network = true;
    return info;
  }

  let body: any = null;
  const trimmed = raw.trim();
  if (trimmed.startsWith("{")) {
    try {
      body = JSON.parse(trimmed);
    } catch {
      body = null;
    }
  }
  if (body && typeof body === "object") {
    if (typeof body.code === "string" && MACHINE_RE.test(body.code)) info.code = body.code;
    if (typeof body.message === "string" && body.message.trim() && !info.message) info.message = body.message.trim();
    if (typeof body.detail === "string") info.detail = body.detail;
    if (typeof body.retryAfter === "number") info.retryAfter = body.retryAfter;
  } else if (trimmed) {
    info.detail = trimmed;
  }
  if (!info.code && info.detail) {
    if (MACHINE_RE.test(info.detail)) info.code = info.detail;
    else {
      for (const [prefix, code] of DETAIL_PREFIXES) {
        if (info.detail.startsWith(prefix)) {
          info.code = code;
          break;
        }
      }
    }
  }
  return info;
}

/**
 * The one sentence to show. Precedence: the backend's own customer message;
 * our sentence for a known code; a human `detail`; the status's sentence.
 */
export function humanError(err: unknown): string {
  const info = readApiError(err);
  if (info.network) return OFFLINE;
  if (info.message && looksHuman(info.message)) return withRetry(info.message, info);
  // "Unsupported file type: .xyz" names the extension; keep that detail.
  if (info.code === "unsupported_type" && info.detail) {
    const ext = /:\s*(\.[a-z0-9]{1,12})\s*$/i.exec(info.detail)?.[1];
    if (ext) return `We can't open ${ext} files yet. Try a PDF, Word, PowerPoint, Excel, web page or picture file.`;
  }
  // A detail that is already a sentence written for a person (413 limits,
  // the intake's "save it as .docx" advice) is more specific than our line.
  if (info.detail && looksHuman(info.detail) && !DETAIL_PREFIXES.some(([p]) => info.detail!.startsWith(p))) {
    if (info.code === null || info.code === "too_large" || info.status === 400 || info.status === 413) {
      return info.detail;
    }
  }
  if (info.code && SENTENCES[info.code]) return withRetry(SENTENCES[info.code], info);
  if (info.detail && looksHuman(info.detail)) return info.detail;
  if (info.status !== null) {
    if (info.status >= 500) return SENTENCES.internal_error;
    if (STATUS_SENTENCES[info.status]) return withRetry(STATUS_SENTENCES[info.status], info);
  }
  return GENERIC;
}

function withRetry(sentence: string, info: ApiErrorInfo): string {
  if (info.status === 429 && info.retryAfter && info.retryAfter > 0 && info.retryAfter <= 3600) {
    const secs = Math.ceil(info.retryAfter);
    const wait = secs < 90 ? `${secs} seconds` : `${Math.ceil(secs / 60)} minutes`;
    return `Too many tries. Wait about ${wait}, then try again.`;
  }
  return sentence;
}

/** Convenience for branching: the HTTP status, or null. */
export function errorStatus(err: unknown): number | null {
  return readApiError(err).status;
}

/** Convenience for branching: the stable code, or null. */
export function errorCode(err: unknown): string | null {
  return readApiError(err).code;
}
