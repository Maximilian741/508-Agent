import { Button, FlatList, StyleSheet, Text, View } from "react-native";

import { useAppStore } from "../src/store/useAppStore";

export default function ManualReviewScreen() {
    const manualReviewQueue = useAppStore((state) => state.manualReviewQueue);
    const clearManualReviewQueue = useAppStore((state) => state.clearManualReviewQueue);

    if (manualReviewQueue.length === 0) {
        return (
            <View style={styles.container}>
                <Text style={styles.empty}>No items awaiting manual review.</Text>
            </View>
        );
    }

    return (
        <View style={styles.container}>
            <Button title="Clear Queue" onPress={clearManualReviewQueue} />
            <FlatList
                data={manualReviewQueue}
                keyExtractor={(item) => item.id}
                renderItem={({ item }) => (
                    <View style={styles.card}>
                        <Text style={styles.title}>Issue {item.issueId}</Text>
                        <Text>Node: {item.targetNodeId}</Text>
                        <Text>Reason: {item.reason}</Text>
                        {item.notes && <Text>Notes: {item.notes}</Text>}
                    </View>
                )}
            />
        </View>
    );
}

const styles = StyleSheet.create({
    container: { flex: 1, padding: 24, gap: 12 },
    empty: { color: "#666" },
    card: { padding: 12, borderRadius: 8, backgroundColor: "#fff", gap: 4 },
    title: { fontWeight: "700" },
});
