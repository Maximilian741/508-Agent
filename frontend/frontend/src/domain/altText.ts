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
  altText: string;
  provider: string;
  confidence: number;
  aiConfigured: boolean;
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
