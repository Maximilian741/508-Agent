import { useLocalSearchParams, useRouter } from "expo-router";
import { useEffect, useMemo, useState } from "react";
import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { RemediationAction } from "../../src/api/client";
import { useAppStore } from "../../src/store/useAppStore";

export default function IssueDetailScreen() {
    const router = useRouter();
    const params = useLocalSearchParams<{ id?: string }>();
    const issueId = typeof params.id === "string" ? params.id : "";

    const scanResults = useAppStore((state) => state.scanResults);
    const runRemediate = useAppStore((state) => state.runRemediate);
    const addManualReviewItem = useAppStore((state) => state.addManualReviewItem);
    const fetchManualReview = useAppStore((state) => state.fetchManualReview);
    const mockMode = useAppStore((state) => state.mockMode);

    const issue = useMemo(() => {
        return scanResults?.issues.find((item) => item.id === issueId) ?? null;
    }, [scanResults, issueId]);

    const [selectedAction, setSelectedAction] = useState<RemediationAction | null>(
        issue?.recommendedActions?.[0] ?? null,
    );
    const [lastResult, setLastResult] = useState<string | null>(null);

    useEffect(() => {
        setSelectedAction(issue?.recommendedActions?.[0] ?? null);
    }, [issue]);

    const handleRunFix = async () => {
        if (!issue || !selectedAction) {
            Alert.alert("No action", "Select an action to run.");
            return;
        }
        const response = await runRemediate({
            issueId: issue.id,
            targetNodeId: issue.nodeId,
            actionCode: selectedAction.actionCode,
        });
        if (!response.ok || !response.results) {
            Alert.alert("Remediation failed", response.error ?? "Unknown error");
            return;
        }
        const result = response.results[0];
        setLastResult(`${result.actionCode} -> ${result.status}`);
        if (result.status !== "success") {
            if (mockMode) {
                addManualReviewItem({
                    id: `${issue.id}-${selectedAction.actionCode}-${Date.now()}`,
                    issueId: issue.id,
                    targetNodeId: issue.nodeId,
                    reason: "Execution blocked by policy.",
                    notes: result.notes,
                });
            } else {
                await fetchManualReview();
            }
            Alert.alert("Manual review required", "The action was blocked; queued for review.");
            router.push("/manual-review");
            return;
        }
        Alert.alert("Success", result.notes);
    };

    if (!issue) {
        return (
            <View style={styles.container}>
                <Text style={styles.empty}>Issue not found. Run a scan first.</Text>
            </View>
        );
    }

    return (
        <ScrollView contentContainerStyle={styles.container}>
            <Text style={styles.title}>Issue {issue.id}</Text>
            <Text style={styles.label}>Severity</Text>
            <Text>{issue.severity.toUpperCase()}</Text>
            <Text style={styles.label}>Rule</Text>
            <Text>{issue.ruleId}</Text>
            <Text style={styles.label}>Description</Text>
            <Text>{issue.description}</Text>
            <Text style={styles.label}>Target Node</Text>
            <Text>{issue.nodeId}</Text>
            {issue.nodePath && (
                <Text>Path: {issue.nodePath.join(" > ")}</Text>
            )}

            <Text style={styles.sectionTitle}>Recommended Actions</Text>
            {issue.recommendedActions.length === 0 && (
                <Text style={styles.empty}>No actions recommended.</Text>
            )}
            {issue.recommendedActions.map((action) => (
                <Pressable
                    key={action.actionCode}
                    onPress={() => setSelectedAction(action)}
                    style={[
                        styles.actionCard,
                        selectedAction?.actionCode === action.actionCode && styles.actionSelected,
                    ]}
                >
                    <Text style={styles.actionTitle}>{action.actionCode}</Text>
                    <Text>{action.description}</Text>
                </Pressable>
            ))}

            <Pressable
                style={[styles.primaryButton, !selectedAction && styles.buttonDisabled]}
                onPress={handleRunFix}
                disabled={!selectedAction}
            >
                <Text style={styles.primaryButtonText}>Run Fix</Text>
            </Pressable>

            {lastResult && (
                <Text style={styles.result}>Last result: {lastResult}</Text>
            )}
        </ScrollView>
    );
}

const styles = StyleSheet.create({
    container: { padding: 24, gap: 12 },
    title: { fontSize: 20, fontWeight: "700" },
    label: { fontWeight: "600" },
    sectionTitle: { marginTop: 8, fontWeight: "700" },
    empty: { color: "#666" },
    actionCard: { borderWidth: 1, borderColor: "#d1d1d1", borderRadius: 8, padding: 12, gap: 4 },
    actionSelected: { borderColor: "#1f4acc", backgroundColor: "#eef3ff" },
    actionTitle: { fontWeight: "700" },
    primaryButton: { marginTop: 8, padding: 14, backgroundColor: "#1f4acc", borderRadius: 8, alignItems: "center" },
    primaryButtonText: { color: "#fff", fontWeight: "700" },
    buttonDisabled: { opacity: 0.5 },
    result: { marginTop: 12, color: "#1f4acc" },
});
