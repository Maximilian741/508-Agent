import { loadToken } from "../domain/account";

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
    anchors?: Array<string | Record<string, unknown>>;
    instructions?: string;
    suggestedFix?: string;
    suggestedText?: string;
    approvedText?: string;
    status?: "pending" | "approved" | "rejected" | string;
    docId?: string;
    readyToFinalize?: boolean;
    aiSuggested?: boolean;
    confidence?: number;
    requiresHuman?: boolean;
}

export interface UploadResponse {
    docId: string;
    filename: string;
    sizeBytes: number;
    docType?: "pdf" | "docx" | "pptx";
}

export interface ScanJobResponse {
    jobId: string;
    status: "queued" | "running" | "done" | "error";
    progress: number;
    message?: string;
}

export interface PolicySummary {
    id: string;
    name: string;
    description: string;
    version: number;
    targets: string[];
    updated_at: string;
}

export interface PolicyDetail extends PolicySummary {
    policy_json?: Record<string, unknown>;
}

export interface JobScorePass {
    passType?: string;
    pass_type?: string;
    scoreTotal?: number;
    score_total?: number;
    status: string;
    countsBySeverity?: Record<string, number>;
    counts_by_severity?: Record<string, number>;
    coverage?: Record<string, number>;
    createdAt?: string;
    created_at?: string;
}

export interface JobScoreResponse {
    jobId: string;
    scores: JobScorePass[];
}

export interface EvidenceBundleSummary {
    bundleId: string;
    createdAt: string;
    bundleHash: string;
    options: Record<string, unknown>;
    downloadUrl: string;
}

export interface EvidenceBundleCreateOptions {
    includeOriginal?: boolean;
    includeFixedIfAvailable?: boolean;
    includeRebuiltIfAvailable?: boolean;
    includeRawArtifacts?: boolean;
    includePiiUnsafe?: boolean;
}

export interface EvidenceBundleCreateResponse {
    bundleId: string;
    jobId: string;
    docId: string;
    createdAt: string;
    bundleHash: string;
    downloadUrl: string;
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
    jobId?: string;
}

export interface FinalizeResponse {
    docId: string;
    jobId?: string | null;
    finalized: boolean;
    finalizedPath?: string;
    report?: FixReport;
    counts?: {
        remaining?: number;
        introduced?: number;
        pendingManual?: number;
        approvedManual?: number;
        rejectedManual?: number;
    };
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

export interface PipelineSummary {
    documentId: string;
    sourceFormat: string;
    title?: string | null;
    language?: string | null;
    pageCount: number;
    nodeCount: number;
    imageCount: number;
    tableCount: number;
}

/**
 * How to fix one finding on a surface we can't remediate ourselves (a live page).
 * `source: "writer"` = a REAL diff our engine produced on a throwaway copy.
 * `source: "guidance"` = a hand-written example pattern (never has `before`).
 */
export interface PipelineFix {
    source: "writer" | "guidance";
    kind: "element" | "structural" | "css" | "advice";
    before?: string | null;
    after?: string | null;
    action?: string | null;
    note?: string | null;
    requiresHumanVerification: boolean;
}

export interface PipelineViolation {
    id: string;
    ruleId: string;
    severity: Severity;
    description: string;
    nodeId: string;
    page?: number | null;
    standards: { wcag_2_1: string[]; section_508: string[]; pdf_ua: string[] };
    evidence: Record<string, unknown>;
    recommendedActions: string[];
    /** Only set by the URL/site scan. */
    fix?: PipelineFix | null;
}

export interface PipelineExecutionResult {
    actionCode: string;
    targetNodeId: string;
    status: "success" | "skipped" | "not_implemented" | "ready";
    notes: string;
}

export interface PipelineScore {
    initialIssues: number;
    fixedAutomatically: number;
    pendingManual: number;
    score: number;
    grade: string;
}

/** What changed since this URL was last scanned (only on a re-scan). */
export interface ScanChangeReport {
    previousScanAt?: string | null;
    previousIssueCount: number;
    previousScore: number;
    previousGrade: string;
    newIssues: number;
    resolvedIssues: number;
    unchangedIssues: number;
}

/** A URL the user asked us to re-check on a schedule. */
export interface Monitor {
    id: string;
    url: string;
    frequency: "daily" | "weekly";
    enabled: boolean;
    notifyEmail: string;
    lastRunAt?: string | null;
    nextRunAt?: string | null;
    lastIssueCount: number;
    lastStatus: string;
}

export interface PipelineResponse {
    summary: PipelineSummary;
    violations: PipelineViolation[];
    executions: PipelineExecutionResult[];
    score: PipelineScore;
    aiProvider: string;
    changes?: ScanChangeReport | null;
}

export interface SitePageResult {
    url: string;
    title?: string | null;
    status: "scanned" | "failed";
    note?: string | null;
    issueCount: number;
    errorCount: number;
    warningCount: number;
    score: number;
    grade: string;
}

export interface SiteIssueRollup {
    ruleId: string;
    severity: string;
    totalCount: number;
    pageCount: number;
}

export interface SiteScanResponse {
    seedUrl: string;
    pagesDiscovered: number;
    pagesScanned: number;
    pagesFailed: number;
    totalIssues: number;
    score: number;
    grade: string;
    issues: SiteIssueRollup[];
    pages: SitePageResult[];
}

export interface PipelineRemediateResult {
    jobId: string;
    filename: string;
    downloadUrl: string;
    approved: string[];
    rejected: string[];
    executions: PipelineExecutionResult[];
    writer: {
        applied: Array<{ kind: string; target_id: string; summary: string }>;
        skipped: Array<{ target_id: string; reason: string }>;
    };
    manualReviewItemsCreated: number;
}

export interface ApiClient {
    scan: (payload: ScanRequest) => Promise<ScanResponse>;
    remediate: (payload: RemediateRequest) => Promise<RemediateResponse>;
    runPipeline: (file: File, execute?: boolean) => Promise<PipelineResponse>;
    runPipelineUrl: (url: string) => Promise<PipelineResponse>;
    runSiteScan: (url: string, maxPages?: number) => Promise<SiteScanResponse>;
    listMonitors: () => Promise<Monitor[]>;
    createMonitor: (url: string, frequency: string, notifyEmail: string) => Promise<Monitor>;
    updateMonitor: (id: string, patch: Partial<Pick<Monitor, "enabled" | "frequency" | "notifyEmail">>) => Promise<Monitor>;
    deleteMonitor: (id: string) => Promise<void>;
    runPipelineRemediate: (
        file: File,
        approvedViolationIds: string[],
        rejectedViolationIds: string[],
        token?: string,
        accountId?: string,
    ) => Promise<PipelineRemediateResult>;
    getPipelineFileUrl: (jobId: string, filename: string) => string;
    batchZip: (jobs: Array<{ jobId: string; filename: string }>, token?: string) => Promise<Blob>;
    manualReview: (docId?: string) => Promise<ManualReviewItem[]>;
    clearManualReview: () => Promise<{ cleared: number }>;
    updateManualReview: (itemId: string, payload: { status: "pending" | "approved" | "rejected"; approvedText?: string }) => Promise<ManualReviewItem>;
    uploadDocument: (file: File) => Promise<UploadResponse>;
    startDocumentScan: (docId: string) => Promise<{ jobId: string }>;
    getJob: (jobId: string) => Promise<ScanJobResponse>;
    getIssues: (docId: string) => Promise<DocumentIssue[]>;
    applyFixes: (docId: string) => Promise<ApplyFixesResponse>;
    finalizeDocument: (docId: string) => Promise<FinalizeResponse>;
    getDownloadUrl: (docId: string, variant: "original" | "fixed") => string;
    getDocumentDiff: (docId: string) => Promise<DocumentDiffResponse>;
    getDocumentSummary: (docId: string) => Promise<DocumentSummary>;
    getTagTree: (docId: string) => Promise<TagTreeResponse>;
    getFixReport: (docId: string) => Promise<FixReport>;
    getRebuiltUrl: (docId: string) => string;
    listPolicies: () => Promise<PolicySummary[]>;
    getPolicy: (policyId: string) => Promise<PolicyDetail>;
    setJobPolicy: (jobId: string, policyPackId: string) => Promise<{ jobId: string; policy: Record<string, unknown> }>;
    getJobScore: (jobId: string) => Promise<JobScoreResponse>;
    createEvidenceBundle: (jobId: string, options?: EvidenceBundleCreateOptions) => Promise<EvidenceBundleCreateResponse>;
    listEvidenceBundlesForDoc: (docId: string) => Promise<EvidenceBundleSummary[]>;
    getEvidenceBundleDownloadUrl: (bundleId: string) => string;
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

// Build request headers carrying the stored session JWT (if any). Every
// authenticated backend route requires `Authorization: Bearer <jwt>`.
function authHeaders(extra?: Record<string, string>): Record<string, string> {
    const headers: Record<string, string> = { ...(extra || {}) };
    const token = loadToken();
    if (token) headers["Authorization"] = "Bearer " + token;
    return headers;
}

export function createApiClient(config: ApiClientConfig): ApiClient {
    const { baseUrl, mockMode } = config;

    const request = async <T>(path: string, payload: unknown): Promise<T> => {
        const response = await fetch(`${baseUrl}${path}`, {
            method: "POST",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            const message = await response.text();
            throw new Error(message || "Request failed");
        }
        return (await response.json()) as T;
    };

    const getJson = async <T>(path: string): Promise<T> => {
        const response = await fetch(`${baseUrl}${path}`, { headers: authHeaders() });
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
        if (__DEV__) console.log("[api] POST /scan");
        return request<ScanResponse>("/scan", payload);
    };

    const runPipeline = async (file: File, execute: boolean = true): Promise<PipelineResponse> => {
        if (mockMode) {
            return {
                summary: {
                    documentId: file.name.replace(/\.[^.]+$/, "") || "doc",
                    sourceFormat: (file.name.split(".").pop() || "pdf").toLowerCase(),
                    title: null,
                    language: null,
                    pageCount: 1,
                    nodeCount: 4,
                    imageCount: 1,
                    tableCount: 0,
                },
                violations: [
                    {
                        id: "vio-doc-1-document_title_missing",
                        ruleId: "DOCUMENT_TITLE_MISSING",
                        severity: "warning",
                        description: "Document title is missing.",
                        nodeId: "doc-1",
                        page: null,
                        standards: { wcag_2_1: ["2.4.2"], section_508: ["E207.4"], pdf_ua: ["7.1-2"] },
                        evidence: {},
                        recommendedActions: ["SET_DOCUMENT_TITLE", "FLAG_FOR_MANUAL_REVIEW"],
                    },
                ],
                executions: [
                    {
                        actionCode: "SET_DOCUMENT_TITLE",
                        targetNodeId: "doc-1",
                        status: "success",
                        notes: "Set document title from None to 'Sample Document'.",
                    },
                ],
                score: { initialIssues: 1, fixedAutomatically: 1, pendingManual: 0, score: 95, grade: "A+" },
                aiProvider: "heuristic",
            };
        }
        const form = new FormData();
        form.append("file", file);
        const url = `${baseUrl}/pipeline/analyze${execute ? "" : "?execute=false"}`;
        const response = await fetch(url, { method: "POST", body: form, headers: authHeaders() });
        if (!response.ok) {
            const message = await response.text();
            // Attach the HTTP status (like runPipelineRemediate does) so the UI
            // can branch on 401/402 instead of string-matching raw JSON.
            const err = new Error(message || "Pipeline analyze failed") as Error & { status?: number };
            err.status = response.status;
            throw err;
        }
        return (await response.json()) as PipelineResponse;
    };

    // Free, read-only accessibility scan of a public web page by URL. Server
    // fetches it (SSRF-guarded) and runs the same HTML analyzers as an upload.
    const runPipelineUrl = async (url: string): Promise<PipelineResponse> => {
        if (mockMode) {
            return {
                summary: {
                    documentId: url, sourceFormat: "html", title: "Example Page",
                    language: "en", pageCount: 0, nodeCount: 12, imageCount: 2, tableCount: 1,
                },
                violations: [],
                executions: [],
                score: { initialIssues: 0, fixedAutomatically: 0, pendingManual: 0, score: 100, grade: "A+" },
                aiProvider: "heuristic",
            };
        }
        const response = await fetch(`${baseUrl}/pipeline/analyze-url`, {
            method: "POST",
            body: JSON.stringify({ url }),
            headers: { ...authHeaders(), "Content-Type": "application/json" },
        });
        if (!response.ok) {
            // Parse the structured {detail} defensively — never surface a raw
            // proxy/HTML/stack body to the user.
            const text = await response.text();
            let detail = "";
            try {
                const j = JSON.parse(text);
                detail = typeof j?.detail === "string" ? j.detail : "";
            } catch {
                /* non-JSON error body (e.g. an upstream 502) — fall back to generic */
            }
            const err = new Error(detail || "URL scan failed") as Error & { status?: number };
            err.status = response.status;
            throw err;
        }
        return (await response.json()) as PipelineResponse;
    };

    // Free, read-only accessibility scan of a whole public SITE (sitemap-discovered,
    // same-origin only). Server-side fetching is SSRF-guarded and page-capped.
    const runSiteScan = async (url: string, maxPages: number = 10): Promise<SiteScanResponse> => {
        if (mockMode) {
            return {
                seedUrl: url, pagesDiscovered: 3, pagesScanned: 3, pagesFailed: 0,
                totalIssues: 4, score: 88, grade: "B",
                issues: [{ ruleId: "MISSING_ALT_TEXT", severity: "error", totalCount: 3, pageCount: 2 }],
                pages: [
                    { url, title: "Home", status: "scanned", issueCount: 2, errorCount: 2, warningCount: 0, score: 88, grade: "B" },
                ],
            };
        }
        const response = await fetch(`${baseUrl}/pipeline/scan-site`, {
            method: "POST",
            body: JSON.stringify({ url, maxPages }),
            headers: { ...authHeaders(), "Content-Type": "application/json" },
        });
        if (!response.ok) {
            const text = await response.text();
            let detail = "";
            try {
                const j = JSON.parse(text);
                detail = typeof j?.detail === "string" ? j.detail : "";
            } catch {
                /* non-JSON error body — fall back to generic */
            }
            const err = new Error(detail || "Site scan failed") as Error & { status?: number };
            err.status = response.status;
            throw err;
        }
        return (await response.json()) as SiteScanResponse;
    };

    // --- Monitored sites (scheduled re-scans + regression alerts) ---
    const _monitorErr = async (response: Response, fallback: string) => {
        const text = await response.text();
        let detail = "";
        try {
            const j = JSON.parse(text);
            detail = typeof j?.detail === "string" ? j.detail : "";
        } catch {
            /* non-JSON body — use the generic fallback */
        }
        const err = new Error(detail || fallback) as Error & { status?: number };
        err.status = response.status;
        return err;
    };

    const listMonitors = async (): Promise<Monitor[]> => {
        if (mockMode) return [];
        const response = await fetch(`${baseUrl}/monitors`, { headers: authHeaders() });
        if (!response.ok) throw await _monitorErr(response, "Could not load your monitors");
        return (await response.json()) as Monitor[];
    };

    const createMonitor = async (url: string, frequency: string, notifyEmail: string): Promise<Monitor> => {
        const response = await fetch(`${baseUrl}/monitors`, {
            method: "POST",
            body: JSON.stringify({ url, frequency, notifyEmail }),
            headers: { ...authHeaders(), "Content-Type": "application/json" },
        });
        if (!response.ok) throw await _monitorErr(response, "Could not start monitoring that URL");
        return (await response.json()) as Monitor;
    };

    const updateMonitor = async (
        id: string,
        patch: Partial<Pick<Monitor, "enabled" | "frequency" | "notifyEmail">>,
    ): Promise<Monitor> => {
        const response = await fetch(`${baseUrl}/monitors/${id}`, {
            method: "PATCH",
            body: JSON.stringify(patch),
            headers: { ...authHeaders(), "Content-Type": "application/json" },
        });
        if (!response.ok) throw await _monitorErr(response, "Could not update that monitor");
        return (await response.json()) as Monitor;
    };

    const deleteMonitor = async (id: string): Promise<void> => {
        const response = await fetch(`${baseUrl}/monitors/${id}`, {
            method: "DELETE",
            headers: authHeaders(),
        });
        if (!response.ok) throw await _monitorErr(response, "Could not remove that monitor");
    };

    const runPipelineRemediate = async (
        file: File,
        approvedIds: string[],
        rejectedIds: string[],
        token?: string,
        accountId?: string,
    ): Promise<PipelineRemediateResult> => {
        if (mockMode) {
            return {
                jobId: "mock-" + Date.now().toString(36),
                filename: file.name.replace(/\.([^.]+)$/, "-remediated.$1"),
                downloadUrl: "",
                approved: approvedIds,
                rejected: rejectedIds,
                executions: approvedIds.map((id) => ({
                    actionCode: "MOCK",
                    targetNodeId: id,
                    status: "success",
                    notes: "Mock-applied. Demo Mode does not produce a real file.",
                })),
                writer: { applied: [], skipped: [] },
                manualReviewItemsCreated: rejectedIds.length,
            };
        }
        const form = new FormData();
        form.append("file", file);
        form.append("approved_violations", JSON.stringify(approvedIds));
        form.append("rejected_violations", JSON.stringify(rejectedIds));
        const headers = authHeaders();
        if (token) headers["Authorization"] = "Bearer " + token;
        void accountId; // legacy param; backend ignores X-Account-Id now
        const response = await fetch(`${baseUrl}/pipeline/remediate`, {
            method: "POST",
            body: form,
            headers,
        });
        if (!response.ok) {
            const message = await response.text();
            const err = new Error(message || "Remediate failed") as Error & { status?: number };
            err.status = response.status;
            throw err;
        }
        const data = (await response.json()) as PipelineRemediateResult;
        // The backend returns a relative, HMAC-signed downloadUrl
        // (/pipeline/files/<job>/<file>?exp=..&sig=..). Make it absolute so the
        // browser can open it directly - and so the required signature is kept
        // (do NOT rebuild this URL with getPipelineFileUrl, which is unsigned).
        if (data.downloadUrl && !/^https?:\/\//.test(data.downloadUrl)) {
            data.downloadUrl = `${baseUrl}${data.downloadUrl}`;
        }
        return data;
    };

    const getPipelineFileUrl = (jobId: string, filename: string) =>
        `${baseUrl}/pipeline/files/${jobId}/${filename}`;

    const batchZip = async (
        jobs: Array<{ jobId: string; filename: string }>,
        token?: string,
    ): Promise<Blob> => {
        const headers: Record<string, string> = { "Content-Type": "application/json", ...authHeaders() };
        if (token) headers["Authorization"] = "Bearer " + token;
        const response = await fetch(`${baseUrl}/pipeline/batch-zip`, {
            method: "POST",
            body: JSON.stringify({ jobs }),
            headers,
        });
        if (!response.ok) {
            const message = await response.text();
            const err = new Error(message || "Batch download failed") as Error & { status?: number };
            err.status = response.status;
            throw err;
        }
        return await response.blob();
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
        if (__DEV__) console.log("[api] POST /remediate");
        return request<RemediateResponse>("/remediate", payload);
    };

    const manualReview = async (docId?: string): Promise<ManualReviewItem[]> => {
        if (mockMode) {
            return [];
        }
        const path = docId ? `/documents/${docId}/manual-review` : "/manual-review";
        if (__DEV__) console.log(`[api] GET ${path}`);
        const response = await fetch(`${baseUrl}${path}`, { headers: authHeaders() });
        if (!response.ok) {
            return [];
        }
        return (await response.json()) as ManualReviewItem[];
    };

    const clearManualReview = async (): Promise<{ cleared: number }> => {
        if (mockMode) {
            return { cleared: 0 };
        }
        if (__DEV__) console.log("[api] DELETE /manual-review");
        const response = await fetch(`${baseUrl}/manual-review`, { method: "DELETE", headers: authHeaders() });
        if (!response.ok) {
            return { cleared: 0 };
        }
        return (await response.json()) as { cleared: number };
    };

    const updateManualReview = async (
        itemId: string,
        payload: { status: "pending" | "approved" | "rejected"; approvedText?: string },
    ): Promise<ManualReviewItem> => {
        if (mockMode) {
            return {
                id: itemId,
                issueId: "mock",
                targetNodeId: "doc-1",
                reason: "mock",
                status: payload.status,
                approvedText: payload.approvedText,
            };
        }
        const response = await fetch(`${baseUrl}/manual-review/${itemId}`, {
            method: "PATCH",
            headers: authHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            const message = await response.text();
            throw new Error(message || "Manual review update failed");
        }
        return (await response.json()) as ManualReviewItem;
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
            headers: authHeaders(),
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
        const response = await fetch(`${baseUrl}/jobs/${jobId}`, { headers: authHeaders() });
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
        const response = await fetch(`${baseUrl}/documents/${docId}/issues`, { headers: authHeaders() });
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

    const finalizeDocument = async (docId: string): Promise<FinalizeResponse> => {
        if (mockMode) {
            return { docId, finalized: true };
        }
        return request<FinalizeResponse>(`/documents/${docId}/finalize`, {});
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
        const response = await fetch(`${baseUrl}/documents/${docId}/diff`, { headers: authHeaders() });
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
        const response = await fetch(`${baseUrl}/documents/${docId}/summary`, { headers: authHeaders() });
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
        const response = await fetch(`${baseUrl}/documents/${docId}/tag-tree`, { headers: authHeaders() });
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
        const response = await fetch(`${baseUrl}/documents/${docId}/fix-report`, { headers: authHeaders() });
        if (!response.ok) {
            const message = await response.text();
            throw new Error(message || "Fix report fetch failed");
        }
        return (await response.json()) as FixReport;
    };

    const listPolicies = async (): Promise<PolicySummary[]> => {
        if (mockMode) {
            return [];
        }
        try {
            return await getJson<PolicySummary[]>("/api/policies");
        } catch {
            return await getJson<PolicySummary[]>("/policies");
        }
    };

    const getPolicy = async (policyId: string): Promise<PolicyDetail> => {
        if (mockMode) {
            return {
                id: policyId,
                name: "Mock Policy",
                description: "Mock policy detail",
                version: 1,
                targets: ["pdf"],
                updated_at: new Date().toISOString(),
                policy_json: {},
            };
        }
        try {
            return await getJson<PolicyDetail>(`/api/policies/${policyId}`);
        } catch {
            return await getJson<PolicyDetail>(`/policies/${policyId}`);
        }
    };

    const setJobPolicy = async (
        jobId: string,
        policyPackId: string,
    ): Promise<{ jobId: string; policy: Record<string, unknown> }> => {
        if (mockMode) {
            return { jobId, policy: { policyPackId } };
        }
        return request<{ jobId: string; policy: Record<string, unknown> }>(`/jobs/${jobId}/policy`, {
            policy_pack_id: policyPackId,
        });
    };

    const getJobScore = async (jobId: string): Promise<JobScoreResponse> => {
        if (mockMode) {
            return { jobId, scores: [] };
        }
        return getJson<JobScoreResponse>(`/jobs/${jobId}/score`);
    };

    const createEvidenceBundle = async (
        jobId: string,
        options: EvidenceBundleCreateOptions = {},
    ): Promise<EvidenceBundleCreateResponse> => {
        if (mockMode) {
            return {
                bundleId: `bundle-${Date.now()}`,
                jobId,
                docId: "doc-mock",
                createdAt: new Date().toISOString(),
                bundleHash: "mock",
                downloadUrl: `/evidence-bundles/bundle-${Date.now()}/download`,
            };
        }
        return request<EvidenceBundleCreateResponse>(`/jobs/${jobId}/evidence-bundle`, options);
    };

    const listEvidenceBundlesForDoc = async (docId: string): Promise<EvidenceBundleSummary[]> => {
        if (mockMode) {
            return [];
        }
        return getJson<EvidenceBundleSummary[]>(`/documents/${docId}/evidence-bundles`);
    };

    const getEvidenceBundleDownloadUrl = (bundleId: string): string => `${baseUrl}/evidence-bundles/${bundleId}/download`;

    return {
        scan,
        remediate,
        runPipeline,
        runPipelineUrl,
        runSiteScan,
        listMonitors,
        createMonitor,
        updateMonitor,
        deleteMonitor,
        runPipelineRemediate,
        getPipelineFileUrl,
        batchZip,
        manualReview,
        clearManualReview,
        updateManualReview,
        uploadDocument,
        startDocumentScan,
        getJob,
        getIssues,
        applyFixes,
        finalizeDocument,
        getDownloadUrl,
        getDocumentDiff,
        getDocumentSummary,
        getTagTree,
        getFixReport,
        getRebuiltUrl,
        listPolicies,
        getPolicy,
        setJobPolicy,
        getJobScore,
        createEvidenceBundle,
        listEvidenceBundlesForDoc,
        getEvidenceBundleDownloadUrl,
    };
}





