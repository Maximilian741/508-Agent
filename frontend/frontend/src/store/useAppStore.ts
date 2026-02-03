import { create } from "zustand";

import {
    ApiClient,
    ApplyFixesResponse,
    DocumentDiffResponse,
    DocumentSummary,
    DocumentIssue,
    ExecutionResult,
    FixReport,
    Issue,
    ManualReviewItem,
    RemediateRequest,
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
    isScanning: boolean;
    isUploading: boolean;
    setApiBaseUrl: (value: string) => void;
    saveApiBaseUrl: (value: string) => Promise<void>;
    setBackendUrlInfo: (url: string, source: BackendUrlSource, warning?: string) => void;
    refreshBackendUrl: () => Promise<void>;
    setMockMode: (value: boolean) => void;
    setSelectedDocument: (doc: DocumentPayload) => void;
    setScanResults: (results: ScanResponse) => void;
    addManualReviewItem: (item: ManualReviewItem) => void;
    clearManualReviewQueue: () => void;
    fetchManualReview: () => Promise<RunResult<ManualReviewItem[]>>;
    clearManualReview: () => Promise<RunResult<{ cleared: number }>>;
    runScan: (request: ScanRequest) => Promise<RunResult<ScanResponse>>;
    runRemediate: (request: RemediateRequest) => Promise<RunResult<ExecutionResult[]>>;
    uploadDocument: (file: File) => Promise<RunResult<UploadResponse>>;
    runDocumentScan: (docId: string) => Promise<RunResult<{ jobId: string }>>;
    applyDocumentFixes: (docId: string) => Promise<RunResult<ApplyFixesResponse>>;
    fetchDocumentDiff: (docId: string) => Promise<RunResult<DocumentDiffResponse>>;
    fetchDocumentSummary: (docId: string) => Promise<RunResult<DocumentSummary>>;
    fetchTagTree: (docId: string) => Promise<RunResult<TagTreeResponse>>;
    fetchFixReport: (docId: string) => Promise<RunResult<FixReport>>;
}

const resolved = getBackendUrlInfo();
const defaultBaseUrl = resolved.url;
const baseUrlKey = "apiBaseUrl";

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

async function probeBackend(baseUrl: string): Promise<{ ok: boolean; message?: string; resolvedUrl?: string }> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 1500);
    try {
        const response = await fetch(`${baseUrl}/healthz`, { signal: controller.signal });
        clearTimeout(timeout);
        if (response.ok) {
            return { ok: true, resolvedUrl: baseUrl };
        }
        return { ok: false, message: "Backend responded but health check failed." };
    } catch (error) {
        clearTimeout(timeout);
        return { ok: false, message: "Backend is unreachable. Check the port and restart using dev_run.py." };
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
    apiBaseUrl: readStoredBaseUrl() ?? defaultBaseUrl,
    backendUrlSource: resolved.source,
    backendUrlWarning: resolved.warning ?? null,
    backendHealth: "unknown",
    backendHealthMessage: null,
    mockMode: true,
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
    isScanning: false,
    isUploading: false,
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
                    });
                    return;
                }
            }
        }
        set({
            backendHealth: primary.ok ? "ok" : "error",
            backendHealthMessage: primary.ok ? null : primary.message ?? "Backend is unreachable.",
            backendUrlWarning: primary.ok ? null : primary.message ?? "Backend is unreachable.",
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
                    });
                    return;
                }
            }
        }
        set({
            backendHealth: primary.ok ? "ok" : "error",
            backendHealthMessage: primary.ok ? null : primary.message ?? "Backend is unreachable.",
            backendUrlWarning: primary.ok ? null : info.warning ?? primary.message ?? "Backend is unreachable.",
        });
    },
    setMockMode: (value) => set({ mockMode: value }),
    setSelectedDocument: (doc) => set({ selectedDocument: doc }),
    setScanResults: (results) => set({ scanResults: results }),
    addManualReviewItem: (item) =>
        set((state) => ({ manualReviewQueue: [item, ...state.manualReviewQueue] })),
    clearManualReviewQueue: () => set({ manualReviewQueue: [] }),
    fetchManualReview: async () => {
        const client = getClient(get());
        try {
            const items = await client.manualReview();
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
    runRemediate: async (request) => {
        const client = getClient(get());
        try {
            const response = await client.remediate(request);
            return { ok: true, results: response.results };
        } catch (error) {
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
            set({
                scanJob: { jobId: start.jobId, status: "queued", progress: 0 },
                documentIssues: [],
                fixedDocId: null,
                documentSummary: null,
                tagTree: null,
                fixReport: null,
            });
            const poll = async (): Promise<void> => {
                const current = await client.getJob(start.jobId);
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
                if (current.status === "done") {
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
                }
                if (current.status === "done") {
                    return;
                }
                if (current.status === "error") {
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
    applyDocumentFixes: async (docId) => {
        const client = getClient(get());
        try {
            const response = await client.applyFixes(docId);
            const resolvedFixedDocId = response.fixedDocId ?? response.report?.fixedDocId ?? response.docId;
            set({ fixedDocId: resolvedFixedDocId, fixReport: response.report ?? null });
            if (response.report?.after?.issueCount !== undefined) {
                set({ documentIssues: response.report.delta?.remaining ?? [] });
            }
            return { ok: true, data: response };
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
            return { ok: false, error: (error as Error).message };
        }
    },
}));
