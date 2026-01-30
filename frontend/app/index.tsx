import { Link } from "expo-router";
import { StyleSheet, Text, View } from "react-native";

export default function HomeScreen() {
    return (
        <View style={styles.container}>
            <Text style={styles.title}>508 Agent</Text>
            <View style={styles.linkGroup}>
                <Link href="/documents" style={styles.link}>Documents</Link>
                <Link href="/scan" style={styles.link}>Latest Scan</Link>
                <Link href="/manual-review" style={styles.link}>Manual Review Queue</Link>
                <Link href="/settings" style={styles.link}>Settings</Link>
            </View>
        </View>
    );
}

const styles = StyleSheet.create({
    container: { flex: 1, padding: 24, gap: 16, backgroundColor: "#f8f8f8" },
    title: { fontSize: 28, fontWeight: "600" },
    linkGroup: { gap: 12 },
    link: { fontSize: 18, color: "#1f4acc" },
});
