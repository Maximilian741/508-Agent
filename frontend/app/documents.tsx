import { useRouter } from "expo-router";
import { useMemo, useState } from "react";
import { Alert, Button, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { useAppStore } from "../src/store/useAppStore";

export default function DocumentsScreen() {
    const router = useRouter();
    const { selectedDocument, setSelectedDocument, runScan, isScanning } = useAppStore();
    const [documentId, setDocumentId] = useState(selectedDocument?.documentId ?? "doc-1");
    const [sourceFormat, setSourceFormat] = useState(selectedDocument?.sourceFormat ?? "pdf");
    const [content, setContent] = useState(selectedDocument?.content ?? "{\n  \"title\": \"Sample Document\"\n}");

    const canScan = useMemo(() => documentId.trim().length > 0, [documentId]);

    const handleSave = () => {
        setSelectedDocument({ documentId, sourceFormat, content });
        Alert.alert("Saved", "Document payload stored for scanning.");
    };

    const handleSample = () => {
        setDocumentId("doc-sample");
        setSourceFormat("pdf");
        setContent("{\n  \"title\": \"Accessibility Sample\",\n  \"sections\": [\n    {\"heading\": \"Intro\", \"body\": \"Hello\"}\n  ]\n}");
    };

    const handleScan = async () => {
        setSelectedDocument({ documentId, sourceFormat, content });
        const result = await runScan({ documentId, sourceFormat, content });
        if (!result.ok) {
            Alert.alert("Scan failed", result.error ?? "Unknown error");
            return;
        }
        router.push("/scan");
    };

    return (
        <ScrollView contentContainerStyle={styles.container}>
            <Text style={styles.label}>Document ID</Text>
            <TextInput value={documentId} onChangeText={setDocumentId} style={styles.input} />
            <Text style={styles.label}>Source Format (pdf/docx/pptx)</Text>
            <TextInput value={sourceFormat} onChangeText={setSourceFormat} style={styles.input} />
            <Text style={styles.label}>Content Payload (JSON)</Text>
            <TextInput
                value={content}
                onChangeText={setContent}
                style={[styles.input, styles.multiline]}
                multiline
            />
            <View style={styles.buttonRow}>
                <Button title="Use Sample" onPress={handleSample} />
                <Button title="Save" onPress={handleSave} />
            </View>
            <Button title={isScanning ? "Scanning..." : "Run Scan"} onPress={handleScan} disabled={!canScan || isScanning} />
        </ScrollView>
    );
}

const styles = StyleSheet.create({
    container: { padding: 24, gap: 16 },
    label: { fontWeight: "600" },
    input: { borderWidth: 1, borderColor: "#c5c5c5", borderRadius: 6, padding: 10, backgroundColor: "#fff" },
    multiline: { minHeight: 160, textAlignVertical: "top" },
    buttonRow: { flexDirection: "row", justifyContent: "space-between", gap: 12 },
});
