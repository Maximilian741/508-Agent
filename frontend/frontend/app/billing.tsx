/**
 * Billing / buy-credits screen.
 *
 * Three tiers in a horizontal row (stacked on narrow widths). Each card is
 * a serif tier name, a big price, an inclusion list, and a "Choose" button
 * that mocks the purchase and routes back to /account with a success toast.
 *
 * No real payment integration is wired. The purchase just calls addCredits
 * locally; this surface is meant to be swapped for a real checkout when
 * billing is hooked up.
 */

import React, { useState } from "react";
import { Platform, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import { useRouter } from "expo-router";

import {
  Account,
  loadAccount,
  purchaseTier,
  refreshAccount,
} from "../src/domain/account";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Hero } from "../src/ui/components/Hero";
import { PixelIcon } from "../src/ui/components/PixelIcon";
import { Screen } from "../src/ui/components/Screen";
import { SignInModal } from "../src/ui/components/SignInModal";
import { useTheme } from "../src/ui/useTheme";
import { useToast } from "../src/ui/toast";

interface Tier {
  key: "starter" | "pro" | "studio";
  name: string;
  priceCents: number;
  baseCredits: number;
  bonusCredits: number;
  features: string[];
  highlight?: boolean;
}

const TIERS: Tier[] = [
  {
    key: "starter",
    name: "Starter",
    priceCents: 500,
    baseCredits: 50,
    bonusCredits: 0,
    features: [
      "50 audit credits",
      "All exporters and tools",
      "Email support",
    ],
  },
  {
    key: "pro",
    name: "Pro",
    priceCents: 1500,
    baseCredits: 200,
    bonusCredits: 50,
    features: [
      "200 credits + 50 bonus",
      "Priority queue",
      "Batch mode unlocked",
      "Priority support",
    ],
    highlight: true,
  },
  {
    key: "studio",
    name: "Studio",
    priceCents: 5000,
    baseCredits: 1000,
    bonusCredits: 300,
    features: [
      "1000 credits + 300 bonus",
      "Multi-workspace seats",
      "Custom severity weights",
      "Direct line to remediators",
    ],
  },
];

export default function BillingScreen() {
  const theme = useTheme();
  const router = useRouter();
  const toast = useToast();
  const { width } = useWindowDimensions();
  const [account, setAccount] = useState<Account | null>(() => loadAccount());
  const [signInOpen, setSignInOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const stacked = width < 880;

  const onChoose = async (tier: Tier) => {
    if (!account) {
      setSignInOpen(true);
      return;
    }
    setBusy(tier.key);
    const total = tier.baseCredits + tier.bonusCredits;
    try {
      const next = await purchaseTier(tier.key);
      setAccount(next);
      // Refresh from /auth/me so the local cache stays canonical even if
      // the purchase response shape ever drifts.
      void refreshAccount()
        .then((fresh) => {
          if (fresh) setAccount(fresh);
        })
        .catch((err) => {
          console.warn("[billing] post-purchase refresh failed", err);
        });
      toast.success("Credits added", {
        description: "+" + total + " credits from " + tier.name + " tier",
      });
      router.push("/account" as any);
    } catch (e: any) {
      console.warn("[billing] purchase failed", e);
      toast.error("Purchase failed", {
        description: e?.message || "Could not reach the billing service. Try again.",
      });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Screen scroll title="Buy credits">
      <Hero
        shader="ember"
        eyebrow="BILLING"
        title="Buy credits"
        subtitle="Credits power audits, batch runs, and AI remediation. Pick a tier - bigger packs include bonus credits."
      />

      <View style={[styles.grid, stacked && styles.gridStacked]}>
        {TIERS.map((tier) => (
          <View
            key={tier.key}
            style={[styles.tierWrap, stacked ? styles.tierStacked : styles.tierFlex]}
          >
            <TierCard
              tier={tier}
              busy={busy === tier.key}
              onChoose={() => onChoose(tier)}
            />
          </View>
        ))}
      </View>

      <Text
        style={{
          color: theme.colors.textMuted,
          fontSize: 12,
          textAlign: "center",
          marginTop: 24,
          paddingHorizontal: 16,
        }}
      >
        Real billing not enabled in this build. Purchases are local-only test credits.
      </Text>

      <SignInModal open={signInOpen} onCancel={() => setSignInOpen(false)} />
    </Screen>
  );
}

function TierCard({
  tier,
  busy,
  onChoose,
}: {
  tier: Tier;
  busy: boolean;
  onChoose: () => void;
}) {
  const theme = useTheme();
  const total = tier.baseCredits + tier.bonusCredits;
  const dollars = (tier.priceCents / 100).toFixed(0);
  const borderColor = tier.highlight ? theme.colors.accent : theme.colors.border;
  return (
    <Card style={{ borderColor, borderWidth: tier.highlight ? 2 : 1 }}>
      {tier.highlight ? (
        <View
          style={[
            styles.badge,
            { backgroundColor: theme.colors.accent + "22" },
          ]}
        >
          <Text style={{ color: theme.colors.accent, fontSize: 10, fontWeight: "800", letterSpacing: 0.6 }}>
            MOST POPULAR
          </Text>
        </View>
      ) : null}

      <Text style={[theme.typography.pixelLarge, { color: theme.colors.text }]}>
        {tier.name}
      </Text>

      <View style={styles.priceRow}>
        <View style={{ marginRight: 8, marginBottom: 8 }}>
          <PixelIcon name="coin" size={4} color={theme.colors.accent} />
        </View>
        <Text style={[theme.typography.display, { color: theme.colors.text }]}>
          ${dollars}
        </Text>
        <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginLeft: 6, marginBottom: 8 }}>
          one-time
        </Text>
      </View>

      <Text style={{ color: theme.colors.accent, fontWeight: "700", fontSize: 14, marginBottom: 12 }}>
        {total} credits
        {tier.bonusCredits > 0 ? "  (" + tier.baseCredits + " + " + tier.bonusCredits + " bonus)" : ""}
      </Text>

      <View style={styles.features}>
        {tier.features.map((f, i) => (
          <View key={i} style={styles.featureRow}>
            <View style={[styles.bullet, { backgroundColor: theme.colors.accent }]} />
            <Text style={{ color: theme.colors.text, fontSize: 13, flex: 1 }}>{f}</Text>
          </View>
        ))}
      </View>

      <View style={{ marginTop: 16 }}>
        <Button
          title={busy ? "Working..." : "Choose"}
          onPress={onChoose}
          variant={tier.highlight ? "primary" : "secondary"}
          loading={busy}
          disabled={busy}
        />
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  grid: {
    flexDirection: "row",
    gap: 16,
    marginTop: 16,
  },
  gridStacked: {
    flexDirection: "column",
  },
  tierWrap: {},
  tierFlex: {
    flex: 1,
  },
  tierStacked: {
    width: "100%",
  },
  badge: {
    alignSelf: "flex-start",
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 999,
    marginBottom: 8,
  },
  priceRow: {
    flexDirection: "row",
    alignItems: "flex-end",
    marginTop: 4,
  },
  features: {
    marginTop: 4,
    gap: 8,
  },
  featureRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  bullet: {
    width: 6,
    height: 6,
    borderRadius: 3,
  },
});
