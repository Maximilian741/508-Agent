/**
 * Billing screen: monthly/annual subscription plans (for teams/high volume)
 * plus one-time credit packs.
 *
 * When real Stripe billing is configured (`/billing/config` -> enabled), the
 * buttons open Stripe Checkout / the Billing Portal. In a dev build without
 * Stripe keys, credit packs fall back to the local mock and subscriptions are
 * shown but explain that Stripe must be configured.
 */

import React, { useEffect, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import { useRouter } from "expo-router";

import {
  Account,
  BillingConfig,
  SubscriptionStatus,
  getBillingConfig,
  getSubscription,
  loadAccount,
  openBillingPortal,
  purchaseTier,
  refreshAccount,
  setOverage,
  startCreditCheckout,
  startSubscriptionCheckout,
} from "../src/domain/account";
import { Button } from "../src/ui/components/Button";
import { Card } from "../src/ui/components/Card";
import { Hero } from "../src/ui/components/Hero";
import { PixelIcon } from "../src/ui/components/PixelIcon";
import { Screen } from "../src/ui/components/Screen";
import { SignInModal } from "../src/ui/components/SignInModal";
import { useTheme } from "../src/ui/useTheme";
import { useToast } from "../src/ui/toast";

type Interval = "month" | "year";

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
  { key: "starter", name: "Starter", priceCents: 500, baseCredits: 50, bonusCredits: 0, features: ["50 audit credits", "All exporters and tools", "Email support"] },
  { key: "pro", name: "Pro", priceCents: 1500, baseCredits: 200, bonusCredits: 50, features: ["200 credits + 50 bonus", "Priority queue", "Batch mode unlocked", "Priority support"], highlight: true },
  { key: "studio", name: "Studio", priceCents: 5000, baseCredits: 1000, bonusCredits: 300, features: ["1000 credits + 300 bonus", "Multi-workspace seats", "Custom severity weights", "Direct line to remediators"] },
];

interface PlanFamily {
  family: "team" | "business";
  name: string;
  blurb: string;
  features: string[];
  highlight?: boolean;
  monthly: { key: string; priceCents: number };
  annual: { key: string; priceCents: number };
}

// Annual is billed at 10x the monthly price (two months free ≈ 17% off).
const PLAN_FAMILIES: PlanFamily[] = [
  {
    family: "team",
    name: "Team",
    blurb: "For small compliance teams",
    features: ["1,000 credits / month", "~200 PDFs or 330 DOCX monthly", "Conformance certificates included", "Priority queue", "Cancel anytime"],
    highlight: true,
    monthly: { key: "team", priceCents: 9900 },
    annual: { key: "team_annual", priceCents: 99000 },
  },
  {
    family: "business",
    name: "Business",
    blurb: "High-volume remediation",
    features: ["6,000 credits / month", "~1,200 PDFs monthly", "Everything in Team", "Batch mode + multi-seat", "Direct support"],
    monthly: { key: "business", priceCents: 49900 },
    annual: { key: "business_annual", priceCents: 499000 },
  },
];

function redirectTo(url: string) {
  if (Platform.OS === "web" && typeof window !== "undefined") {
    window.location.href = url;
  }
}

export default function BillingScreen() {
  const theme = useTheme();
  const router = useRouter();
  const toast = useToast();
  const { width } = useWindowDimensions();
  const [account, setAccount] = useState<Account | null>(() => loadAccount());
  const [signInOpen, setSignInOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [config, setConfig] = useState<BillingConfig | null>(null);
  const [subscription, setSubscription] = useState<SubscriptionStatus | null>(null);
  const [interval, setInterval] = useState<Interval>("month");

  const stacked = width < 880;
  const stripeEnabled = config?.enabled ?? false;

  useEffect(() => {
    let alive = true;
    void getBillingConfig().then((c) => { if (alive) setConfig(c); });
    if (loadAccount()) {
      void getSubscription().then((s) => { if (alive) setSubscription(s); });
    }
    return () => { alive = false; };
  }, []);

  const onChoose = async (tier: Tier) => {
    if (!account) { setSignInOpen(true); return; }
    setBusy(tier.key);
    try {
      if (stripeEnabled) {
        redirectTo(await startCreditCheckout(tier.key));
        return;
      }
      const total = tier.baseCredits + tier.bonusCredits;
      const next = await purchaseTier(tier.key);
      setAccount(next);
      void refreshAccount().then((fresh) => { if (fresh) setAccount(fresh); }).catch(() => {});
      toast.success("Credits added", { description: "+" + total + " credits from " + tier.name + " tier" });
      router.push("/account" as any);
    } catch (e: any) {
      toast.error("Purchase failed", { description: e?.message || "Could not reach the billing service." });
    } finally {
      setBusy(null);
    }
  };

  const onSubscribe = async (planKey: string) => {
    if (!account) { setSignInOpen(true); return; }
    if (!stripeEnabled) {
      toast.error("Subscriptions require Stripe", {
        description: "Set STRIPE_SECRET_KEY and the STRIPE_PRICE_* plan price ids to enable.",
      });
      return;
    }
    setBusy(planKey);
    try {
      redirectTo(await startSubscriptionCheckout(planKey));
    } catch (e: any) {
      toast.error("Couldn't start subscription", { description: e?.message || "Try again." });
    } finally {
      setBusy(null);
    }
  };

  const onManage = async () => {
    setBusy("manage");
    try {
      redirectTo(await openBillingPortal());
    } catch (e: any) {
      toast.error("Couldn't open billing portal", { description: e?.message || "Try again." });
    } finally {
      setBusy(null);
    }
  };

  const onToggleOverage = async () => {
    if (!subscription) return;
    const next = await setOverage(!subscription.overageEnabled);
    if (next) {
      setSubscription(next);
      toast.success(next.overageEnabled ? "Auto top-up enabled" : "Auto top-up disabled", {
        description: next.overageEnabled
          ? "If you run out mid-month we'll add a credit pack so you're never blocked."
          : "You'll be asked to buy credits when you run out.",
      });
    } else {
      toast.error("Couldn't update overage setting");
    }
  };

  return (
    <Screen scroll title="Plans & credits">
      <Hero
        shader="ember"
        eyebrow="BILLING"
        title="Plans & credits"
        subtitle="Subscribe for a monthly or annual allowance (best for teams) — conformance certificates included — or buy one-time credit packs."
      />

      {subscription?.active ? (
        <Card style={{ borderColor: theme.colors.accent, borderWidth: 2, marginTop: 16 }}>
          <View style={{ flexDirection: stacked ? "column" : "row", alignItems: stacked ? "flex-start" : "center", gap: 12 }}>
            <View style={{ flex: 1 }}>
              <Text style={[theme.typography.pixelLarge, { color: theme.colors.text }]}>
                {(subscription.plan || "").replace("_", " ")} plan active
              </Text>
              <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 4 }}>
                {subscription.monthlyCredits
                  ? subscription.monthlyCredits + " credits/month + free certificates."
                  : "Your allowance refills automatically + free certificates."}
                {subscription.currentPeriodEnd ? "  Renews " + new Date(subscription.currentPeriodEnd).toLocaleDateString() + "." : ""}
              </Text>
              <Pressable
                onPress={onToggleOverage}
                accessibilityRole="button"
                accessibilityLabel="Toggle automatic overage top-up"
                style={{ marginTop: 8, alignSelf: "flex-start" }}
              >
                <Text style={{ color: theme.colors.accent, fontSize: 13, fontWeight: "700" }}>
                  Auto top-up if you run out: {subscription.overageEnabled ? "ON" : "OFF"} · tap to {subscription.overageEnabled ? "disable" : "enable"}
                </Text>
              </Pressable>
              <Pressable
                onPress={() => router.push("/team" as any)}
                accessibilityRole="button"
                accessibilityLabel="Manage your team"
                style={{ marginTop: 6, alignSelf: "flex-start" }}
              >
                <Text style={{ color: theme.colors.accent, fontSize: 13, fontWeight: "700" }}>
                  Share your plan with your team · manage seats →
                </Text>
              </Pressable>
            </View>
            <Button title={busy === "manage" ? "Opening..." : "Manage subscription"} onPress={onManage} variant="secondary" loading={busy === "manage"} disabled={busy === "manage"} />
          </View>
        </Card>
      ) : null}

      <View style={{ flexDirection: stacked ? "column" : "row", alignItems: stacked ? "flex-start" : "flex-end", justifyContent: "space-between", marginTop: 28, gap: 8 }}>
        <View style={{ flex: 1 }}>
          <Text style={[theme.typography.displaySmall, { color: theme.colors.text }]}>Subscription plans</Text>
          <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 4 }}>
            Best value for ongoing 508 / ADA compliance. Certificates included. Cancel anytime.
          </Text>
        </View>
        <IntervalToggle interval={interval} onChange={setInterval} theme={theme} />
      </View>

      <View style={[styles.grid, stacked && styles.gridStacked, { marginTop: 12 }]}>
        {PLAN_FAMILIES.map((fam) => {
          const v = interval === "year" ? fam.annual : fam.monthly;
          return (
            <View key={fam.family} style={[styles.tierWrap, stacked ? styles.tierStacked : styles.tierFlex]}>
              <PlanCard family={fam} interval={interval} busy={busy === v.key} onSubscribe={() => onSubscribe(v.key)} />
            </View>
          );
        })}
      </View>

      <Text style={[theme.typography.displaySmall, { color: theme.colors.text, marginTop: 32 }]}>One-time credit packs</Text>
      <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginTop: 4, marginBottom: 12 }}>No commitment — credits never expire.</Text>
      <View style={[styles.grid, stacked && styles.gridStacked]}>
        {TIERS.map((tier) => (
          <View key={tier.key} style={[styles.tierWrap, stacked ? styles.tierStacked : styles.tierFlex]}>
            <TierCard tier={tier} busy={busy === tier.key} onChoose={() => onChoose(tier)} />
          </View>
        ))}
      </View>

      {!stripeEnabled ? (
        <Text style={{ color: theme.colors.textMuted, fontSize: 12, textAlign: "center", marginTop: 24, paddingHorizontal: 16 }}>
          Stripe is not configured in this build - credit packs add local test credits, and subscriptions need Stripe keys.
        </Text>
      ) : null}

      <SignInModal open={signInOpen} onCancel={() => setSignInOpen(false)} />
    </Screen>
  );
}

function IntervalToggle({ interval, onChange, theme }: { interval: Interval; onChange: (i: Interval) => void; theme: ReturnType<typeof useTheme> }) {
  return (
    <View style={{ flexDirection: "row", alignSelf: "flex-start", borderWidth: 1, borderColor: theme.colors.border, borderRadius: 999, padding: 3 }}>
      {(["month", "year"] as Interval[]).map((opt) => {
        const active = interval === opt;
        return (
          <Pressable
            key={opt}
            accessibilityRole="button"
            accessibilityLabel={opt === "month" ? "Monthly billing" : "Annual billing"}
            onPress={() => onChange(opt)}
            style={{ paddingHorizontal: 14, paddingVertical: 6, borderRadius: 999, backgroundColor: active ? theme.colors.accent : "transparent" }}
          >
            <Text style={{ color: active ? "#FFFFFF" : theme.colors.textMuted, fontWeight: "700", fontSize: 13 }}>
              {opt === "month" ? "Monthly" : "Annual · save 17%"}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

function PlanCard({ family, interval, busy, onSubscribe }: { family: PlanFamily; interval: Interval; busy: boolean; onSubscribe: () => void }) {
  const theme = useTheme();
  const v = interval === "year" ? family.annual : family.monthly;
  const dollars = (v.priceCents / 100).toFixed(0);
  const perLabel = interval === "year" ? "/ year" : "/ month";
  const monthlyEquiv = interval === "year" ? Math.round(v.priceCents / 12 / 100) : Math.round(v.priceCents / 100);
  const borderColor = family.highlight ? theme.colors.accent : theme.colors.border;
  return (
    <Card style={{ borderColor, borderWidth: family.highlight ? 2 : 1 }}>
      {family.highlight ? (
        <View style={[styles.badge, { backgroundColor: theme.colors.accent + "22" }]}>
          <Text style={{ color: theme.colors.accent, fontSize: 10, fontWeight: "800", letterSpacing: 0.6 }}>BEST FOR TEAMS</Text>
        </View>
      ) : null}
      <Text style={[theme.typography.pixelLarge, { color: theme.colors.text }]}>{family.name}</Text>
      <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 2 }}>{family.blurb}</Text>
      <View style={styles.priceRow}>
        <Text style={[theme.typography.display, { color: theme.colors.text }]}>${dollars}</Text>
        <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginLeft: 6, marginBottom: 8 }}>{perLabel}</Text>
      </View>
      {interval === "year" ? (
        <Text style={{ color: theme.colors.accent, fontWeight: "700", fontSize: 13, marginBottom: 8 }}>
          ${monthlyEquiv}/mo billed yearly · save ~17%
        </Text>
      ) : null}
      <View style={styles.features}>
        {family.features.map((f, i) => (
          <View key={i} style={styles.featureRow}>
            <View style={[styles.bullet, { backgroundColor: theme.colors.accent }]} />
            <Text style={{ color: theme.colors.text, fontSize: 13, flex: 1 }}>{f}</Text>
          </View>
        ))}
      </View>
      <View style={{ marginTop: 16 }}>
        <Button title={busy ? "Working..." : "Subscribe"} onPress={onSubscribe} variant={family.highlight ? "primary" : "secondary"} loading={busy} disabled={busy} />
      </View>
    </Card>
  );
}

function TierCard({ tier, busy, onChoose }: { tier: Tier; busy: boolean; onChoose: () => void }) {
  const theme = useTheme();
  const total = tier.baseCredits + tier.bonusCredits;
  const dollars = (tier.priceCents / 100).toFixed(0);
  const borderColor = tier.highlight ? theme.colors.accent : theme.colors.border;
  return (
    <Card style={{ borderColor, borderWidth: tier.highlight ? 2 : 1 }}>
      {tier.highlight ? (
        <View style={[styles.badge, { backgroundColor: theme.colors.accent + "22" }]}>
          <Text style={{ color: theme.colors.accent, fontSize: 10, fontWeight: "800", letterSpacing: 0.6 }}>MOST POPULAR</Text>
        </View>
      ) : null}
      <Text style={[theme.typography.pixelLarge, { color: theme.colors.text }]}>{tier.name}</Text>
      <View style={styles.priceRow}>
        <View style={{ marginRight: 8, marginBottom: 8 }}>
          <PixelIcon name="coin" size={4} color={theme.colors.accent} />
        </View>
        <Text style={[theme.typography.display, { color: theme.colors.text }]}>${dollars}</Text>
        <Text style={{ color: theme.colors.textMuted, fontSize: 13, marginLeft: 6, marginBottom: 8 }}>one-time</Text>
      </View>
      <Text style={{ color: theme.colors.accent, fontWeight: "700", fontSize: 14, marginBottom: 12 }}>
        {total} credits{tier.bonusCredits > 0 ? "  (" + tier.baseCredits + " + " + tier.bonusCredits + " bonus)" : ""}
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
        <Button title={busy ? "Working..." : "Choose"} onPress={onChoose} variant={tier.highlight ? "primary" : "secondary"} loading={busy} disabled={busy} />
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  grid: { flexDirection: "row", gap: 16, marginTop: 4 },
  gridStacked: { flexDirection: "column" },
  tierWrap: {},
  tierFlex: { flex: 1 },
  tierStacked: { width: "100%" },
  badge: { alignSelf: "flex-start", paddingHorizontal: 8, paddingVertical: 4, borderRadius: 999, marginBottom: 8 },
  priceRow: { flexDirection: "row", alignItems: "flex-end", marginTop: 4 },
  features: { marginTop: 4, gap: 8 },
  featureRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  bullet: { width: 6, height: 6, borderRadius: 3 },
});
