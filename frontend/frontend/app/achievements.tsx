/**
 * Achievements - grid view with unlock progress + confetti.
 */
import { useMemo, useState } from "react";
import { StyleSheet, Text, View } from "react-native";

import {
  Achievement,
  loadAchievements,
  totalAchievements,
  unlockedCount,
} from "../src/domain/achievements";
import { Card } from "../src/ui/components/Card";
import { Icon, IconName } from "../src/ui/components/Icon";
import { ProgressBar } from "../src/ui/components/ProgressBar";
import { Chip } from "../src/ui/components/Chip";
import { Confetti } from "../src/ui/components/Confetti";
import { EmptyState } from "../src/ui/components/EmptyState";
import { Screen } from "../src/ui/components/Screen";
import { Hero } from "../src/ui/components/Hero";
import { useTheme } from "../src/ui/useTheme";

function pickGlyph(id: string): IconName {
  const k = id.toLowerCase();
  if (k.includes("first")) return "flag";
  if (k.includes("ten") || k.includes("audits")) return "file-text";
  if (k.includes("score") || k.includes("ninety")) return "star";
  if (k.includes("share")) return "share-2";
  if (k.includes("streak")) return "activity";
  return "award";
}

export default function AchievementsScreen() {
  const theme = useTheme();
  const list: Achievement[] = useMemo(() => loadAchievements(), []);
  const unlocked = unlockedCount();
  const total = totalAchievements();
  const pct = total > 0 ? Math.round((unlocked / total) * 100) : 0;
  const [burst] = useState<number>(unlocked === total && total > 0 ? Date.now() : 0);

  return (
    <Screen scroll>
      <Confetti trigger={burst} />
      <Hero
        eyebrow="ACHIEVEMENTS"
        title="Achievements"
        subtitle="Stickers you have earned across all your audits."
        rightSlot={
          <Chip
            label={unlocked + " of " + total}
            tone={unlocked === total ? "success" : "info"}
          />
        }
      />

      <Card>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Most unlock the first time you do something. Hit them all and you get the full board.
        </Text>
        <View style={{ marginTop: 12 }}>
          <ProgressBar value={pct / 100} accessibilityLabel="Achievements unlocked" />
        </View>
        <Text
          style={[
            theme.typography.body,
            { color: theme.colors.textMuted, marginTop: 6, fontSize: 12 },
          ]}
        >
          {pct + "% complete"}
        </Text>
      </Card>

      {unlocked === 0 ? (
        <EmptyState
          icon="trophy"
          title="No stickers yet"
          body="Run your first audit to start unlocking achievements. Most pop the very first time you do something."
        />
      ) : null}

      <View style={styles.grid}>
        {list.map((a) => {
          const locked = !a.unlockedAt;
          return (
            <View
              key={a.id}
              style={[
                styles.tile,
                {
                  borderRadius: theme.radius.lg,
                  borderColor: locked ? theme.colors.glassBorder : theme.colors.accent,
                  backgroundColor: locked ? theme.colors.surface2 : theme.colors.surface,
                },
              ]}
            >
              <Icon name={locked ? "lock" : pickGlyph(a.id)} size={22} color={locked ? theme.colors.textMuted : theme.colors.accent} />
              <Text
                style={[
                  theme.typography.h2,
                  { color: theme.colors.text, fontSize: 14, marginTop: 6 },
                ]}
              >
                {a.title}
              </Text>
              <Text
                style={[
                  theme.typography.body,
                  { color: theme.colors.textMuted, marginTop: 4, fontSize: 12 },
                ]}
              >
                {a.description}
              </Text>
              <View style={{ marginTop: 6 }}>
                <Chip
                  label={locked ? "Locked" : "Unlocked"}
                  tone={locked ? "default" : "success"}
                />
              </View>
            </View>
          );
        })}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  barTrack: { height: 8, borderRadius: 0, marginTop: 12, overflow: "hidden" },
  barFill: { height: 8, borderRadius: 0 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 12, marginTop: 4 },
  tile: {
    flexBasis: "30%",
    flexGrow: 1,
    minWidth: 220,
    borderWidth: 1,
    padding: 14,
    gap: 4,
  },
  icon: { fontSize: 28 },
});
