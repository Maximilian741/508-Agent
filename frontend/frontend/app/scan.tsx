import { useMemo, useState } from "react";
import { FlatList, Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { Issue } from "../src/api/client";
import { useAppStore } from "../src/store/useAppStore";

const severityOrder: Record<string, number> = { error: 0, warning: 1, info: 2 };

export default function ScanScreen() {
    const router = useRouter();
    const scanResults = useAppStore((state) => state.scanResults);
    const selectedDocument = useAppStore((state) => state.selectedDocument);
    const [sortDescending, setSortDescending] = useState(false);

    const issues = useMemo(() => {
        const list = scanResults?.issues ?? [];
        const sorted = [...list].sort((a, b) => {
            const diff = severityOrder[a.severity] - severityOrder[b.severity];
            return sortDescending ? -diff : diff;
        });
        return sorted;
    }, [scanResults, sortDescending]);

    if (!scanResults) {
        return (
            <View style={styles.container}>
                <Text style={styles.empty}>No scan results yet. Run a scan from Documents.</Text>
            </View>
        );
    }

    return (
        <View style={styles.container}>
            <Pressable onPress={() => setSortDescending((prev) => !prev)}>
                <Text style={styles.sort}>Sort by severity {sortDescending ? "(desc)" : "(asc)"}</Text>
            </Pressable>
            <DocumentTree
                documentId={scanResults.documentId}
                content={selectedDocument?.content ?? ""}
                issues={issues}
            />
            <FlatList
                data={issues}
                keyExtractor={(item) => item.id}
                renderItem={({ item }) => <IssueRow issue={item} onPress={() => router.push(`/issue/${item.id}`)} />}
                ItemSeparatorComponent={() => <View style={styles.separator} />}
            />
        </View>
    );
}

function DocumentTree({
    documentId,
    content,
    issues,
}: {
    documentId: string;
    content: string;
    issues: Issue[];
}) {
    const issueNodes = new Set(issues.map((issue) => issue.nodeId));
    const lines = useMemo(() => buildTreeLines(documentId, content, issueNodes), [documentId, content, issueNodes]);

    return (
        <View style={styles.treeContainer}>
            <Text style={styles.treeTitle}>Document Tree</Text>
            <Text style={styles.treeText}>{lines.join("\n")}</Text>
        </View>
    );
}

function buildTreeLines(documentId: string, content: string, issueNodes: Set<string>): string[] {
    let parsed: Record<string, any> = {};
    try {
        parsed = JSON.parse(content);
    } catch (error) {
        parsed = {};
    }
    const title = typeof parsed.title === "string" ? parsed.title : "";
    const lines: string[] = [];
    const docId = "doc-1";
    const docNote = issueNodes.has(docId) ? "  < ISSUE HERE" : "";
    lines.push(`document (${docId})${docNote}`);
    lines.push(`  title ${title.trim() ? JSON.stringify(title) : "(missing)"}`);
    const images = Array.isArray(parsed.images) ? parsed.images : [];
    if (images.length > 0) {
        lines.push("  images");
        images.forEach((image: any, index: number) => {
            const id = `img-${index + 1}`;
            const alt = typeof image?.alt_text === "string" ? image.alt_text : "";
            const decorative = Boolean(image?.decorative) || alt.toLowerCase().includes("decorative");
            const note = issueNodes.has(id) ? "  < ISSUE HERE" : "";
            lines.push(`    ${id} alt_text=${alt ? JSON.stringify(alt) : "(missing)"} decorative=${decorative}${note}`);
        });
    }
    const headings = Array.isArray(parsed.headings) ? parsed.headings : [];
    if (headings.length > 0) {
        lines.push("  headings");
        headings.forEach((heading: any, index: number) => {
            const id = typeof heading?.id === "string" ? heading.id : `h-${index + 1}`;
            const level = typeof heading?.level === "number" ? heading.level : 1;
            const text = typeof heading?.text === "string" ? heading.text : "Heading";
            const note = issueNodes.has(id) ? "  < ISSUE HERE" : "";
            lines.push(`    ${id} h${level} ${JSON.stringify(text)}${note}`);
        });
    }
    return lines;
}

function IssueRow({ issue, onPress }: { issue: Issue; onPress: () => void }) {
    return (
        <Pressable onPress={onPress} style={styles.row}>
            <View style={styles.rowHeader}>
                <Text style={styles.severity}>{issue.severity.toUpperCase()}</Text>
                <Text style={styles.rule}>{issue.ruleId}</Text>
            </View>
            <Text style={styles.nodeId}>Node: {issue.nodeId}</Text>
            <Text style={styles.description}>{issue.description}</Text>
        </Pressable>
    );
}

const styles = StyleSheet.create({
    container: { flex: 1, padding: 24, gap: 12 },
    empty: { color: "#666" },
    sort: { color: "#1f4acc", fontWeight: "600" },
    treeContainer: { padding: 12, borderRadius: 8, backgroundColor: "#f0f3ff", gap: 8 },
    treeTitle: { fontWeight: "700" },
    treeText: { fontFamily: "Courier", fontSize: 12, color: "#2c2c2c" },
    row: { paddingVertical: 12 },
    rowHeader: { flexDirection: "row", justifyContent: "space-between" },
    severity: { fontWeight: "700" },
    rule: { color: "#666" },
    nodeId: { color: "#444", marginTop: 4 },
    description: { marginTop: 4 },
    separator: { height: 1, backgroundColor: "#e0e0e0" },
});
