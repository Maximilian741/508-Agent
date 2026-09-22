/**
 * Client for the standalone AI alt-text tool (POST /tools/alt-text).
 *
 * Sends a single image as multipart/form-data. We deliberately do NOT set a
 * Content-Type header — the browser must set the multipart boundary itself.
 * Requires a signed-in user (the backend enforces auth); the call is free.
 */
import { getBackendUrlInfo } from "../config/backendUrl";
import { loadToken } from "./account";

export interface AltTextResult {
  /** Empty when no honest description could be written; see `message`. */
  altText: string;
  provider: string;
  confidence: number;
  aiConfigured: boolean;
  /** Plain-language reason shown when `altText` is empty. */
  message?: string | null;
}

export interface AltTextAvailability {
  available: boolean;
  /** Plain-language reason when `available` is false. */
  message?: string | null;
}

/**
 * Whether the tool can describe pictures on this deployment (no sign-in
 * needed). Resolves to null when the check itself fails, so the page falls
 * back to letting the person try.
 */
export async function fetchAltTextAvailability(): Promise<AltTextAvailability | null> {
  try {
    const res = await fetch(`${getBackendUrlInfo().url}/tools/alt-text/availability`);
    if (!res.ok) return null;
    const body = (await res.json()) as AltTextAvailability;
    return typeof body?.available === "boolean" ? body : null;
  } catch {
    return null;
  }
}

export async function generateAltTextForImage(file: File): Promise<AltTextResult> {
  const base = getBackendUrlInfo().url;
  const token = loadToken();
  const form = new FormData();
  form.append("file", file);
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = "Bearer " + token;

  const res = await fetch(`${base}/tools/alt-text`, {
    method: "POST",
    body: form,
    headers,
  });
  if (!res.ok) {
    let message = "";
    try {
      message = await res.text();
    } catch {
      message = "";
    }
    const err = new Error(message || "Alt-text generation failed") as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return (await res.json()) as AltTextResult;
}
