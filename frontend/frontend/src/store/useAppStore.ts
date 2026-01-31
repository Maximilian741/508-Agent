import { create } from "zustand";

import {
    ApiClient,
    ExecutionResult,
    Issue,
    ManualReviewItem,
    RemediateRequest,
    ScanRequest,
    ScanResponse,
    createApiClient,
} from "../api/client";

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
    mockMode: boolean;
    selectedDocument: DocumentPayload | null;
    scanResults: ScanResponse | null;
    manualReviewQueue: ManualReviewItem[];
    isScanning: boolean;
    setApiBaseUrl: (value: string) => void;
    setMockMode: (value: boolean) => void;
    setSelectedDocument: (doc: DocumentPayload) => void;
    setScanResults: (results: ScanResponse) => void;
    addManualReviewItem: (item: ManualReviewItem) => void;
    clearManualReviewQueue: () => void;
    fetchManualReview: () => Promise<RunResult<ManualReviewItem[]>>;
    clearManualReview: () => Promise<RunResult<{ cleared: number }>>;
    runScan: (request: ScanRequest) => Promise<RunResult<ScanResponse>>;
    runRemediate: (request: RemediateRequest) => Promise<RunResult<ExecutionResult[]>>;
}

const defaultBaseUrl = "http://localhost:8000";

function getClient(state: AppState): ApiClient {
    return createApiClient({ baseUrl: state.apiBaseUrl, mockMode: state.mockMode });
}

export const useAppStore = create<AppState>((set, get) => ({
    apiBaseUrl: defaultBaseUrl,
    mockMode: true,
    selectedDocument: null,
    scanResults: null,
    manualReviewQueue: [],
    isScanning: false,
    setApiBaseUrl: (value) => set({ apiBaseUrl: value }),
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
            set({ manualReviewQueue: items });
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
}));
