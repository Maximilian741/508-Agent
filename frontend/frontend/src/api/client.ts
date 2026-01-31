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
}

export interface ApiClient {
    scan: (payload: ScanRequest) => Promise<ScanResponse>;
    remediate: (payload: RemediateRequest) => Promise<RemediateResponse>;
    manualReview: () => Promise<ManualReviewItem[]>;
    clearManualReview: () => Promise<{ cleared: number }>;
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

    return { scan, remediate, manualReview, clearManualReview };
}
