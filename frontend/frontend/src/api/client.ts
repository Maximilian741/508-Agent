export type Severity = "error" | "warning" | "info";

export interface ScanRequest {
    documentId: string;
    sourceFormat: string;
    content: string;
}

export interface RemediationAction {
    actionCode: string;
    description: string;
    requiresAi: boolean;
    requiresHumanReview: boolean;
    isAutoApplicable: boolean;
    supportedNodeTypes: string[];
    relatedFlagCode: string;
}

export interface Issue {
    id: string;
    ruleId: string;
    severity: Severity;
    description: string;
    nodeId: string;
    nodePath?: string[];
    evidence?: Record<string, unknown>;
    recommendedActions: RemediationAction[];
}

export interface ScanResponse {
    scanId: string;
    documentId: string;
    issues: Issue[];
}

export interface RemediateRequest {
    issueId?: string;
    targetNodeId: string;
    actionCode: string;
}

export interface ExecutionResult {
    actionCode: string;
    targetNodeId: string;
    status: "success" | "skipped" | "not_implemented" | "ready";
    notes: string;
}

export interface RemediateResponse {
    results: ExecutionResult[];
}

export interface ManualReviewItem {
    id: string;
    issueId: string;
    targetNodeId: string;
    reason: string;
    notes?: string;
    createdAt?: string;
    pages?: number[];
    anchors?: string[];
    instructions?: string;
    suggestedFix?: string;
    confidence?: number;
    requiresHuman?: boolean;
}

export interface UploadResponse {
    docId: string;
    filename: string;
    sizeBytes: number;
}

export interface ScanJobResponse {
    jobId: string;
    status: "queued" | "running" | "done" | "error";
    progress: number;
    message?: string;
}

export interface DocumentIssue {
    id: string;
    ruleId: string;
    title: string;
    severity: Severity;
    description: string;
    locationHint: string;
    recommendation: string;
    evidence?: Record<string, unknown>;
}

export interface ApplyFixesResponse {
    docId: string;
    fixedDocId?: string;
    fixedPath?: string;
    rebuiltDocId?: string;
    rebuiltPath?: string;
    fixed: boolean;
    report?: FixReport;
}

export interface FixReportItem {
    fixId: string;
    ruleId: string;
    severity: Severity;
    action: string;
    pages: number[];
    anchors: string[];
    deterministic: boolean;
}

export interface FixReport {
    docId: string;
    fixedDocId: string;
    fixedPath?: string;
    rebuiltDocId?: string | null;
    rebuiltPath?: string | null;
    fixedExists?: boolean;
    rebuiltExists?: boolean;
    fixedSize?: number;
    rebuiltSize?: number;
    scanTargetPath?: string;
    appliedFixes: FixReportItem[];
    before: {
        issueCount: number;
        bySeverity: Record<string, number>;
        byRuleId: Record<string, number>;
    };
    after: {
        issueCount: number;
        bySeverity: Record<string, number>;
        byRuleId: Record<string, number>;
    };
    delta: {
        fixed: DocumentIssue[];
        remaining: DocumentIssue[];
        introduced: DocumentIssue[];
    };
    manualReview: ManualReviewItem[];
    beforeAfter: {
        metadata_before: Record<string, string | null>;
        metadata_after: Record<string, string | null>;
    };
    deterministic: boolean;
    mode: string;
    rebuilt: boolean;
}

export interface DocumentDiffResponse {
    beforeText: string;
    afterText: string;
    diffText: string;
}


export interface TagTreeNode {
    id: string;
    role: string;
    tag: string | null;
    title: string | null;
    alt: string | null;
    actualText: string | null;
    lang: string | null;
    kids: string[];
    mcid?: number;
    pg?: string | null;
}

export interface TagTreeSummary {
    nodeCount: number;
    tagCounts: Record<string, number>;
    figures: number;
    figuresMissingAlt: number;
    headings: Record<string, number>;
    tables: Record<string, number>;
}

export interface TagTreeResponse {
    tagged: boolean;
    warnings: string[];
    summary: TagTreeSummary;
    tree: {
        rootId: string;
        nodes: Record<string, TagTreeNode>;
    };
}

export interface DocumentSummary {
    docId: string;
    title: string;
    pages: number;
    images: number;
    tagged: boolean;
    tagCounts: Record<string, number>;
    figures: number;
    figuresMissingAlt: number;
    nodeCount: number;
    outlineCount: number;
    formFields: number;
    unlabeledFields: number;
}

export interface ApiClient {
    scan: (payload: ScanRequest) => Promise<ScanResponse>;
    remediate: (payload: RemediateRequest) => Promise<RemediateResponse>;
    manualReview: () => Promise<ManualReviewItem[]>;
    clearManualReview: () => Promise<{ cleared: number }>;
    uploadDocument: (file: File) => Promise<UploadResponse>;
    startDocumentScan: (docId: string) => Promise<{ jobId: string }>;
    getJob: (jobId: string) => Promise<ScanJobResponse>;
    getIssues: (docId: string) => Promise<DocumentIssue[]>;
    applyFixes: (docId: string) => Promise<ApplyFixesResponse>;
    getDownloadUrl: (docId: string, variant: "original" | "fixed") => string;
    getDocumentDiff: (docId: string) => Promise<DocumentDiffResponse>;
    getDocumentSummary: (docId: string) => Promise<DocumentSummary>;
    getTagTree: (docId: string) => Promise<TagTreeResponse>;
    getFixReport: (docId: string) => Promise<FixReport>;
    getRebuiltUrl: (docId: string) => string;
}

interface ApiClientConfig {
    baseUrl: string;
    mockMode: boolean;
}

const mockActions: RemediationAction[] = [
    {
        actionCode: "SET_DOCUMENT_TITLE",
        description: "Set document title metadata.",
        requiresAi: false,
        requiresHumanReview: true,
        isAutoApplicable: false,
        supportedNodeTypes: ["document"],
        relatedFlagCode: "DOCUMENT_TITLE_MISSING",
    },
];

const mockIssues: Issue[] = [
    {
        id: "issue-1",
        ruleId: "DOCUMENT_TITLE_MISSING",
        severity: "error",
        description: "Document title is missing.",
        nodeId: "doc-1",
        nodePath: ["document"],
        recommendedActions: mockActions,
    },
];

export function createApiClient(config: ApiClientConfig): ApiClient {
    const { baseUrl, mockMode } = config;

    const request = async <T>(path: string, payload: unknown): Promise<T> => {
        const response = await fetch(`${baseUrl}${path}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            const message = await response.text();
            throw new Error(message || "Request failed");
        }
        return (await response.json()) as T;
    };

    const scan = async (payload: ScanRequest): Promise<ScanResponse> => {
        if (mockMode) {
            return {
                scanId: `scan-${Date.now()}`,
                documentId: payload.documentId,
                issues: mockIssues,
            };
        }
        console.log("[api] POST /scan", payload);
        return request<ScanResponse>("/scan", payload);
    };

    const remediate = async (payload: RemediateRequest): Promise<RemediateResponse> => {
        if (mockMode) {
            return {
                results: [
                    {
                        actionCode: payload.actionCode,
                        targetNodeId: payload.targetNodeId,
                        status: payload.actionCode === "SET_DOCUMENT_TITLE" ? "success" : "not_implemented",
                        notes: payload.actionCode === "SET_DOCUMENT_TITLE"
                            ? "Set document title from None to 'Untitled Document'."
                            : "Manual review required; queued for human review.",
                    },
                ],
            };
        }
        console.log("[api] POST /remediate", payload);
        return request<RemediateResponse>("/remediate", payload);
    };

    const manualReview = async (): Promise<ManualReviewItem[]> => {
        if (mockMode) {
            return [];
        }
        console.log("[api] GET /manual-review");
        const response = await fetch(`${baseUrl}/manual-review`);
        if (!response.ok) {
            return [];
        }
        return (await response.json()) as ManualReviewItem[];
    };

    const clearManualReview = async (): Promise<{ cleared: number }> => {
        if (mockMode) {
            return { cleared: 0 };
        }
        console.log("[api] DELETE /manual-review");
        const response = await fetch(`${baseUrl}/manual-review`, { method: "DELETE" });
        if (!response.ok) {
            return { cleared: 0 };
        }
        return (await response.json()) as { cleared: number };
    };

    const uploadDocument = async (file: File): Promise<UploadResponse> => {
        if (mockMode) {
            return { docId: `doc-${Date.now()}`, filename: file.name, sizeBytes: file.size };
        }
        const form = new FormData();
        form.append("file", file);
        const response = await fetch(`${baseUrl}/documents/upload`, {
            method: "POST",
            body: form,
        });
        if (!response.ok) {
            const message = await response.text();
            throw new Error(message || "Upload failed");
        }
        return (await response.json()) as UploadResponse;
    };

    const startDocumentScan = async (docId: string): Promise<{ jobId: string }> => {
        if (mockMode) {
            return { jobId: `job-${Date.now()}` };
        }
        return request<{ jobId: string }>(`/documents/${docId}/scan`, {});
    };

    const getJob = async (jobId: string): Promise<ScanJobResponse> => {
        if (mockMode) {
            return { jobId, status: "done", progress: 100 };
        }
        const response = await fetch(`${baseUrl}/jobs/${jobId}`);
        if (!response.ok) {
            const message = await response.text();
            throw new Error(message || "Job lookup failed");
        }
        return (await response.json()) as ScanJobResponse;
    };

    const getIssues = async (docId: string): Promise<DocumentIssue[]> => {
        if (mockMode) {
            return [];
        }
        const response = await fetch(`${baseUrl}/documents/${docId}/issues`);
        if (!response.ok) {
            const message = await response.text();
            throw new Error(message || "Issue fetch failed");
        }
        return (await response.json()) as DocumentIssue[];
    };

    const applyFixes = async (docId: string): Promise<ApplyFixesResponse> => {
        if (mockMode) {
            return { docId, fixed: true };
        }
        return request<ApplyFixesResponse>(`/documents/${docId}/apply-fixes`, {});
    };

    const getDownloadUrl = (docId: string, variant: "original" | "fixed") => {
        if (variant === "fixed") {
            return `${baseUrl}/documents/${docId}/pdf-fixed`;
        }
        return `${baseUrl}/documents/${docId}/pdf`;
    };

    const getRebuiltUrl = (docId: string) => `${baseUrl}/documents/${docId}/pdf-rebuilt`;

    const getDocumentDiff = async (docId: string): Promise<DocumentDiffResponse> => {
        if (mockMode) {
            return { beforeText: "", afterText: "", diffText: "" };
        }
        const response = await fetch(`${baseUrl}/documents/${docId}/diff`);
        if (!response.ok) {
            const message = await response.text();
            throw new Error(message || "Diff fetch failed");
        }
        return (await response.json()) as DocumentDiffResponse;
    };

    const getDocumentSummary = async (docId: string): Promise<DocumentSummary> => {
        if (mockMode) {
            return {
                docId,
                title: "",
                pages: 0,
                images: 0,
                tagged: false,
                tagCounts: {},
                figures: 0,
                figuresMissingAlt: 0,
                nodeCount: 0,
                outlineCount: 0,
                formFields: 0,
                unlabeledFields: 0,
            };
        }
        const response = await fetch(`${baseUrl}/documents/${docId}/summary`);
        if (!response.ok) {
            const message = await response.text();
            throw new Error(message || "Summary fetch failed");
        }
        return (await response.json()) as DocumentSummary;
    };

    const getTagTree = async (docId: string): Promise<TagTreeResponse> => {
        if (mockMode) {
            return {
                tagged: false,
                warnings: ["Tag tree unavailable in mock mode."],
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
        }
        const response = await fetch(`${baseUrl}/documents/${docId}/tag-tree`);
        if (!response.ok) {
            const message = await response.text();
            throw new Error(message || "Tag tree fetch failed");
        }
        return (await response.json()) as TagTreeResponse;
    };

    const getFixReport = async (docId: string): Promise<FixReport> => {
        if (mockMode) {
            return {
                docId,
                fixedDocId: docId,
                appliedFixes: [],
                before: { issueCount: 0, bySeverity: {}, byRuleId: {} },
                after: { issueCount: 0, bySeverity: {}, byRuleId: {} },
                delta: { fixed: [], remaining: [], introduced: [] },
                manualReview: [],
                beforeAfter: { metadata_before: {}, metadata_after: {} },
                deterministic: true,
                mode: "in_place",
                rebuilt: false,
            };
        }
        const response = await fetch(`${baseUrl}/documents/${docId}/fix-report`);
        if (!response.ok) {
            const message = await response.text();
            throw new Error(message || "Fix report fetch failed");
        }
        return (await response.json()) as FixReport;
    };

    return {
        scan,
        remediate,
        manualReview,
        clearManualReview,
        uploadDocument,
        startDocumentScan,
        getJob,
        getIssues,
        applyFixes,
        getDownloadUrl,
        getDocumentDiff,
        getDocumentSummary,
        getTagTree,
        getFixReport,
        getRebuiltUrl,
    };
}





