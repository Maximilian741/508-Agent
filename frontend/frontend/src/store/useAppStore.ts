import { create } from "zustand";

import {
    ApiClient,
    DocumentDiffResponse,
    DocumentSummary,
    DocumentIssue,
    EvidenceBundleCreateOptions,
    EvidenceBundleCreateResponse,
    EvidenceBundleSummary,
    ExecutionResult,
    FixReport,
    Issue,
    JobScorePass,
    ManualReviewItem,
    PolicyDetail,
    PolicySummary,
    ScanRequest,
    ScanResponse,
    TagTreeResponse,
    UploadResponse,
    createApiClient,
} from "../api/client";
import { Platform } from "react-native";
import { BackendUrlSource, fetchBackendUrlInfo, getBackendUrlInfo } from "../config/backendUrl";

export interface DocumentPayload {
    documentId: string;
    sourceFormat: string;
    content: string;
}

interface RunResult<T> {
    ok: boolean;
    data?: T;
    error?: string;
    results?: ExecutionResult[];
}

interface AppState {
    apiBaseUrl: string;
    backendUrlSource: BackendUrlSource;
    backendUrlWarning: string | null;
    backendHealth: "unknown" | "ok" | "error";
    backendHealthMessage: string | null;
    /** Upload cap the backend advertises on /healthz, in MB. null until probed. */
    maxUploadMb: number | null;
    themeMode: "system" | "light" | "dark";
    mockMode: boolean;
    selectedDocument: DocumentPayload | null;
    scanResults: ScanResponse | null;
    manualReviewQueue: ManualReviewItem[];
    manualReviewLastFetched: string | null;
    uploadedDocument: UploadResponse | null;
    scanJob: { jobId: string; status: string; progress: number; message?: string } | null;
    documentIssues: DocumentIssue[];
    fixedDocId: string | null;
    documentSummary: DocumentSummary | null;
    tagTree: TagTreeResponse | null;
    fixReport: FixReport | null;
    policies: { items: PolicySummary[]; loaded: boolean; error?: string };
    policyDetailsById: Record<string, PolicyDetail>;
    selectedPolicyId: string | null;
    jobScoresByJobId: Record<string, JobScorePass[]>;
    evidenceBundlesByDocId: Record<string, EvidenceBundleSummary[]>;
    isExportingBundle: boolean;
    exportError?: string;
    isScanning: boolean;
    isUploading: boolean;
    /**
     * Number of free scans the visitor has used in this browser. Stored in
     * localStorage so the gate is sticky across reloads. The first scan is
     * free; subsequent gated actions (apply, download, second upload) prompt
     * sign-in unless a token exists or the bypass flag is on.
     */
    freeScansUsed: number;
    /**
     * Dev/test escape hatch. When true, all "1 free scan" gating is skipped
     * regardless of freeScansUsed. Persisted in localStorage so refreshes
     * keep the bypass on across a testing session.
     */
    bypassFreeScanGate: boolean;
    /**
     * Auto-fix policy for new audits. Determines which AI-suggested fixes
     * are pre-approved when scan completes:
     *   - "conservative": pre-approves nothing
     *   - "balanced": pre-approves heuristic high-confidence + deterministic
     *   - "aggressive": pre-approves all AI / heuristic suggestions
     */
    autoFixPolicy: "conservative" | "balanced" | "aggressive";
    setFreeScansUsed: (value: number) => void;
    setBypassFreeScanGate: (value: boolean) => void;
    setAutoFixPolicy: (value: "conservative" | "balanced" | "aggressive") => void;
    setApiBaseUrl: (value: string) => void;
    saveApiBaseUrl: (value: string) => Promise<void>;
    setBackendUrlInfo: (url: string, source: BackendUrlSource, warning?: string) => void;
    refreshBackendUrl: () => Promise<void>;
    setMockMode: (value: boolean) => void;
    setThemeMode: (value: "system" | "light" | "dark") => void;
    setSelectedDocument: (doc: DocumentPayload) => void;
    setScanResults: (results: ScanResponse) => void;
    addManualReviewItem: (item: ManualReviewItem) => void;
    clearManualReviewQueue: () => void;
    fetchManualReview: (docId?: string) => Promise<RunResult<ManualReviewItem[]>>;
    clearManualReview: () => Promise<RunResult<{ cleared: number }>>;
    updateManualReview: (
        itemId: string,
        payload: { status: "pending" | "approved" | "rejected"; approvedText?: string },
        docId?: string,
    ) => Promise<RunResult<ManualReviewItem>>;
    runScan: (request: ScanRequest) => Promise<RunResult<ScanResponse>>;
    uploadDocument: (file: File) => Promise<RunResult<UploadResponse>>;
    runDocumentScan: (docId: string) => Promise<RunResult<{ jobId: string }>>;
    fetchDocumentIssues: (docId: string) => Promise<RunResult<DocumentIssue[]>>;
    fetchDocumentDiff: (docId: string) => Promise<RunResult<DocumentDiffResponse>>;
    fetchDocumentSummary: (docId: string) => Promise<RunResult<DocumentSummary>>;
    fetchTagTree: (docId: string) => Promise<RunResult<TagTreeResponse>>;
    fetchFixReport: (docId: string) => Promise<RunResult<FixReport>>;
    fetchPolicies: () => Promise<RunResult<PolicySummary[]>>;
    setSelectedPolicy: (policyId: string | null) => void;
    fetchPolicyDetail: (policyId: string) => Promise<RunResult<PolicyDetail>>;
    applySelectedPolicyToJob: (jobId: string) => Promise<RunResult<{ jobId: string; policy: Record<string, unknown> }>>;
    fetchJobScores: (jobId: string) => Promise<RunResult<JobScorePass[]>>;
    exportEvidenceBundle: (
        jobId: string,
        docId: string,
        options?: EvidenceBundleCreateOptions,
    ) => Promise<RunResult<EvidenceBundleCreateResponse>>;
    fetchEvidenceBundles: (docId: string) => Promise<RunResult<EvidenceBundleSummary[]>>;
}

const resolved = getBackendUrlInfo();
const defaultBaseUrl = resolved.url;
const baseUrlKey = "apiBaseUrl";
const selectedPolicyKey = "selectedPolicyId";
const freeScansUsedKey = "freeScansUsed";
const bypassFreeScanGateKey = "bypassFreeScanGate";
const autoFixPolicyKey = "autoFixPolicy";

const emptyTagTree: TagTreeResponse = {
    tagged: false,
    warnings: ["Tag tree unavailable."],
    summary: {
        nodeCount: 0,
        tagCounts: {},
        figures: 0,
        figuresMissingAlt: 0,
        headings: {},
        tables: {},
    },
    tree: { rootId: "0", nodes: {} },
};

function readStoredBaseUrl(): string | null {
    if (Platform.OS !== "web") return null;
    try {
        return window.localStorage.getItem(baseUrlKey);
    } catch (error) {
        return null;
    }
}

function storeBaseUrl(value: string) {
    if (Platform.OS !== "web") return;
    try {
        window.localStorage.setItem(baseUrlKey, value);
    } catch (error) {
        return;
    }
}

function readStoredSelectedPolicyId(): string | null {
    if (Platform.OS !== "web") return null;
    try {
        return window.localStorage.getItem(selectedPolicyKey);
    } catch (error) {
        return null;
    }
}

function storeSelectedPolicyId(value: string | null) {
    if (Platform.OS !== "web") return;
    try {
        if (!value) {
            window.localStorage.removeItem(selectedPolicyKey);
            return;
        }
        window.localStorage.setItem(selectedPolicyKey, value);
    } catch (error) {
        return;
    }
}

function readStoredFreeScansUsed(): number {
    if (Platform.OS !== "web") return 0;
    try {
        const raw = window.localStorage.getItem(freeScansUsedKey);
        if (!raw) return 0;
        const parsed = parseInt(raw, 10);
        return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
    } catch (error) {
        return 0;
    }
}

function storeFreeScansUsed(value: number) {
    if (Platform.OS !== "web") return;
    try {
        window.localStorage.setItem(freeScansUsedKey, String(value));
    } catch (error) {
        return;
    }
}

function readStoredBypassFreeScanGate(): boolean {
    if (Platform.OS !== "web") return false;
    try {
        return window.localStorage.getItem(bypassFreeScanGateKey) === "true";
    } catch (error) {
        return false;
    }
}

function storeBypassFreeScanGate(value: boolean) {
    if (Platform.OS !== "web") return;
    try {
        window.localStorage.setItem(bypassFreeScanGateKey, value ? "true" : "false");
    } catch (error) {
        return;
    }
}

function readStoredAutoFixPolicy(): "conservative" | "balanced" | "aggressive" {
    if (Platform.OS !== "web") return "balanced";
    try {
        const raw = window.localStorage.getItem(autoFixPolicyKey);
        if (raw === "conservative" || raw === "balanced" || raw === "aggressive") {
            return raw;
        }
        return "balanced";
    } catch (error) {
        return "balanced";
    }
}

function storeAutoFixPolicy(value: "conservative" | "balanced" | "aggressive") {
    if (Platform.OS !== "web") return;
    try {
        window.localStorage.setItem(autoFixPolicyKey, value);
    } catch (error) {
        return;
    }
}

async function probeBackend(
    baseUrl: string,
): Promise<{ ok: boolean; message?: string; resolvedUrl?: string; maxUploadMb?: number }> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 1500);
    try {
        const response = await fetch(`${baseUrl}/healthz`, { signal: controller.signal });
        clearTimeout(timeout);
        if (response.ok) {
            // The backend advertises its upload cap here so the UI can state
            // it up front rather than letting the user find out via a 413.
            let maxUploadMb: number | undefined;
            try {
                const body = await response.json();
                if (typeof body?.maxUploadMb === "number") maxUploadMb = body.maxUploadMb;
            } catch {
                /* older backends return no body worth reading */
            }
            return { ok: true, resolvedUrl: baseUrl, maxUploadMb };
        }
        return { ok: false, message: "Backend responded but health check failed." };
    } catch (error) {
        clearTimeout(timeout);
        // Customer-safe copy: on a managed build (env-baked API URL) this is a
        // service interruption, not something the user can fix locally.
        const message =
            resolved.source === "env"
                ? "We're having trouble reaching the service. It usually recovers in a moment — please retry."
                : "Backend is unreachable. Check that it is running and that the URL in Settings is correct.";
        return { ok: false, message };
    }
}

function normalizeLocalhost(baseUrl: string): string | null {
    if (!baseUrl.includes("localhost")) return null;
    try {
        const url = new URL(baseUrl);
        url.hostname = "127.0.0.1";
        return url.toString().replace(/\/$/, "");
    } catch (error) {
        return null;
    }
}

function getClient(state: AppState): ApiClient {
    return createApiClient({ baseUrl: state.apiBaseUrl, mockMode: state.mockMode });
}

export const useAppStore = create<AppState>((set, get) => ({
    // On managed builds the env-baked API URL always wins: a stale URL saved
    // in localStorage (e.g. from a dev session or an old deploy) would
    // otherwise strand the session on a dead backend with no recovery hint.
    apiBaseUrl: resolved.source === "env" ? defaultBaseUrl : readStoredBaseUrl() ?? defaultBaseUrl,
    backendUrlSource: resolved.source,
    backendUrlWarning: resolved.warning ?? null,
    backendHealth: "unknown",
    backendHealthMessage: null,
    maxUploadMb: null,
    themeMode: "system",
    mockMode: false,
    selectedDocument: null,
    scanResults: null,
    manualReviewQueue: [],
    manualReviewLastFetched: null,
    uploadedDocument: null,
    scanJob: null,
    documentIssues: [],
    fixedDocId: null,
    documentSummary: null,
    tagTree: null,
    fixReport: null,
    policies: { items: [], loaded: false },
    policyDetailsById: {},
    selectedPolicyId: readStoredSelectedPolicyId(),
    jobScoresByJobId: {},
    evidenceBundlesByDocId: {},
    isExportingBundle: false,
    exportError: undefined,
    isScanning: false,
    isUploading: false,
    freeScansUsed: readStoredFreeScansUsed(),
    bypassFreeScanGate: readStoredBypassFreeScanGate(),
    autoFixPolicy: readStoredAutoFixPolicy(),
    setFreeScansUsed: (value) => {
        const safe = Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0;
        storeFreeScansUsed(safe);
        set({ freeScansUsed: safe });
    },
    setBypassFreeScanGate: (value) => {
        storeBypassFreeScanGate(value);
        set({ bypassFreeScanGate: value });
    },
    setAutoFixPolicy: (value) => {
        storeAutoFixPolicy(value);
        set({ autoFixPolicy: value });
    },
    setApiBaseUrl: (value) => {
        storeBaseUrl(value);
        set({ apiBaseUrl: value });
    },
    saveApiBaseUrl: async (value) => {
        storeBaseUrl(value);
        set({ apiBaseUrl: value });
        const primary = await probeBackend(value);
        if (!primary.ok) {
            const normalized = normalizeLocalhost(value);
            if (normalized) {
                const fallback = await probeBackend(normalized);
                if (fallback.ok) {
                    storeBaseUrl(normalized);
                    set({
                        apiBaseUrl: normalized,
                        backendHealth: "ok",
                        backendHealthMessage: null,
                        backendUrlWarning: null,
                        maxUploadMb: fallback.maxUploadMb ?? null,
                    });
                    return;
                }
            }
        }
        set({
            backendHealth: primary.ok ? "ok" : "error",
            backendHealthMessage: primary.ok ? null : primary.message ?? "Backend is unreachable.",
            backendUrlWarning: primary.ok ? null : primary.message ?? "Backend is unreachable.",
            maxUploadMb: primary.ok ? primary.maxUploadMb ?? null : null,
        });
    },
    setBackendUrlInfo: (url, source, warning) =>
        set({ apiBaseUrl: url, backendUrlSource: source, backendUrlWarning: warning ?? null }),
    refreshBackendUrl: async () => {
        const state = get();
        if (state.mockMode) {
            set({ backendHealth: "unknown", backendHealthMessage: null });
            return;
        }
        const info = await fetchBackendUrlInfo();
        set({
            apiBaseUrl: info.url,
            backendUrlSource: info.source,
        });
        const primary = await probeBackend(info.url);
        if (!primary.ok) {
            const normalized = normalizeLocalhost(info.url);
            if (normalized) {
                const fallback = await probeBackend(normalized);
                if (fallback.ok) {
                    storeBaseUrl(normalized);
                    set({
                        apiBaseUrl: normalized,
                        backendHealth: "ok",
                        backendHealthMessage: null,
                        backendUrlWarning: null,
                        maxUploadMb: fallback.maxUploadMb ?? null,
                    });
                    return;
                }
            }
        }
        set({
            backendHealth: primary.ok ? "ok" : "error",
            backendHealthMessage: primary.ok ? null : primary.message ?? "Backend is unreachable.",
            backendUrlWarning: primary.ok ? null : info.warning ?? primary.message ?? "Backend is unreachable.",
            maxUploadMb: primary.ok ? primary.maxUploadMb ?? null : null,
        });
    },
    setMockMode: (value) => set({ mockMode: value }),
    setThemeMode: (value) => set({ themeMode: value }),
    setSelectedDocument: (doc) => set({ selectedDocument: doc }),
    setScanResults: (results) => set({ scanResults: results }),
    addManualReviewItem: (item) =>
        set((state) => ({ manualReviewQueue: [item, ...state.manualReviewQueue] })),
    clearManualReviewQueue: () => set({ manualReviewQueue: [] }),
    fetchManualReview: async (docId) => {
        const client = getClient(get());
        try {
            const items = await client.manualReview(docId);
            set({ manualReviewQueue: items, manualReviewLastFetched: new Date().toISOString() });
            return { ok: true, data: items };
        } catch (error) {
            return { ok: false, error: (error as Error).message };
        }
    },
    clearManualReview: async () => {
        const client = getClient(get());
        try {
            const result = await client.clearManualReview();
            set({ manualReviewQueue: [] });
            return { ok: true, data: result };
        } catch (error) {
            return { ok: false, error: (error as Error).message };
        }
    },
    updateManualReview: async (itemId, payload, docId) => {
        const client = getClient(get());
        try {
            const item = await client.updateManualReview(itemId, payload);
            const refreshed = await client.manualReview(docId);
            set({
                manualReviewQueue: refreshed,
                manualReviewLastFetched: new Date().toISOString(),
            });
            return { ok: true, data: item };
        } catch (error) {
            return { ok: false, error: (error as Error).message };
        }
    },
    runScan: async (request) => {
        const client = getClient(get());
        set({ isScanning: true });
        try {
            const response = await client.scan(request);
            set({ scanResults: response, isScanning: false });
            return { ok: true, data: response };
        } catch (error) {
            set({ isScanning: false });
            return { ok: false, error: (error as Error).message };
        }
    },
    uploadDocument: async (file) => {
        const client = getClient(get());
        set({ isUploading: true });
        try {
            const response = await client.uploadDocument(file);
            set({
                uploadedDocument: response,
                scanJob: null,
                documentIssues: [],
                fixedDocId: null,
                documentSummary: null,
                tagTree: null,
                jobScoresByJobId: {},
                evidenceBundlesByDocId: {},
                isUploading: false,
            });
            return { ok: true, data: response };
        } catch (error) {
            set({ isUploading: false });
            return { ok: false, error: (error as Error).message };
        }
    },
    runDocumentScan: async (docId) => {
        const client = getClient(get());
        try {
            const start = await client.startDocumentScan(docId);
            const isTerminalStatus = (status: string) =>
                status === "done" || status === "completed" || status === "error" || status === "failed";
            const isSuccessStatus = (status: string) => status === "done" || status === "completed";
            const selectedPolicyId = get().selectedPolicyId;
            if (selectedPolicyId) {
                try {
                    await client.setJobPolicy(start.jobId, selectedPolicyId);
                } catch (error) {
                    // If job already left queued state, keep scan flow alive.
                }
            }
            set({
                scanJob: { jobId: start.jobId, status: "queued", progress: 0 },
                documentIssues: [],
                fixedDocId: null,
                documentSummary: null,
                tagTree: null,
                fixReport: null,
            });
            const poll = async (): Promise<void> => {
                let current: { jobId: string; status: string; progress: number; message?: string } | null = null;
                try {
                    current = await client.getJob(start.jobId);
                    set({
                        scanJob: {
                            jobId: current.jobId,
                            status: current.status,
                            progress: current.progress,
                            message: current.message,
                        },
                    });
                    const issues = await client.getIssues(docId);
                    set({ documentIssues: issues });
                } catch (error) {
                    const message = (error as Error).message || "Job not found";
                    set({
                        scanJob: {
                            jobId: start.jobId,
                            status: "error",
                            progress: 0,
                            message,
                        },
                    });
                    return;
                }
                if (!current) {
                    return;
                }
                if (isSuccessStatus(current.status)) {
                    try {
                        const summary = await client.getDocumentSummary(docId);
                        set({ documentSummary: summary });
                    } catch (error) {
                        return;
                    }
                    try {
                        const tagTree = await client.getTagTree(docId);
                        set({ tagTree });
                    } catch (error) {
                        set({ tagTree: emptyTagTree });
                    }
                    try {
                        const report = await client.getFixReport(docId);
                        set({ fixReport: report });
                    } catch (error) {
                        return;
                    }
                    try {
                        const scorePayload = await client.getJobScore(start.jobId);
                        set((state) => ({
                            jobScoresByJobId: {
                                ...state.jobScoresByJobId,
                                [start.jobId]: scorePayload.scores ?? [],
                            },
                        }));
                    } catch (error) {
                        // keep UX non-blocking if score is unavailable
                    }
                }
                if (isTerminalStatus(current.status)) {
                    return;
                }
                setTimeout(poll, 1000);
            };
            void poll();
            return { ok: true, data: start };
        } catch (error) {
            return { ok: false, error: (error as Error).message };
        }
    },
    fetchDocumentIssues: async (docId) => {
        const client = getClient(get());
        try {
            const issues = await client.getIssues(docId);
            set({ documentIssues: issues });
            return { ok: true, data: issues };
        } catch (error) {
            return { ok: false, error: (error as Error).message };
        }
    },
    fetchDocumentDiff: async (docId) => {
        const client = getClient(get());
        try {
            const response = await client.getDocumentDiff(docId);
            return { ok: true, data: response };
        } catch (error) {
            return { ok: false, error: (error as Error).message };
        }
    },
    fetchDocumentSummary: async (docId) => {
        const client = getClient(get());
        try {
            const response = await client.getDocumentSummary(docId);
            set({ documentSummary: response });
            return { ok: true, data: response };
        } catch (error) {
            return { ok: false, error: (error as Error).message };
        }
    },
    fetchTagTree: async (docId) => {
        const client = getClient(get());
        try {
            const response = await client.getTagTree(docId);
            set({ tagTree: response });
            return { ok: true, data: response };
        } catch (error) {
            set({ tagTree: emptyTagTree });
            return { ok: false, error: (error as Error).message };
        }
    },
    fetchFixReport: async (docId) => {
        const client = getClient(get());
        try {
            const response = await client.getFixReport(docId);
            set({ fixReport: response });
            return { ok: true, data: response };
        } catch (error) {
            const message = (error as Error).message || "";
            if (message.includes("Fix report not found") || message.includes("404")) {
                set({ fixReport: null });
                return { ok: true };
            }
            return { ok: false, error: message };
        }
    },
    fetchPolicies: async () => {
        const client = getClient(get());
        try {
            const items = await client.listPolicies();
            const currentSelected = get().selectedPolicyId;
            let nextSelected = currentSelected;
            if (!nextSelected || !items.some((item) => item.id === nextSelected)) {
                nextSelected = items.length > 0 ? items[0].id : null;
                storeSelectedPolicyId(nextSelected);
            }
            set({
                policies: { items, loaded: true },
                selectedPolicyId: nextSelected,
            });
            return { ok: true, data: items };
        } catch (error) {
            const message = (error as Error).message;
            set({ policies: { items: [], loaded: true, error: message } });
            return { ok: false, error: message };
        }
    },
    setSelectedPolicy: (policyId) => {
        storeSelectedPolicyId(policyId);
        set({ selectedPolicyId: policyId });
    },
    fetchPolicyDetail: async (policyId) => {
        const client = getClient(get());
        try {
            const detail = await client.getPolicy(policyId);
            set((state) => ({
                policyDetailsById: {
                    ...state.policyDetailsById,
                    [policyId]: detail,
                },
            }));
            return { ok: true, data: detail };
        } catch (error) {
            return { ok: false, error: (error as Error).message };
        }
    },
    applySelectedPolicyToJob: async (jobId) => {
        const selectedPolicyId = get().selectedPolicyId;
        if (!selectedPolicyId) {
            return { ok: false, error: "No selected policy." };
        }
        const client = getClient(get());
        try {
            const payload = await client.setJobPolicy(jobId, selectedPolicyId);
            return { ok: true, data: payload };
        } catch (error) {
            return { ok: false, error: (error as Error).message };
        }
    },
    fetchJobScores: async (jobId) => {
        const client = getClient(get());
        try {
            const payload = await client.getJobScore(jobId);
            set((state) => ({
                jobScoresByJobId: {
                    ...state.jobScoresByJobId,
                    [jobId]: payload.scores ?? [],
                },
            }));
            return { ok: true, data: payload.scores ?? [] };
        } catch (error) {
            return { ok: false, error: (error as Error).message };
        }
    },
    exportEvidenceBundle: async (jobId, docId, options) => {
        const client = getClient(get());
        set({ isExportingBundle: true, exportError: undefined });
        try {
            const payload = await client.createEvidenceBundle(jobId, options);
            const bundles = await client.listEvidenceBundlesForDoc(docId);
            set((state) => ({
                isExportingBundle: false,
                evidenceBundlesByDocId: {
                    ...state.evidenceBundlesByDocId,
                    [docId]: bundles,
                },
            }));
            return { ok: true, data: payload };
        } catch (error) {
            const message = (error as Error).message;
            set({ isExportingBundle: false, exportError: message });
            return { ok: false, error: message };
        }
    },
    fetchEvidenceBundles: async (docId) => {
        const client = getClient(get());
        try {
            const bundles = await client.listEvidenceBundlesForDoc(docId);
            set((state) => ({
                evidenceBundlesByDocId: {
                    ...state.evidenceBundlesByDocId,
                    [docId]: bundles,
                },
            }));
            return { ok: true, data: bundles };
        } catch (error) {
            return { ok: false, error: (error as Error).message };
        }
    },
}));
