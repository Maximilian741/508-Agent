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

export interface PipelineResponse {
    summary: PipelineSummary;
    violations: PipelineViolation[];
    executions: PipelineExecutionResult[];
    score: PipelineScore;
    aiProvider: string;
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
    runPipelineRemediate: (
        file: File,
        approvedViolationIds: string[],
        rejectedViolationIds: string[],
    ) => Promise<PipelineRemediateResult>;
    getPipelineFileUrl: (jobId: string, filename: string) => string;
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

    const getJson = async <T>(path: string): Promise<T> => {
        const response = await fetch(`${baseUrl}${path}`);
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
        const response = await fetch(url, { method: "POST", body: form });
        if (!response.ok) {
            const message = await response.text();
            throw new Error(message || "Pipeline analyze failed");
        }
        return (await response.json()) as PipelineResponse;
    };

    const runPipelineRemediate = async (
        file: File,
        approvedIds: string[],
        rejectedIds: string[],
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
        const response = await fetch(`${baseUrl}/pipeline/remediate`, {
            method: "POST",
            body: form,
        });
        if (!response.ok) {
            const message = await response.text();
            throw new Error(message || "Remediate failed");
        }
        return (await response.json()) as PipelineRemediateResult;
    };

    const getPipelineFileUrl = (jobId: string, filename: string) =>
        `${baseUrl}/pipeline/files/${jobId}/${filename}`;

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

    const manualReview = async (docId?: string): Promise<ManualReviewItem[]> => {
        if (mockMode) {
            return [];
        }
        const path = docId ? `/documents/${docId}/manual-review` : "/manual-review";
        console.log(`[api] GET ${path}`);
        const response = await fetch(`${baseUrl}${path}`);
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
            headers: { "Content-Type": "application/json" },
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
        runPipelineRemediate,
        getPipelineFileUrl,
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





