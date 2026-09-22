/**
 * LegacyFlowMoved: what the retired /documents and /scan routes now render.
 *
 * Those screens drove the old /documents/* fix endpoints, which wrote fixes
 * (including a title guessed from the file name and a hard-coded en-US
 * language) and handed the file back without charging anything. The backend
 * now answers 410 there. Fixing a document happens only on Audit, where every
 * fix is approved and priced before anything is charged, and only fixes that
 * are actually written into the file count. The routes stay so an old bookmark
 * lands on a pointer instead of a 404 or a page of buttons that can only fail.
 */
import { StyleSheet, Text, View } from "react-native";

import { Button } from "../ui/components/Button";
import { Card } from "../ui/components/Card";
import { Screen } from "../ui/components/Screen";
import { useTheme } from "../ui/useTheme";

export function LegacyFlowMoved({ screenTitle }: { screenTitle: string }) {
  const theme = useTheme();
  return (
    <Screen title={screenTitle}>
      <Card style={styles.card}>
        <Text accessibilityRole="header" style={[theme.typography.h2, { color: theme.colors.text }]}>
          Fixing documents now happens in Audit
        </Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          This older workflow has been retired. On the Audit screen you upload a PDF, Word, PowerPoint or HTML file,
          review every issue, approve the fixes you want, and see the credit cost before anything is charged. You pay
          only for fixes that are written into your file.
        </Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Findings you reject during an audit are kept in the manual review queue for your team.
        </Text>
        <View style={styles.actions}>
          <Button title="Go to Audit" href="/audit" />
          <Button title="Open manual review" href="/manual-review" variant="secondary" />
        </View>
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  card: { gap: 12 },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 12, marginTop: 4 },
});
