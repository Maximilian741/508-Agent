import { useState } from "react";
import { Button, StyleSheet, Switch, Text, TextInput, View } from "react-native";

import { useAppStore } from "../src/store/useAppStore";

export default function SettingsScreen() {
    const apiBaseUrl = useAppStore((state) => state.apiBaseUrl);
    const mockMode = useAppStore((state) => state.mockMode);
    const setApiBaseUrl = useAppStore((state) => state.setApiBaseUrl);
    const setMockMode = useAppStore((state) => state.setMockMode);
    const [draftUrl, setDraftUrl] = useState(apiBaseUrl);

    return (
        <View style={styles.container}>
            <Text style={styles.label}>API Base URL</Text>
            <TextInput value={draftUrl} onChangeText={setDraftUrl} style={styles.input} />
            <Button title="Save" onPress={() => setApiBaseUrl(draftUrl)} />
            <View style={styles.toggleRow}>
                <Text style={styles.label}>Mock Mode</Text>
                <Switch value={mockMode} onValueChange={setMockMode} />
            </View>
        </View>
    );
}

const styles = StyleSheet.create({
    container: { flex: 1, padding: 24, gap: 16 },
    label: { fontWeight: "600" },
    input: { borderWidth: 1, borderColor: "#c5c5c5", borderRadius: 6, padding: 10, backgroundColor: "#fff" },
    toggleRow: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
});
