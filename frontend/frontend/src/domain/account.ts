/**
 * Account + credits domain bound to the real backend.
 *
 * Backend endpoints used:
 *   POST /auth/sign-in           -> { user, token }
 *   GET  /auth/me                -> { user }
 *   POST /auth/sign-out
 *   POST /auth/grant-starter     (best effort; idempotent on the server)
 *   GET  /credits/balance        -> { balance, history }
 *   POST /credits/purchase       -> { user }
 *   POST /credits/spend          -> { user }
 *
 * Storage (web only):
 *   508-account-v2-token   -> raw JWT
 *   508-account-v2-cache   -> last successful Account snapshot (offline fallback)
 *
 * Keep the localStorage cache so the UI doesn't go blank on a slow backend
 * fetch; refreshAccount() updates it whenever a fresh /auth/me lands.
 *
 * Errors fail soft: callers get null + a console.warn; the UI surfaces a
 * toast where appropriate. Nothing here should ever throw to the React tree.
 */

import { Platform } from "react-native";

import { useAppStore } from "../store/useAppStore";

export type HistoryKind = "purchase" | "spend" | "grant" | "refund";

export interface HistoryEntry {
  id: string;
  at: string;
  kind: HistoryKind;
  amount: number;
  description: string;
}

export interface Account {
  id: string;
  email: string;
  displayName: string;
  createdAt: string;
  credits: number;
  history: HistoryEntry[];
  hasPassword: boolean;
  emailVerifiedAt: string | null;
}

export type Tier = "starter" | "pro" | "studio";

const TOKEN_KEY = "508-account-v2-token";
const CACHE_KEY = "508-account-v2-cache";

let memoryToken: string | null = null;
let memoryAccount: Account | null = null;

function _isWeb(): boolean {
  return Platform.OS === "web" && typeof window !== "undefined";
}

function _readToken(): string | null {
  if (!_isWeb()) return memoryToken;
  try {
    // Prefer localStorage (long-lived). Fall back to sessionStorage for users
    // who explicitly chose "do not keep me signed in" at sign-in time.
    const local = window.localStorage.getItem(TOKEN_KEY);
    if (local) return local;
    const session = window.sessionStorage.getItem(TOKEN_KEY);
    if (session) return session;
    return null;
  } catch {
    return memoryToken;
  }
}

/**
 * Persist the auth token. The optional `remember` flag controls storage:
 *   true  (default) -> localStorage, survives tab close + browser restart
 *   false           -> sessionStorage, cleared when the tab closes
 * Always clears the opposite store so we never leave a stale copy behind.
 */
function _writeToken(token: string | null, remember: boolean = true): void {
  memoryToken = token;
  if (!_isWeb()) return;
  try {
    if (token === null) {
      window.localStorage.removeItem(TOKEN_KEY);
      window.sessionStorage.removeItem(TOKEN_KEY);
      return;
    }
    if (remember) {
      window.localStorage.setItem(TOKEN_KEY, token);
      window.sessionStorage.removeItem(TOKEN_KEY);
    } else {
      window.sessionStorage.setItem(TOKEN_KEY, token);
      window.localStorage.removeItem(TOKEN_KEY);
    }
  } catch {
    // ignore quota
  }
}

function _readCache(): Account | null {
  if (!_isWeb()) return memoryAccount;
  try {
    const raw = window.localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return _coerceAccount(parsed);
  } catch {
    return memoryAccount;
  }
}

function _writeCache(account: Account | null): void {
  memoryAccount = account;
  if (!_isWeb()) return;
  try {
    if (account === null) window.localStorage.removeItem(CACHE_KEY);
    else window.localStorage.setItem(CACHE_KEY, JSON.stringify(account));
  } catch {
    // ignore quota
  }
}

function _isHistoryEntry(x: any): x is HistoryEntry {
  return (
    x &&
    typeof x.id === "string" &&
    typeof x.at === "string" &&
    (x.kind === "purchase" || x.kind === "spend" || x.kind === "grant" || x.kind === "refund") &&
    typeof x.amount === "number" &&
    typeof x.description === "string"
  );
}

function _coerceAccount(raw: any): Account | null {
  if (!raw || typeof raw !== "object") return null;
  // Accept either the shape we emit or the backend's UserOut shape.
  const id = typeof raw.id === "string" ? raw.id : null;
  const email = typeof raw.email === "string" ? raw.email : null;
  if (!id || !email) return null;
  const displayName =
    typeof raw.displayName === "string" && raw.displayName
      ? raw.displayName
      : typeof raw.display_name === "string" && raw.display_name
      ? raw.display_name
      : email.split("@")[0] || "You";
  const createdAt =
    typeof raw.createdAt === "string"
      ? raw.createdAt
      : typeof raw.created_at === "string"
      ? raw.created_at
      : new Date().toISOString();
  // Backend returns the field as `creditsBalance`; older callers may pass
  // `credits` or `credits_balance`. Accept any of the three so the cache
  // and UI never silently fall back to 0 after a successful sign-in.
  const credits =
    typeof raw.creditsBalance === "number"
      ? raw.creditsBalance
      : typeof raw.credits === "number"
      ? raw.credits
      : typeof raw.credits_balance === "number"
      ? raw.credits_balance
      : 0;
  const history = Array.isArray(raw.history) ? raw.history.filter(_isHistoryEntry) : [];
  const hasPassword =
    typeof raw.hasPassword === "boolean"
      ? raw.hasPassword
      : typeof raw.has_password === "boolean"
      ? raw.has_password
      : false;
  const emailVerifiedAt =
    typeof raw.emailVerifiedAt === "string" && raw.emailVerifiedAt
      ? raw.emailVerifiedAt
      : typeof raw.email_verified_at === "string" && raw.email_verified_at
      ? raw.email_verified_at
      : null;
  return {
    id,
    email,
    displayName,
    createdAt,
    credits,
    history,
    hasPassword,
    emailVerifiedAt,
  };
}

function _baseUrl(): string {
  try {
    const u = useAppStore.getState().apiBaseUrl || "";
    return u.replace(/\/+$/, "");
  } catch {
    return "";
  }
}

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const base = _baseUrl();
  const url = base + (path.startsWith("/") ? path : "/" + path);
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...((init && (init.headers as Record<string, string>)) || {}),
  };
  if (init && init.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const token = _readToken();
  if (token) headers["Authorization"] = "Bearer " + token;
  return fetch(url, { ...init, headers });
}

async function _readJson(res: Response): Promise<any> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

/** Read the cached account synchronously. Always cheap, never throws. */
export function loadAccount(): Account | null {
  return _readCache();
}

export function loadToken(): string | null {
  return _readToken();
}

/**
 * Sign in to the backend, persist the JWT + account cache, and best-effort
 * grant the starter credit pack. Returns the fresh account + token.
 *
 * Throws on transport/credential failure so the caller can surface an inline
 * error in the modal.
 */
export async function signIn(
  email: string,
  displayName?: string,
  password?: string,
  remember: boolean = true,
): Promise<{ user: Account; token: string }> {
  const trimmedEmail = (email || "").trim();
  const trimmedName = (displayName || "").trim() || trimmedEmail.split("@")[0] || "You";
  const reqBody: Record<string, string> = {
    email: trimmedEmail,
    displayName: trimmedName,
  };
  if (typeof password === "string" && password.length > 0) {
    reqBody.password = password;
  }
  const res = await apiFetch("/auth/sign-in", {
    method: "POST",
    body: JSON.stringify(reqBody),
  });
  if (!res.ok) {
    const body = await _readJson(res);
    const detail = (body && (body.detail || body.message)) || ("HTTP " + res.status);
    throw new Error(typeof detail === "string" ? detail : "Sign in failed");
  }
  const body = await _readJson(res);
  const token: string | null = body && typeof body.token === "string" ? body.token : null;
  const user = _coerceAccount(body && body.user);
  if (!token || !user) {
    throw new Error("Sign in succeeded but response was malformed.");
  }
  _writeToken(token, remember);
  _writeCache(user);

  // Best-effort starter grant. Idempotent on the server; ignore failures.
  try {
    const grantRes = await apiFetch("/auth/grant-starter", { method: "POST" });
    if (grantRes.ok) {
      const grantBody = await _readJson(grantRes);
      const refreshed = _coerceAccount(grantBody && (grantBody.user || grantBody));
      if (refreshed) _writeCache(refreshed);
    }
  } catch (e) {
    console.warn("[account] grant-starter failed (ignored)", e);
  }

  return { user: _readCache() || user, token };
}

/** Forget local credentials and fire-and-forget a server logout. */
export function signOut(): void {
  const token = _readToken();
  _writeToken(null);
  _writeCache(null);
  if (!token) return;
  try {
    void fetch(_baseUrl() + "/auth/sign-out", {
      method: "POST",
      headers: { Authorization: "Bearer " + token },
    }).catch(() => undefined);
  } catch {
    // ignore
  }
}

/**
 * Pull the latest account snapshot from /auth/me. Returns null when signed
 * out or when the call fails; never throws.
 */
export async function refreshAccount(): Promise<Account | null> {
  if (!_readToken()) return null;
  try {
    const res = await apiFetch("/auth/me");
    if (res.status === 401) {
      _writeToken(null);
      _writeCache(null);
      return null;
    }
    if (!res.ok) {
      console.warn("[account] /auth/me", res.status);
      return _readCache();
    }
    const body = await _readJson(res);
    const next = _coerceAccount(body && (body.user || body));
    if (next) _writeCache(next);
    return next ?? _readCache();
  } catch (e) {
    console.warn("[account] refreshAccount failed", e);
    return _readCache();
  }
}

/**
 * Buy a credit tier server-side. Updates the local cache and returns the
 * fresh account on success; throws on failure so the caller can toast.
 */
export async function purchaseTier(tier: Tier): Promise<Account> {
  const res = await apiFetch("/credits/purchase", {
    method: "POST",
    body: JSON.stringify({ tier }),
  });
  if (!res.ok) {
    const body = await _readJson(res);
    const detail = (body && (body.detail || body.message)) || ("HTTP " + res.status);
    throw new Error(typeof detail === "string" ? detail : "Purchase failed");
  }
  const body = await _readJson(res);
  const next = _coerceAccount(body && (body.user || body));
  if (!next) throw new Error("Purchase succeeded but response was malformed.");
  _writeCache(next);
  return next;
}

// ---------------------------------------------------------------------------
// Real Stripe billing: credit-pack checkout, subscriptions, billing portal.
// ---------------------------------------------------------------------------

export interface BillingTierInfo {
  tier: string;
  credits: number;
  priceConfigured: boolean;
}

export interface SubscriptionPlanInfo {
  plan: string;
  monthlyCredits: number;
  priceConfigured: boolean;
}

export interface BillingConfig {
  enabled: boolean;
  tiers: BillingTierInfo[];
  subscriptionPlans: SubscriptionPlanInfo[];
}

export interface SubscriptionStatus {
  active: boolean;
  plan?: string | null;
  status?: string | null;
  currentPeriodEnd?: string | null;
  monthlyCredits?: number | null;
  overageEnabled?: boolean;
}

function _origin(): string {
  if (typeof window !== "undefined" && window.location) return window.location.origin;
  return "";
}

/** Report whether real Stripe billing is configured (and the available plans). */
export async function getBillingConfig(): Promise<BillingConfig> {
  try {
    const res = await apiFetch("/billing/config");
    if (!res.ok) return { enabled: false, tiers: [], subscriptionPlans: [] };
    const body = await _readJson(res);
    return {
      enabled: !!(body && body.enabled),
      tiers: (body && body.tiers) || [],
      subscriptionPlans: (body && body.subscriptionPlans) || [],
    };
  } catch {
    return { enabled: false, tiers: [], subscriptionPlans: [] };
  }
}

/** Current user's active subscription (active=false when none). Never throws. */
export async function getSubscription(): Promise<SubscriptionStatus> {
  if (!_readToken()) return { active: false };
  try {
    const res = await apiFetch("/billing/subscription");
    if (!res.ok) return { active: false };
    const body = await _readJson(res);
    return (body as SubscriptionStatus) || { active: false };
  } catch {
    return { active: false };
  }
}

async function _checkoutUrl(path: string, payload: Record<string, unknown>): Promise<string> {
  const origin = _origin();
  const res = await apiFetch(path, {
    method: "POST",
    body: JSON.stringify({
      ...payload,
      success_url: `${origin}/account`,
      cancel_url: `${origin}/billing`,
    }),
  });
  if (!res.ok) {
    const body = await _readJson(res);
    const detail = (body && (body.detail || body.message)) || "HTTP " + res.status;
    throw new Error(typeof detail === "string" ? detail : "Checkout failed");
  }
  const body = await _readJson(res);
  const url = body && typeof body.url === "string" ? body.url : null;
  if (!url) throw new Error("Checkout session did not return a URL.");
  return url;
}

/** Start a one-time credit-pack Stripe Checkout; returns the redirect URL. */
export function startCreditCheckout(tier: string): Promise<string> {
  return _checkoutUrl("/billing/create-checkout-session", { tier });
}

/** Start a recurring subscription Stripe Checkout; returns the redirect URL. */
export function startSubscriptionCheckout(plan: string): Promise<string> {
  return _checkoutUrl("/billing/create-subscription-session", { plan });
}

/** Open the Stripe Billing Portal (manage/cancel); returns the redirect URL. */
export async function openBillingPortal(): Promise<string> {
  const res = await apiFetch("/billing/create-portal-session", {
    method: "POST",
    body: JSON.stringify({ return_url: `${_origin()}/account` }),
  });
  if (!res.ok) {
    const body = await _readJson(res);
    const detail = (body && (body.detail || body.message)) || "HTTP " + res.status;
    throw new Error(typeof detail === "string" ? detail : "Could not open billing portal");
  }
  const body = await _readJson(res);
  const url = body && typeof body.url === "string" ? body.url : null;
  if (!url) throw new Error("Portal session did not return a URL.");
  return url;
}

export interface IssuedCertificate {
  certificateId: string;
  issuedAt: string;
  issuedTo?: string | null;
  filename: string;
  conformanceClaim: string;
  score: number;
  fixedCount: number;
  remainingCount: number;
  paidWith: string;
  verifyUrl: string;
}

/**
 * Issue a verifiable conformance certificate. Free for active subscribers,
 * otherwise spends credits. Throws with `.status === 402` when the caller has
 * neither, so the caller can route them to billing.
 */
export async function issueCertificate(payload: {
  documentId: string;
}): Promise<IssuedCertificate> {
  const res = await apiFetch("/billing/issue-certificate", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await _readJson(res);
    const detail = (body && (body.detail || body.message)) || "HTTP " + res.status;
    const err = new Error(typeof detail === "string" ? detail : "Certificate failed") as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return (await _readJson(res)) as IssuedCertificate;
}

/** Toggle auto-overage on the caller's active subscription. */
export async function setOverage(enabled: boolean): Promise<SubscriptionStatus | null> {
  try {
    const res = await apiFetch("/billing/overage", { method: "POST", body: JSON.stringify({ enabled }) });
    if (!res.ok) return null;
    return (await _readJson(res)) as SubscriptionStatus;
  } catch {
    return null;
  }
}

/** Public: verify a certificate by id (no auth required). Returns null if not found. */
export async function verifyCertificate(certId: string): Promise<IssuedCertificate | null> {
  try {
    const res = await apiFetch(`/billing/certificate/${encodeURIComponent(certId)}`);
    if (!res.ok) return null;
    return (await _readJson(res)) as IssuedCertificate;
  } catch {
    return null;
  }
}

/**
 * Spend credits server-side. Returns true on success, false on insufficient
 * funds or any transport failure (logged + cached state untouched).
 */
export async function spendCredits(
  amount: number,
  description: string,
  relatedDocId?: string,
): Promise<boolean> {
  if (!Number.isFinite(amount) || amount <= 0) return false;
  if (!_readToken()) return false;
  try {
    const res = await apiFetch("/credits/spend", {
      method: "POST",
      body: JSON.stringify({
        amount,
        description,
        related_doc_id: relatedDocId ?? null,
      }),
    });
    if (res.status === 402) return false; // insufficient funds
    if (!res.ok) {
      console.warn("[account] /credits/spend", res.status);
      return false;
    }
    const body = await _readJson(res);
    const next = _coerceAccount(body && (body.user || body));
    if (next) _writeCache(next);
    return true;
  } catch (e) {
    console.warn("[account] spendCredits failed", e);
    return false;
  }
}

/**
 * Back-compat shim for callers that used to optimistically add credits to
 * the local cache. Now it just nudges /credits/purchase via a tier guess and
 * falls back to a cache-only bump so demo flows keep working.
 *
 * Prefer purchaseTier() for real flows.
 */
export async function addCredits(amount: number, description: string): Promise<void> {
  if (!Number.isFinite(amount) || amount <= 0) return;
  const cached = _readCache();
  if (!cached) return;
  const next: Account = {
    ...cached,
    credits: cached.credits + amount,
    history: [
      {
        id: "h-" + Date.now().toString(36),
        at: new Date().toISOString(),
        kind: "purchase" as HistoryKind,
        amount,
        description,
      },
      ...cached.history,
    ].slice(0, 200),
  };
  _writeCache(next);
}

/**
 * Idempotently ask the server for the starter pack. Safe to call after
 * sign-in completes; failures are swallowed.
 */
export async function grantStarterCredits(): Promise<void> {
  if (!_readToken()) return;
  try {
    const res = await apiFetch("/auth/grant-starter", { method: "POST" });
    if (!res.ok) return;
    const body = await _readJson(res);
    const next = _coerceAccount(body && (body.user || body));
    if (next) _writeCache(next);
  } catch (e) {
    console.warn("[account] grantStarterCredits failed", e);
  }
}


/**
 * Update the current user's profile (display name and/or email).
 * Returns the fresh account on success and writes through to the cache.
 * Throws on transport/server failure so the caller can surface a toast.
 */
export async function updateProfile(patch: {
  displayName?: string;
  email?: string;
}): Promise<Account> {
  if (!_readToken()) throw new Error("Not signed in.");
  const body: Record<string, string> = {};
  if (patch.displayName !== undefined) body.displayName = patch.displayName.trim();
  if (patch.email !== undefined) body.email = patch.email.trim().toLowerCase();
  const res = await apiFetch("/auth/me", {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const j = await _readJson(res);
    const detail = (j && (j.detail || j.message)) || "HTTP " + res.status;
    throw new Error(typeof detail === "string" ? detail : "Update failed");
  }
  const j = await _readJson(res);
  const next = _coerceAccount(j && (j.user || j));
  if (!next) throw new Error("Update succeeded but response was malformed.");
  // Preserve cached history (PATCH /me only returns user fields)
  const cached = _readCache();
  const merged: Account = {
    ...next,
    history: cached?.history ?? next.history,
  };
  _writeCache(merged);
  return merged;
}

/**
 * Download a JSON dump of the user's data from /auth/export.
 * Returns the response body as a Blob so the caller can trigger a download.
 * Throws on failure.
 */
export async function exportData(): Promise<Blob> {
  if (!_readToken()) throw new Error("Not signed in.");
  const res = await apiFetch("/auth/export", { method: "GET" });
  if (!res.ok) {
    const j = await _readJson(res);
    const detail = (j && (j.detail || j.message)) || "HTTP " + res.status;
    throw new Error(typeof detail === "string" ? detail : "Export failed");
  }
  return await res.blob();
}

/**
 * Permanently delete the current user's account and cascade-clear the
 * server-side ledger.  Always clears local credentials + cache and reloads
 * the page on web, even if the server call fails (so the UI can't get
 * stranded with a stale session).
 */
export async function deleteAccount(): Promise<void> {
  const token = _readToken();
  try {
    if (token) {
      const res = await apiFetch("/auth/me", { method: "DELETE" });
      if (!res.ok && res.status !== 401 && res.status !== 404) {
        const j = await _readJson(res);
        const detail = (j && (j.detail || j.message)) || "HTTP " + res.status;
        // Preserve fail-soft: clear locally regardless, but surface the err.
        _writeToken(null);
        _writeCache(null);
        throw new Error(typeof detail === "string" ? detail : "Delete failed");
      }
    }
  } finally {
    _writeToken(null);
    _writeCache(null);
    if (_isWeb()) {
      try {
        window.location.reload();
      } catch {
        // ignore
      }
    }
  }
}


/**
 * Set or change the signed-in user's password. Returns the fresh account.
 * Throws on failure.
 */
export async function setPassword(password: string): Promise<Account> {
  if (!_readToken()) throw new Error("Not signed in.");
  if (!password || password.length < 4) {
    throw new Error("Password must be at least 4 characters.");
  }
  const res = await apiFetch("/auth/set-password", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
  if (!res.ok) {
    const j = await _readJson(res);
    const detail = (j && (j.detail || j.message)) || "HTTP " + res.status;
    throw new Error(typeof detail === "string" ? detail : "Set password failed");
  }
  const j = await _readJson(res);
  const next = _coerceAccount(j && (j.user || j));
  if (!next) throw new Error("Set password succeeded but response was malformed.");
  // Preserve cached history (set-password only returns user fields).
  const cached = _readCache();
  const merged: Account = {
    ...next,
    history: cached?.history ?? next.history,
  };
  _writeCache(merged);
  return merged;
}

/**
 * Ask the backend to mint a verification token + log the magic link.
 * Returns true if the request was queued. Fails soft.
 */
export async function requestEmailVerification(): Promise<boolean> {
  if (!_readToken()) return false;
  try {
    const res = await apiFetch("/auth/request-verify-email", { method: "POST" });
    if (!res.ok) {
      console.warn("[account] request-verify-email", res.status);
      return false;
    }
    const j = await _readJson(res);
    return !!(j && j.queued);
  } catch (e) {
    console.warn("[account] requestEmailVerification failed", e);
    return false;
  }
}

// ---------------------------------------------------------------------------
// Teams (multi-seat shared subscription wallet)
// ---------------------------------------------------------------------------

export interface TeamMember {
  userId: string;
  email?: string | null;
  displayName?: string | null;
  role: string;
  joinedAt: string;
  isOwner: boolean;
}

export interface TeamInvite {
  id: string;
  email: string;
  role: string;
  status: string;
  createdAt: string;
  acceptUrl?: string | null;
}

export interface Team {
  id: string;
  name: string;
  ownerId: string;
  seatLimit: number;
  seatsUsed: number;
  role: string; // the caller's role
  members: TeamMember[];
  invites: TeamInvite[];
}

export interface MyTeam {
  team: Team | null;
  canCreate: boolean;
}

async function _throwDetail(res: Response, fallback: string): Promise<never> {
  const body = await _readJson(res);
  const detail = (body && (body.detail || body.message)) || "HTTP " + res.status;
  const err = new Error(typeof detail === "string" ? detail : fallback) as Error & { status?: number };
  err.status = res.status;
  throw err;
}

/** The caller's team (or null) plus whether they're eligible to create one. */
export async function getMyTeam(): Promise<MyTeam> {
  if (!_readToken()) return { team: null, canCreate: false };
  try {
    const res = await apiFetch("/teams/me");
    if (!res.ok) return { team: null, canCreate: false };
    return ((await _readJson(res)) as MyTeam) || { team: null, canCreate: false };
  } catch {
    return { team: null, canCreate: false };
  }
}

/** Create a team (active subscribers only). Throws (`.status === 402`) otherwise. */
export async function createTeam(name: string): Promise<Team> {
  const res = await apiFetch("/teams", { method: "POST", body: JSON.stringify({ name }) });
  if (!res.ok) await _throwDetail(res, "Could not create team");
  return (await _readJson(res)) as Team;
}

/** Invite a teammate by email. Throws (`.status === 409`) when seats are full. */
export async function inviteTeamMember(email: string, role: string = "member"): Promise<TeamInvite> {
  const res = await apiFetch("/teams/invite", { method: "POST", body: JSON.stringify({ email, role }) });
  if (!res.ok) await _throwDetail(res, "Could not send invite");
  return (await _readJson(res)) as TeamInvite;
}

/** Accept an invite by token. Throws on mismatch / full / unknown. */
export async function acceptTeamInvite(token: string): Promise<Team> {
  const res = await apiFetch("/teams/accept", { method: "POST", body: JSON.stringify({ token }) });
  if (!res.ok) await _throwDetail(res, "Could not accept invite");
  return (await _readJson(res)) as Team;
}

/** Remove a member (admins only). Returns true on success. */
export async function removeTeamMember(userId: string): Promise<boolean> {
  try {
    const res = await apiFetch("/teams/remove", { method: "POST", body: JSON.stringify({ userId }) });
    return res.ok;
  } catch {
    return false;
  }
}

/** Revoke a pending invite (admins only). Returns true on success. */
export async function revokeTeamInvite(inviteId: string): Promise<boolean> {
  try {
    const res = await apiFetch("/teams/revoke-invite", { method: "POST", body: JSON.stringify({ inviteId }) });
    return res.ok;
  } catch {
    return false;
  }
}

/** Leave the team you belong to (non-owners). Returns true on success. */
export async function leaveTeam(): Promise<boolean> {
  try {
    const res = await apiFetch("/teams/leave", { method: "POST" });
    return res.ok;
  } catch {
    return false;
  }
}

/** Disband the team you own (removes all members + invites). Returns true on success. */
export async function disbandTeam(): Promise<boolean> {
  try {
    const res = await apiFetch("/teams", { method: "DELETE" });
    return res.ok;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Admin metrics dashboard
// ---------------------------------------------------------------------------

export interface AdminMetrics {
  users: { total: number; verified: number; newLast30d: number };
  subscriptions: { active: number; byPlan: Record<string, number>; estimatedMrrUsd: number };
  credits: { granted: number; spent: number; outstanding: number };
  certificates: { total: number; last30d: number; bySubscription: number; byCredits: number };
  teams: { count: number; seatsTotal: number; seatsUsed: number };
  overage: { charges: number; revenueUsd: number };
  recentCertificates: Array<{ id: string; issuedTo?: string | null; filename: string; paidWith: string; issuedAt: string }>;
  recentSubscriptions: Array<{ plan: string; status: string; createdAt: string }>;
  generatedAt: string;
}

/**
 * Fetch admin metrics. Throws with `.status === 403` for non-admins (so the
 * page can show an access-denied state) and `.status === 401` when signed out.
 */
export async function getAdminMetrics(): Promise<AdminMetrics> {
  const res = await apiFetch("/admin/metrics");
  if (!res.ok) await _throwDetail(res, "Could not load metrics");
  return (await _readJson(res)) as AdminMetrics;
}
