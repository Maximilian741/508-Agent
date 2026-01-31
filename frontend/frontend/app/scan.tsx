import { useMemo, useState } from "react";
import { FlatList, Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import { Issue } from "../src/api/client";
import { useAppStore } from "../src/store/useAppStore";

const severityOrder: Record<string, number> = { error: 0, warning: 1, info: 2 };

export default function ScanScreen() {
    const router = useRouter();
    const scanResults = useAppStore((state) => state.scanResults);
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
            <FlatList
                data={issues}
                keyExtractor={(item) => item.id}
                renderItem={({ item }) => <IssueRow issue={item} onPress={() => router.push(`/issue/${item.id}`)} />}
                ItemSeparatorComponent={() => <View style={styles.separator} />}
            />
        </View>
    );
}

function IssueRow({ issue, onPress }: { issue: Issue; onPress: () => void }) {
    return (
        <Pressable onPress={onPress} style={styles.row}>
            <View style={styles.rowHeader}>
                <Text style={styles.severity}>{issue.severity.toUpperCase()}</Text>
                <Text style={styles.rule}>{issue.ruleId}</Text>
            </View>
            <Text style={styles.description}>{issue.description}</Text>
        </Pressable>
    );
}

const styles = StyleSheet.create({
    container: { flex: 1, padding: 24, gap: 12 },
    empty: { color: "#666" },
    sort: { color: "#1f4acc", fontWeight: "600" },
    row: { paddingVertical: 12 },
    rowHeader: { flexDirection: "row", justifyContent: "space-between" },
    severity: { fontWeight: "700" },
    rule: { color: "#666" },
    description: { marginTop: 4 },
    separator: { height: 1, backgroundColor: "#e0e0e0" },
});
