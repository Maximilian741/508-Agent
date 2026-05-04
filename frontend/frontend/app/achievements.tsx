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
import { PixelIcon, PixelGlyph } from "../src/ui/components/PixelIcon";
import { PixelProgress } from "../src/ui/components/PixelProgress";
import { Chip } from "../src/ui/components/Chip";
import { Confetti } from "../src/ui/components/Confetti";
import { Screen } from "../src/ui/components/Screen";
import { ShaderCanvas } from "../src/ui/components/ShaderCanvas";
import { useTheme } from "../src/ui/useTheme";

function pickGlyph(id: string): PixelGlyph {
  const k = id.toLowerCase();
  if (k.includes("first")) return "spark";
  if (k.includes("ten") || k.includes("audits")) return "doc";
  if (k.includes("score") || k.includes("ninety")) return "star";
  if (k.includes("share")) return "bolt";
  if (k.includes("streak")) return "heart";
  return "trophy";
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
      <View style={{ position: "relative", borderRadius: 18, overflow: "hidden", marginBottom: 16, minHeight: 160, backgroundColor: "#0B1020", padding: 24, justifyContent: "center" }}>
        <ShaderCanvas variant="pumpkin" opacity={0.55} />
        <View style={[styles.header, { position: "relative", zIndex: 1 }]}>
          <View style={{ flex: 1 }}>
            <Text
              accessibilityRole="header"
              style={[theme.typography.title, { color: "#FFFFFF" }]}
            >
              Achievements
            </Text>
            <Text style={[theme.typography.body, { color: "rgba(255,255,255,0.85)", marginTop: 4 }]}>
              Stickers you have earned across all your audits.
            </Text>
          </View>
          <Chip
            label={unlocked + " of " + total}
            tone={unlocked === total ? "success" : "info"}
          />
        </View>
      </View>

      <Card>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Stickers you have earned across all your audits. Most unlock the first
          time you do something. Hit them all and you get the full board.
        </Text>
        <View style={{ marginTop: 12 }}>
          <PixelProgress value={pct / 100} cells={20} cellSize={12} />
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

      <View style={styles.grid}>
        {list.map((a) => {
          const locked = !a.unlockedAt;
          return (
            <View
              key={a.id}
              style={[
                styles.tile,
                {
                  borderColor: locked ? theme.colors.border : theme.colors.accent,
                  backgroundColor: locked ? theme.colors.surface2 : theme.colors.surface,
                  opacity: locked ? 0.7 : 1,
                },
              ]}
            >
              <PixelIcon name={pickGlyph(a.id)} size={4} color={locked ? theme.colors.textMuted : theme.colors.accent} />
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
  header: { flexDirection: "row", alignItems: "center", gap: 12, marginBottom: 8 },
  barTrack: { height: 8, borderRadius: 4, marginTop: 12, overflow: "hidden" },
  barFill: { height: 8, borderRadius: 4 },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: 12, marginTop: 4 },
  tile: {
    flexBasis: "30%",
    flexGrow: 1,
    minWidth: 220,
    borderWidth: 1,
    borderRadius: 12,
    padding: 14,
    gap: 4,
  },
  icon: { fontSize: 28 },
});
