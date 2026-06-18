/**
 * Developer API key management (client side).
 *
 * Thin wrappers over the JWT-authed /api-keys endpoints. The plaintext key is
 * returned only by createApiKey() and must be shown to the user immediately —
 * it can never be retrieved again.
 */

import { loadToken } from "./account";

export interface ApiKeyDTO {
  id: string;
  name: string;
  keyPrefix: string;
  createdAt: string;
  lastUsedAt?: string | null;
  revoked: boolean;
}

export interface CreatedApiKeyDTO extends ApiKeyDTO {
  /** The full secret — present only on creation. Show once, then it's gone. */
  key: string;
}

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...(extra || {}) };
  const token = loadToken();
  if (token) headers["Authorization"] = "Bearer " + token;
  return headers;
}

export async function listApiKeys(baseUrl: string): Promise<ApiKeyDTO[]> {
  const r = await fetch(`${baseUrl}/api-keys`, { headers: authHeaders() });
  if (!r.ok) throw new Error((await r.text()) || "Couldn't load API keys");
  return (await r.json()) as ApiKeyDTO[];
}

export async function createApiKey(baseUrl: string, name: string): Promise<CreatedApiKeyDTO> {
  const r = await fetch(`${baseUrl}/api-keys`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ name }),
  });
  if (!r.ok) throw new Error((await r.text()) || "Couldn't create API key");
  return (await r.json()) as CreatedApiKeyDTO;
}

export async function revokeApiKey(baseUrl: string, id: string): Promise<ApiKeyDTO> {
  const r = await fetch(`${baseUrl}/api-keys/${encodeURIComponent(id)}/revoke`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!r.ok) throw new Error((await r.text()) || "Couldn't revoke API key");
  return (await r.json()) as ApiKeyDTO;
}
