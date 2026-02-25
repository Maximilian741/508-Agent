import { useLocalSearchParams } from "expo-router";
import { useEffect, useState } from "react";
import { Text } from "react-native";

import { ExportsList } from "../src/components/ExportsList";
import { useAppStore } from "../src/store/useAppStore";
import { Screen } from "../src/ui/components/Screen";
import { useTheme } from "../src/ui/useTheme";

export default function ExportsScreen() {
  const theme = useTheme();
  const params = useLocalSearchParams<{ docId?: string | string[] }>();
  const uploadedDocument = useAppStore((state) => state.uploadedDocument);
  const fetchEvidenceBundles = useAppStore((state) => state.fetchEvidenceBundles);
  const evidenceBundlesByDocId = useAppStore((state) => state.evidenceBundlesByDocId);
  const isExportingBundle = useAppStore((state) => state.isExportingBundle);
  const [error, setError] = useState<string | null>(null);

  const docIdParam = Array.isArray(params.docId) ? params.docId[0] : params.docId;
  const docId = docIdParam ?? uploadedDocument?.docId ?? null;

  useEffect(() => {
    if (!docId) return;
    const run = async () => {
      const result = await fetchEvidenceBundles(docId);
      if (!result.ok) {
        setError(result.error ?? "Unable to load exports.");
        return;
      }
      setError(null);
    };
    void run();
  }, [docId, fetchEvidenceBundles]);

  return (
    <Screen scroll>
      <Text style={[theme.typography.title, { color: theme.colors.text }]}>Evidence Exports</Text>
      <ExportsList
        docId={docId}
        bundles={docId ? evidenceBundlesByDocId[docId] ?? [] : []}
        loading={isExportingBundle}
        error={error ?? undefined}
        onRefresh={() => {
          if (docId) {
            void fetchEvidenceBundles(docId);
          }
        }}
      />
    </Screen>
  );
}
