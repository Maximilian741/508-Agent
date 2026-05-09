import { MaterialIcons } from "@expo/vector-icons";
import { useRouter } from "expo-router";
import { useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { useAppStore } from "../src/store/useAppStore";
import { PolicyPicker } from "../src/components/PolicyPicker";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Chip } from "../src/ui/components/Chip";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

export default function DocumentsScreen() {
  const router = useRouter();
  const theme = useTheme();
  const {
    setSelectedDocument,
    mockMode,
    uploadDocument,
    runDocumentScan,
    uploadedDocument,
    scanJob,
    documentIssues,
    isUploading,
    policies,
    selectedPolicyId,
    policyDetailsById,
    fetchPolicies,
    setSelectedPolicy,
    fetchPolicyDetail,
  } = useAppStore();
  const [error, setError] = useState<string | null>(null);
  const [uploadName, setUploadName] = useState<string | null>(null);
  const [uploadType, setUploadType] = useState<string | null>(null);
  const [uploadNotice, setUploadNotice] = useState<string | null>(null);
  const fileInputRef = useRef<any>(null);

  const canScan = Boolean(uploadedDocument);

  const handlePickedFile = async (file: File) => {
    setUploadName(file.name);
    setUploadType(file.type || "unknown");
    const result = await uploadDocument(file);
    if (!result.ok) {
      setUploadNotice(result.error ?? "Upload failed.");
      return;
    }
    const format = inferFormat(file.name);
    setSelectedDocument({ documentId: result.data?.docId ?? "doc-1", sourceFormat: format, content: "" });
    setUploadNotice("Upload received. You can run a scan.");
  };

  const handleUploadPress = async () => {
    if (Platform.OS === "web") {
      fileInputRef.current?.click();
      return;
    }
    setUploadNotice("Upload is supported on web only in this build. Use Sample Document to continue.");
  };

  useEffect(() => {
    void fetchPolicies();
  }, [fetchPolicies]);

  useEffect(() => {
    if (Platform.OS !== "web") return;
    if (typeof document === "undefined") return;

    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".pdf,.docx,.pptx";
    input.onchange = () => {
      const file = input.files?.[0];
      if (file) {
        void handlePickedFile(file);
      }
    };
    fileInputRef.current = input;
    return () => {
      input.onchange = null;
      fileInputRef.current = null;
    };
  }, []);

  const dragHandlers =
    Platform.OS === "web"
      ? ({
          onDragOver: (event: any) => event.preventDefault?.(),
          onDrop: (event: any) => {
            event.preventDefault?.();
            const file = event?.dataTransfer?.files?.[0];
            if (file) {
              void handlePickedFile(file);
            }
          },
        } as any)
      : {};

  const handleRunScan = async () => {
    if (uploadedDocument) {
      const result = await runDocumentScan(uploadedDocument.docId);
      if (result.ok) {
        router.push("/scan");
      } else {
        setError(result.error ?? "Scan failed. Try again.");
      }
      return;
    }
    setError("Upload a document before running a scan.");
  };

  return (
    <Screen scroll>
      <View style={styles.header}>
        <View>
          <Text style={[theme.typography.title, { color: theme.colors.text }]}>Document</Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
            Prepare payload and run a scan
          </Text>
        </View>
        <Chip label={mockMode ? "Mock Mode" : "Live Mode"} tone={mockMode ? "warning" : "success"} />
      </View>

      {error && <InlineNotice title="Scan failed" message={error} tone="danger" />}

      <PolicyPicker
        policies={policies.items}
        selectedPolicyId={selectedPolicyId}
        onSelectPolicy={(policyId) => setSelectedPolicy(policyId)}
        onOpenDetails={(policyId) => {
          void fetchPolicyDetail(policyId);
        }}
        detail={selectedPolicyId ? policyDetailsById[selectedPolicyId] : undefined}
        error={policies.error}
      />

      <Card>
        <View style={styles.uploadHeader}>
          <View>
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Upload Document</Text>
            <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
              Add a PDF, DOCX, or PPTX to scan
            </Text>
          </View>
          <Button title="Upload" onPress={handleUploadPress} variant="secondary" disabled={isUploading} />
        </View>
        <Pressable accessibilityRole="button" accessibilityLabel="Upload document"
          onPress={handleUploadPress}
          disabled={isUploading}
          style={[
            styles.dropZone,
            { borderColor: theme.colors.border, backgroundColor: theme.colors.surface2 },
          ]}
          {...dragHandlers}
        >
          <MaterialIcons name="cloud-upload" size={28} color={theme.colors.textMuted} />
          <Text style={[theme.typography.body, { color: theme.colors.text }]}>Drop file here or click to browse</Text>
          <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>PDF, DOCX, PPTX supported</Text>
        </Pressable>
        {uploadName && (
          <View style={styles.uploadMeta}>
            <Chip label={`Selected: ${uploadName}`} tone="info" />
            <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>Type: {uploadType}</Text>
          </View>
        )}
        {uploadNotice && <InlineNotice title="Upload status" message={uploadNotice} tone="warning" />}
        {uploadedDocument && (
          <InlineNotice
            title="Ready to scan"
            message={`Uploaded ${uploadedDocument.filename} (${uploadedDocument.sizeBytes} bytes)`}
            tone="success"
          />
        )}
        {scanJob && scanJob.status !== "done" && (
          <InlineNotice
            title="Scan in progress"
            message={`${scanJob.status} • ${scanJob.progress}%`}
            tone="info"
          />
        )}
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Run Scan</Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Start a scan for the uploaded document.
        </Text>
        <View style={styles.actions}>
          <Button
            title="Run Scan"
            onPress={handleRunScan}
            loading={isUploading}
            disabled={!canScan || isUploading}
          />
          {!uploadedDocument && (
            <Chip label="Upload required" tone="warning" />
          )}
        </View>
        {scanJob && scanJob.status !== "done" && (
          <View style={styles.scanRow}>
            <ActivityIndicator color={theme.colors.accent} />
            <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
              {scanJob.status} • {scanJob.progress}%
            </Text>
          </View>
        )}
        {documentIssues.length > 0 && (
          <InlineNotice
            title="Issues ready"
            message={`Scan complete. ${documentIssues.length} issues detected.`}
            tone="success"
          />
        )}
      </Card>
    </Screen>
  );
}

function inferFormat(filename: string): string {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".docx")) return "docx";
  if (lower.endsWith(".pptx")) return "pptx";
  return "pdf";
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  uploadHeader: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  dropZone: {
    marginTop: 12,
    borderWidth: 1,
    borderRadius: 14,
    paddingVertical: 24,
    paddingHorizontal: 16,
    alignItems: "center",
    gap: 8,
  },
  uploadMeta: { marginTop: 12, gap: 6 },
  actions: { flexDirection: "row", gap: 12, marginTop: 16, flexWrap: "wrap", alignItems: "center" },
  scanRow: { flexDirection: "row", gap: 8, alignItems: "center", marginTop: 12 },
});
