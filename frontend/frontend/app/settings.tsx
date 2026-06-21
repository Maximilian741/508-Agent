/**
 * Settings — backend URL configuration and Demo Mode toggle.
 *
 * Demo Mode is the renamed-and-explained "mockMode" toggle.  In testing,
 * users couldn't tell whether they were looking at real findings or fake
 * data; the new copy spells it out.
 */

import { useEffect, useState } from "react";
import { Image, Platform, Pressable, StyleSheet, Switch, Text, TextInput, View, useColorScheme } from "react-native";

import { clearHistory } from "../src/domain/auditHistory";
import {
  clearDemoData,
  hasDemoData,
  loadDemoData,
  nukeDemoData,
} from "../src/domain/demoSeed";
import { DEFAULT_WEIGHTS, loadWeights, resetWeights, saveWeights } from "../src/domain/scoreWeights";
import {
  hasBranding,
  loadBranding,
  saveBranding,
  type ReportBranding,
} from "../src/domain/reportBranding";
import {
  createApiKey,
  listApiKeys,
  revokeApiKey,
  type ApiKeyDTO,
  type CreatedApiKeyDTO,
} from "../src/domain/apiKeys";
import { loadToken } from "../src/domain/account";
import {
  notificationsAvailable,
  notificationsEnabled,
  requestPermission,
  setNotificationsEnabled,
} from "../src/domain/notifications";
import { useAppStore } from "../src/store/useAppStore";
import { Button } from "../src/ui/components/Button";
import { openSystemCheck } from "../src/ui/components/SystemCheckWizard";
import { Card } from "../src/ui/components/Card";
import { PixelIcon } from "../src/ui/components/PixelIcon";
import { Chip } from "../src/ui/components/Chip";
import { InlineNotice } from "../src/ui/components/InlineNotice";
import { Screen } from "../src/ui/components/Screen";
import { Hero } from "../src/ui/components/Hero";
import { useToast } from "../src/ui/toast";
import { useTheme } from "../src/ui/useTheme";

export default function SettingsScreen() {
  const theme = useTheme();
  const toast = useToast();
  const apiBaseUrl = useAppStore((state) => state.apiBaseUrl);
  const mockMode = useAppStore((state) => state.mockMode);
  const freeScansUsed = useAppStore((state) => state.freeScansUsed);
  const setFreeScansUsed = useAppStore((state) => state.setFreeScansUsed);
  const bypassFreeScanGate = useAppStore((state) => state.bypassFreeScanGate);
  const setBypassFreeScanGate = useAppStore((state) => state.setBypassFreeScanGate);
  const backendUrlWarning = useAppStore((state) => state.backendUrlWarning);
  const backendHealth = useAppStore((state) => state.backendHealth);
  const backendHealthMessage = useAppStore((state) => state.backendHealthMessage);
  const themeMode = useAppStore((state) => state.themeMode);
  const setThemeMode = useAppStore((state) => state.setThemeMode);
  const setApiBaseUrl = useAppStore((state) => state.setApiBaseUrl);
  const saveApiBaseUrl = useAppStore((state) => state.saveApiBaseUrl);
  const setMockMode = useAppStore((state) => state.setMockMode);
  const backendUrlSource = useAppStore((state) => state.backendUrlSource);
  const [draftUrl, setDraftUrl] = useState(apiBaseUrl);
  const [notifyOn, setNotifyOn] = useState(notificationsEnabled());
  // White-label report branding (agencies put their own brand on deliverables).
  const [branding, setBranding] = useState<ReportBranding>(() => loadBranding());
  const systemScheme = useColorScheme();

  const pickLogo = () => {
    if (Platform.OS !== "web" || typeof document === "undefined") return;
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/png,image/jpeg,image/gif,image/webp,image/svg+xml";
    input.onchange = () => {
      const file = input.files && input.files[0];
      if (!file) return;
      if (file.size > 512 * 1024) {
        toast.error("Logo too large", { description: "Please use an image under 512 KB." });
        return;
      }
      const reader = new FileReader();
      reader.onload = () => setBranding((b) => ({ ...b, logoDataUrl: String(reader.result || "") }));
      reader.readAsDataURL(file);
    };
    input.click();
  };

  const saveBrandingNow = () => {
    saveBranding(branding);
    toast.success("Report branding saved", {
      description: hasBranding(branding)
        ? "Your reports now carry your brand."
        : "Branding cleared. Reports use the default style.",
    });
  };

  // --- Developer API keys ---------------------------------------------------
  const signedIn = !!loadToken();
  const [apiKeys, setApiKeys] = useState<ApiKeyDTO[]>([]);
  const [apiKeysError, setApiKeysError] = useState<string | null>(null);
  const [keyName, setKeyName] = useState("");
  const [creatingKey, setCreatingKey] = useState(false);
  const [justCreatedKey, setJustCreatedKey] = useState<CreatedApiKeyDTO | null>(null);

  useEffect(() => {
    if (!signedIn || mockMode) return;
    let alive = true;
    listApiKeys(apiBaseUrl)
      .then((ks) => { if (alive) setApiKeys(ks); })
      .catch((e) => { if (alive) setApiKeysError((e as Error).message); });
    return () => { alive = false; };
  }, [signedIn, mockMode, apiBaseUrl]);

  const onCreateKey = async () => {
    setCreatingKey(true);
    setApiKeysError(null);
    try {
      const created = await createApiKey(apiBaseUrl, keyName.trim() || "API key");
      setJustCreatedKey(created);
      setKeyName("");
      setApiKeys((prev) => [created, ...prev]);
      toast.success("API key created", { description: "Copy it now; you won't see it again." });
    } catch (e) {
      setApiKeysError((e as Error).message);
      toast.error("Couldn't create API key", { description: (e as Error).message });
    } finally {
      setCreatingKey(false);
    }
  };

  const onRevokeKey = async (id: string) => {
    try {
      const updated = await revokeApiKey(apiBaseUrl, id);
      setApiKeys((prev) => prev.map((k) => (k.id === id ? updated : k)));
      if (justCreatedKey?.id === id) setJustCreatedKey(null);
      toast.success("API key revoked");
    } catch (e) {
      toast.error("Couldn't revoke key", { description: (e as Error).message });
    }
  };

  const copyKey = async (value: string) => {
    if (Platform.OS !== "web") return;
    try {
      await navigator.clipboard.writeText(value);
      toast.success("Copied to clipboard");
    } catch {
      toast.error("Couldn't copy — select it and copy manually.");
    }
  };
  // Operator/dev affordances (free-scan bypass, analyzer URL override, AI
  // provider notes) are hidden on managed builds: customers on the hosted
  // product must never see a "bypass the paywall" switch or be told to run
  // dev_run.py. They appear in dev builds, or when no env API URL is baked
  // (self-hosted operators pointing the UI at their own backend).
  const showOperatorTools = __DEV__ || backendUrlSource !== "env";

  return (
    <Screen scroll title="Settings">
      <Hero
        eyebrow="SETTINGS"
        title="Settings"
        subtitle="Where the analyzer lives, and whether you're working with real or fake data."
        rightSlot={
          <Chip
            label={mockMode ? "Demo data" : "Live data"}
            tone={mockMode ? "warning" : "success"}
          />
        }
      />

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="bolt" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Report branding</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Put your own logo, name, and colour on the conformance &amp; remediation reports you download.
          Hand clients a deliverable under your brand. (The methodology text always keeps a small
          “automated testing by 508 Agent” line.)
        </Text>

        <Text style={[styles.brandLabel, { color: theme.colors.textMuted }]}>Organization name</Text>
        <TextInput
          value={branding.orgName}
          onChangeText={(v) => setBranding((b) => ({ ...b, orgName: v }))}
          style={[styles.input, { borderColor: theme.colors.border, color: theme.colors.text, borderRadius: theme.radius.xs }]}
          placeholder="Sunriver Consulting"
          placeholderTextColor={theme.colors.textMuted}
        />

        <Text style={[styles.brandLabel, { color: theme.colors.textMuted }]}>Contact line (optional)</Text>
        <TextInput
          value={branding.contact}
          onChangeText={(v) => setBranding((b) => ({ ...b, contact: v }))}
          style={[styles.input, { borderColor: theme.colors.border, color: theme.colors.text, borderRadius: theme.radius.xs }]}
          placeholder="access@sunriver.com · sunriver.com"
          placeholderTextColor={theme.colors.textMuted}
        />

        <Text style={[styles.brandLabel, { color: theme.colors.textMuted }]}>Accent colour (hex)</Text>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
          <TextInput
            value={branding.accent}
            onChangeText={(v) => setBranding((b) => ({ ...b, accent: v }))}
            style={[styles.input, { flex: 1, borderColor: theme.colors.border, color: theme.colors.text, borderRadius: theme.radius.xs }]}
            placeholder="#2D5BFF"
            placeholderTextColor={theme.colors.textMuted}
            autoCapitalize="none"
          />
          {/^#[0-9a-fA-F]{6}$/.test(branding.accent.trim()) ? (
            <View style={{ width: 36, height: 36, borderRadius: theme.radius.xs, backgroundColor: branding.accent.trim(), borderWidth: 1, borderColor: theme.colors.border }} />
          ) : null}
        </View>

        <Text style={[styles.brandLabel, { color: theme.colors.textMuted }]}>Logo</Text>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
          <Button title={branding.logoDataUrl ? "Change logo" : "Upload logo"} onPress={pickLogo} variant="secondary" />
          {branding.logoDataUrl ? (
            <>
              <Image
                source={{ uri: branding.logoDataUrl }}
                style={{ height: 40, width: 120, resizeMode: "contain" }}
                accessibilityLabel="Current logo preview"
              />
              <Pressable accessibilityRole="button" accessibilityLabel="Remove logo" onPress={() => setBranding((b) => ({ ...b, logoDataUrl: "" }))}>
                <Text style={[theme.typography.body, { color: theme.colors.danger, fontWeight: "700", fontSize: 13 }]}>Remove</Text>
              </Pressable>
            </>
          ) : (
            <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>PNG/SVG, under 512 KB</Text>
          )}
        </View>

        <View style={styles.buttonRow}>
          <Button title="Save branding" onPress={saveBrandingNow} />
          {hasBranding(branding) ? (
            <Button
              title="Clear"
              onPress={() => {
                setBranding({ orgName: "", logoDataUrl: "", accent: "", contact: "" });
                saveBranding({ orgName: "", logoDataUrl: "", accent: "", contact: "" });
                toast.success("Branding cleared");
              }}
              variant="ghost"
            />
          ) : null}
        </View>
      </Card>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="bolt" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Developer API</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Scan documents for accessibility issues programmatically, and wire it into your CI or build pipeline.
          Create a key and POST a file to <Text style={[theme.typography.mono, { color: theme.colors.text }]}>/pipeline/analyze</Text>.
          Keys are scan-only: they can't spend credits or change your account.
        </Text>

        {!signedIn ? (
          <InlineNotice tone="info" title="Sign in first" message="Create a free account to generate API keys." />
        ) : (
          <>
            <View style={[styles.codeBlock, { backgroundColor: theme.colors.surface2, borderColor: theme.colors.border, borderRadius: theme.radius.xs }]}>
              <Text style={[theme.typography.mono, { color: theme.colors.text, fontSize: 12 }]}>
                {`curl -X POST ${apiBaseUrl}/pipeline/analyze \\\n  -H "X-API-Key: ak_live_…" \\\n  -F "file=@report.pdf"`}
              </Text>
            </View>

            {justCreatedKey ? (
              <View style={[styles.newKeyBox, { borderColor: theme.colors.success, backgroundColor: theme.colors.success + "12", borderRadius: theme.radius.xs }]}>
                <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "700" }]}>
                  Your new API key: copy it now
                </Text>
                <Text style={[theme.typography.caption, { color: theme.colors.textMuted, marginBottom: 6 }]}>
                  This is the only time we'll show it. Store it somewhere safe.
                </Text>
                <Text selectable style={[theme.typography.mono, { color: theme.colors.text, fontSize: 12 }]}>
                  {justCreatedKey.key}
                </Text>
                <View style={[styles.buttonRow, { marginTop: 8 }]}>
                  <Button title="Copy key" onPress={() => copyKey(justCreatedKey.key)} variant="secondary" />
                  <Button title="Done" onPress={() => setJustCreatedKey(null)} variant="ghost" />
                </View>
              </View>
            ) : null}

            <Text style={[styles.brandLabel, { color: theme.colors.textMuted }]}>New key name</Text>
            <View style={{ flexDirection: "row", gap: 10, alignItems: "center" }}>
              <TextInput
                value={keyName}
                onChangeText={setKeyName}
                style={[styles.input, { flex: 1, borderColor: theme.colors.border, color: theme.colors.text, marginTop: 0, borderRadius: theme.radius.xs }]}
                placeholder="CI pipeline"
                placeholderTextColor={theme.colors.textMuted}
              />
              <Button title={creatingKey ? "Creating…" : "Create key"} onPress={onCreateKey} loading={creatingKey} disabled={creatingKey} />
            </View>

            {apiKeysError ? <InlineNotice tone="danger" title="API keys" message={apiKeysError} /> : null}

            {apiKeys.length > 0 ? (
              <View style={{ marginTop: 12, gap: 6 }}>
                {apiKeys.map((k) => (
                  <View key={k.id} style={[styles.keyRow, { borderColor: theme.colors.border, borderRadius: theme.radius.none }]}>
                    <View style={{ flex: 1 }}>
                      <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "600", fontSize: 14 }]}>
                        {k.name}{" "}
                        <Text style={[theme.typography.mono, { color: theme.colors.textMuted, fontSize: 12 }]}>{k.keyPrefix}…</Text>
                      </Text>
                      <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                        Created {new Date(k.createdAt).toLocaleDateString()}
                        {k.lastUsedAt ? ` · last used ${new Date(k.lastUsedAt).toLocaleDateString()}` : " · never used"}
                      </Text>
                    </View>
                    {k.revoked ? (
                      <Chip label="Revoked" tone="default" />
                    ) : (
                      <Pressable accessibilityRole="button" accessibilityLabel={`Revoke ${k.name}`} onPress={() => onRevokeKey(k.id)}>
                        <Text style={[theme.typography.body, { color: theme.colors.danger, fontWeight: "700", fontSize: 13 }]}>Revoke</Text>
                      </Pressable>
                    )}
                  </View>
                ))}
              </View>
            ) : null}
          </>
        )}
      </Card>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="play" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Demo Mode</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          When Demo Mode is on, the app shows pre-baked sample data instead of calling the
          analyzer service. It's useful for exploring the UI without setting up the backend, but
          findings and scores in Demo Mode are <Text style={{ fontWeight: "700" }}>not real</Text>.
          Don't rely on them for an audit.
        </Text>
        <View style={styles.toggleRow}>
          <Text style={[theme.typography.body, { color: theme.colors.text }]}>
            {mockMode ? "Demo Mode is ON" : "Demo Mode is OFF (live)"}
          </Text>
          <Switch value={mockMode} onValueChange={setMockMode} accessibilityLabel="Demo Mode" />
        </View>
      </Card>

      {__DEV__ ? (
        <Card>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <PixelIcon name="key" size={3} color={theme.colors.accent} />
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Free-scan gate (dev only)</Text>
          </View>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
            New visitors get one free scan, then the app prompts them to sign in
            before the second upload or any download. Flip the bypass below to
            test the full flow without burning your free scan every time. This
            card is only visible in development builds.
          </Text>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 6 }]}>
            Free scans used so far: {freeScansUsed}
          </Text>
          <View style={styles.toggleRow}>
            <Text style={[theme.typography.body, { color: theme.colors.text }]}>
              {bypassFreeScanGate ? "Bypass is ON (gate skipped)" : "Bypass is OFF (gate active)"}
            </Text>
            <Switch
              value={bypassFreeScanGate}
              onValueChange={setBypassFreeScanGate}
              accessibilityLabel="Bypass the free-scan sign-in gate (dev only)"
            />
          </View>
          <View style={[styles.buttonRow, { marginTop: 8 }]}>
            <Button
              title="Reset free-scan counter"
              variant="ghost"
              disabled={freeScansUsed === 0}
              onPress={() => {
                setFreeScansUsed(0);
                toast.info("Free-scan counter reset");
              }}
            />
          </View>
          {bypassFreeScanGate ? (
            <InlineNotice
              tone="warning"
              title="Bypass is on"
              message="The sign-in gate is disabled. Turn this off before shipping to real users."
            />
          ) : null}
        </Card>
      ) : null}

      {showOperatorTools ? (
      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="bolt" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Analyzer service URL</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          The Python backend writes its own URL to{" "}
          <Text style={[theme.typography.mono, { color: theme.colors.text }]}>
            backend/.runtime/backend_url.txt
          </Text>{" "}
          when you run <Text style={[theme.typography.mono, { color: theme.colors.text }]}>python dev_run.py</Text>.
          You only need to override it here if you're running the backend on a different host.
        </Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 6 }]}>
          Currently using: {apiBaseUrl}
        </Text>
        <TextInput
          value={draftUrl}
          onChangeText={setDraftUrl}
          style={[
            styles.input,
            { borderColor: theme.colors.border, color: theme.colors.text, borderRadius: theme.radius.xs },
          ]}
          placeholder="http://127.0.0.1:8000"
          placeholderTextColor={theme.colors.textMuted}
        />
        <View style={styles.buttonRow}>
          <Button title="Save & test" onPress={() => saveApiBaseUrl(draftUrl)} />
          <Button title="Save without testing" onPress={() => setApiBaseUrl(draftUrl)} variant="ghost" />
        </View>
        {!mockMode && backendHealth === "error" && (
          <InlineNotice
            title="Analyzer is unreachable"
            message={
              backendHealthMessage ??
              backendUrlWarning ??
              "Check that your backend is running and reachable at the URL above, then Save & test."
            }
            tone="danger"
          />
        )}
        {!mockMode && backendHealth === "ok" && (
          <InlineNotice
            title="Connected"
            message={`Successfully reached ${apiBaseUrl}.`}
            tone="success"
          />
        )}
      </Card>
      ) : null}

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="star" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Appearance</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Three distinct looks: dusky Twilight (default), warm Light parchment, or deep Dark walnut.
        </Text>
        <View style={styles.themeRow}>
          {(["system", "light", "dark"] as const).map((mode) => (
            <Pressable accessibilityRole="button"
              key={mode}
              onPress={() => setThemeMode(mode)}
              accessibilityLabel={`Set theme to ${mode === "system" ? "twilight" : mode}`}
              style={[
                styles.themeChoice,
                {
                  borderColor: themeMode === mode ? theme.colors.accent : theme.colors.border,
                  backgroundColor: themeMode === mode ? theme.colors.accent + "22" : theme.colors.surface,
                  borderRadius: theme.radius.sm,
                },
              ]}
            >
              <Text
                style={[
                  theme.typography.body,
                  {
                    color: themeMode === mode ? theme.colors.accent : theme.colors.text,
                    fontWeight: themeMode === mode ? "700" : "500",
                  },
                ]}
              >
                {mode === "system" ? "Twilight" : mode === "light" ? "Light" : "Dark"}
              </Text>
            </Pressable>
          ))}
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted, marginTop: 8, fontSize: 12 }]}>
          Twilight is a dusky violet/peach palette - the default. Pick Light for cream parchment, Dark for deep walnut. (Your OS is currently {systemScheme === "dark" ? "Dark" : "Light"}; Twilight ignores it on purpose so all three options look different.)
        </Text>
      </Card>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="spark" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Notifications</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Get a browser notification and a soft chime when an audit finishes. Useful when you've
          switched tabs while a long document is being analyzed.
        </Text>
        <View style={styles.toggleRow}>
          <Text style={[theme.typography.body, { color: theme.colors.text }]}>
            {notifyOn
              ? "Audit-complete notifications: ON"
              : notificationsAvailable()
              ? "Audit-complete notifications: OFF"
              : "Notifications aren't supported in this browser"}
          </Text>
          <Switch
            value={notifyOn}
            disabled={!notificationsAvailable()}
            accessibilityLabel="Desktop notifications"
            onValueChange={async (next) => {
              if (next) {
                const status = await requestPermission();
                if (status === "granted") {
                  setNotificationsEnabled(true);
                  setNotifyOn(true);
                  toast.success("Notifications enabled");
                } else {
                  toast.warning("Permission not granted", {
                    description: "Enable notifications in your browser settings, then try again.",
                  });
                }
              } else {
                setNotificationsEnabled(false);
                setNotifyOn(false);
                toast.info("Notifications disabled");
              }
            }}
          />
        </View>
      </Card>

      <Card>
        <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Score weighting</Text>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Tune how much each severity contributes to the live audit score. Defaults match the
          backend formula. Set Info to 1 if you want informational findings to count, or set
          Warning to 2 if your team treats warnings as effectively as serious as errors.
        </Text>
        <ScoreWeightControls />
      </Card>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="coin" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Demo data</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Populate the dashboard, history, and achievements with a realistic
          set of fixture audits so you can poke around without uploading any
          documents. Seeded rows live in localStorage and can be removed with
          one click.
        </Text>
        <View style={[styles.buttonRow, { marginTop: 8 }]}>
          <Button
            title="Load demo data"
            onPress={() => {
              const r = loadDemoData({ mode: "merge" });
              toast.success(
                `Loaded ${r.historyAdded} audits, ${r.achievementsUnlocked} new badges, ${r.workspacesCreated} workspaces.`,
              );
            }}
          />
          <Button
            title={hasDemoData() ? "Clear demo data" : "Clear demo (none)"}
            variant="ghost"
            disabled={!hasDemoData()}
            onPress={() => {
              const r = clearDemoData();
              toast.info(`Removed ${r.removed} demo audits.`);
            }}
          />
          <Button
            title="Reset everything"
            variant="ghost"
            onPress={() => {
              if (
                typeof window !== "undefined" &&
                !window.confirm(
                  "Reset all demo data, achievements, and seeded workspaces? Real audits and your own workspaces are kept.",
                )
              ) {
                return;
              }
              nukeDemoData();
              toast.info("Demo data, achievements, and seeded workspaces reset.");
            }}
          />
        </View>
      </Card>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="doc" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Audit history</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          A list of every audit you've run is kept locally so the home page can show recent work.
          Nothing is sent off-device. Clear it any time.
        </Text>
        <View style={[styles.buttonRow, { marginTop: 8 }]}>
          <Button
            title="Clear audit history"
            variant="ghost"
            onPress={() => {
              if (
                typeof window !== "undefined" &&
                !window.confirm("Clear all locally-stored audit history?")
              ) {
                return;
              }
              clearHistory();
              toast.info("Audit history cleared");
            }}
          />
        </View>
      </Card>

      <Card>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
          <PixelIcon name="gear" size={3} color={theme.colors.accent} />
          <Text style={[theme.typography.h2, { color: theme.colors.text }]}>Diagnostics</Text>
        </View>
        <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
          Not sure if everything's working? Run the System check — it confirms the backend, the
          database, and a real end-to-end engine self-test, and shows what's configured (OCR,
          Stripe, email). The raw operator probe is below.
        </Text>
        <View style={{ marginTop: 10, marginBottom: 4 }}>
          <Button title="Run system check" onPress={openSystemCheck} />
        </View>
        <DiagnosticsPanel apiBaseUrl={apiBaseUrl} />
      </Card>

      {showOperatorTools ? (
        <Card>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <PixelIcon name="spark" size={3} color={theme.colors.accent} />
            <Text style={[theme.typography.h2, { color: theme.colors.text }]}>AI provider</Text>
          </View>
          <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
            Alt-text and link-text suggestions can be powered by an AI vision model. The backend
            picks one based on environment variables when it starts:
          </Text>
          <View style={styles.aiList}>
            <Text style={[theme.typography.body, { color: theme.colors.text }]}>
              • Set <Text style={[theme.typography.mono]}>ANTHROPIC_API_KEY</Text> for Claude with vision.
            </Text>
            <Text style={[theme.typography.body, { color: theme.colors.text }]}>
              • Set <Text style={[theme.typography.mono]}>OPENAI_API_KEY</Text> for GPT-4 with vision.
            </Text>
            <Text style={[theme.typography.body, { color: theme.colors.text }]}>
              • Without either, the backend falls back to local heuristics (no network, lower quality).
            </Text>
          </View>
        </Card>
      ) : null}
    </Screen>
  );
}

interface DiagnosticCheck {
  name: string;
  ok: boolean;
  ms: number;
  detail?: string;
  items?: string[];
}

interface DiagnosticPayload {
  status: "ok" | "degraded";
  checks: DiagnosticCheck[];
  ai: {
    provider: string;
    ok: boolean;
    ms: number;
    sampleOutput?: string;
    confidence?: number;
    error?: string;
  };
  totals: { ms: number };
}

function ScoreWeightControls() {
  const theme = useTheme();
  const toast = useToast();
  const [weights, setWeights] = useState(loadWeights());

  const setWeight = (key: "error" | "warning" | "info", delta: number) => {
    const next = { ...weights, [key]: Math.max(0, Math.min(10, weights[key] + delta)) };
    setWeights(next);
    saveWeights(next);
  };

  const reset = () => {
    setWeights(resetWeights());
    toast.info("Score weights reset to defaults");
  };

  const isDefault =
    weights.error === DEFAULT_WEIGHTS.error &&
    weights.warning === DEFAULT_WEIGHTS.warning &&
    weights.info === DEFAULT_WEIGHTS.info;

  return (
    <View style={{ marginTop: 12, gap: 8 }}>
      {(["error", "warning", "info"] as const).map((key) => (
        <View
          key={key}
          style={{
            flexDirection: "row",
            alignItems: "center",
            gap: 12,
            padding: 8,
            borderWidth: 1,
            borderColor: theme.colors.border,
            backgroundColor: theme.colors.surface2,
            borderRadius: theme.radius.none,
          }}
        >
          <View
            style={{
              width: 8,
              height: 8,
              borderRadius: 4,
              backgroundColor:
                key === "error" ? theme.colors.danger : key === "warning" ? theme.colors.warning : theme.colors.info,
            }}
          />
          <Text style={[theme.typography.body, { color: theme.colors.text, flex: 1, fontWeight: "600" }]}>
            {key === "error" ? "Error" : key === "warning" ? "Warning" : "Info"}
          </Text>
          <Pressable accessibilityRole="button"
            onPress={() => setWeight(key, -1)}
            accessibilityLabel={`Decrease ${key} weight`}
            style={{
              width: 28,
              height: 28,
              borderRadius: theme.radius.sm,
              borderWidth: 1,
              borderColor: theme.colors.border,
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Text style={{ color: theme.colors.text, fontWeight: "700" }}>−</Text>
          </Pressable>
          <Text
            style={[
              theme.typography.body,
              { color: theme.colors.text, fontWeight: "700", minWidth: 32, textAlign: "center" },
            ]}
          >
            {weights[key]}
          </Text>
          <Pressable accessibilityRole="button"
            onPress={() => setWeight(key, 1)}
            accessibilityLabel={`Increase ${key} weight`}
            style={{
              width: 28,
              height: 28,
              borderRadius: theme.radius.sm,
              borderWidth: 1,
              borderColor: theme.colors.border,
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Text style={{ color: theme.colors.text, fontWeight: "700" }}>+</Text>
          </Pressable>
        </View>
      ))}
      <View style={{ flexDirection: "row", gap: 8, marginTop: 4 }}>
        <Button title="Reset to defaults" variant="ghost" onPress={reset} disabled={isDefault} />
        <Text
          style={[
            theme.typography.caption,
            { color: theme.colors.textMuted, alignSelf: "center", marginLeft: "auto" },
          ]}
        >
          Active during your next audit run
        </Text>
      </View>
    </View>
  );
}

function DiagnosticsPanel({ apiBaseUrl }: { apiBaseUrl: string }) {
  const theme = useTheme();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DiagnosticPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runDiagnostics = async () => {
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      const response = await fetch(`${apiBaseUrl}/diagnostics`);
      if (!response.ok) {
        // Fall back to /healthz so we report something useful.
        await fetch(`${apiBaseUrl}/healthz`);
        throw new Error(`Diagnostics endpoint returned ${response.status}`);
      }
      const payload = (await response.json()) as DiagnosticPayload;
      setResult(payload);
      if (payload.status === "ok") {
        toast.success("All systems healthy", { description: `${payload.totals.ms}ms` });
      } else {
        toast.warning("Some checks degraded", {
          description: "See diagnostics panel for details.",
        });
      }
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      toast.error("Diagnostics failed", { description: msg });
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={{ marginTop: 8, gap: 12 }}>
      <View style={{ flexDirection: "row", gap: 8 }}>
        <Button title={busy ? "Running…" : "Run diagnostic"} onPress={runDiagnostics} loading={busy} />
      </View>
      {error ? (
        <View
          style={{
            borderWidth: 1,
            borderColor: theme.colors.danger,
            backgroundColor: theme.colors.danger + "11",
            borderRadius: theme.radius.none,
            padding: 10,
          }}
        >
          <Text style={[theme.typography.mono, { color: theme.colors.danger }]}>
            ERROR: {error}
          </Text>
        </View>
      ) : null}
      {result ? (
        <View style={{ gap: 8 }}>
          <View style={{ flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <Text style={[theme.typography.h2, { color: theme.colors.text, fontSize: 14 }]}>
              {result.status === "ok" ? "All checks passed" : "Some checks degraded"}
            </Text>
            <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
              ({result.totals.ms}ms total)
            </Text>
          </View>
          {result.checks.map((check) => (
            <View
              key={check.name}
              style={{
                borderWidth: 1,
                borderColor: check.ok ? theme.colors.border : theme.colors.danger,
                backgroundColor: check.ok ? theme.colors.surface2 : theme.colors.danger + "11",
                borderRadius: theme.radius.none,
                padding: 10,
                gap: 4,
              }}
            >
              <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
                <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "700" }]}>
                  {check.ok ? "✓" : "✗"} {check.name}
                </Text>
                <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                  {check.ms}ms
                </Text>
              </View>
              {check.detail ? (
                <Text style={[theme.typography.body, { color: theme.colors.textMuted }]}>
                  {check.detail}
                </Text>
              ) : null}
              {check.items && check.items.length ? (
                <Text
                  style={[theme.typography.mono, { color: theme.colors.textMuted, fontSize: 11 }]}
                  numberOfLines={3}
                >
                  {check.items.join(", ")}
                </Text>
              ) : null}
            </View>
          ))}
          <View
            style={{
              borderWidth: 1,
              borderColor: result.ai.ok ? theme.colors.border : theme.colors.danger,
              backgroundColor: result.ai.ok ? theme.colors.surface2 : theme.colors.danger + "11",
              borderRadius: theme.radius.none,
              padding: 10,
              gap: 6,
            }}
          >
            <View style={{ flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
              <Text style={[theme.typography.body, { color: theme.colors.text, fontWeight: "700" }]}>
                {result.ai.ok ? "✓" : "✗"} AI provider · {result.ai.provider}
              </Text>
              <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                {result.ai.ms}ms
              </Text>
            </View>
            {result.ai.error ? (
              <Text style={[theme.typography.mono, { color: theme.colors.danger, fontSize: 11 }]}>
                {result.ai.error}
              </Text>
            ) : null}
            {result.ai.sampleOutput ? (
              <View>
                <Text style={[theme.typography.caption, { color: theme.colors.textMuted }]}>
                  Example output (alt-text for a synthetic image)
                  {typeof result.ai.confidence === "number"
                    ? ` · confidence ${result.ai.confidence}`
                    : ""}
                </Text>
                <Text
                  style={[theme.typography.body, { color: theme.colors.text, marginTop: 2 }]}
                >
                  "{result.ai.sampleOutput}"
                </Text>
              </View>
            ) : null}
          </View>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  input: { borderWidth: 1, padding: 10, marginTop: 12 },
  brandLabel: { fontSize: 12, fontWeight: "700", letterSpacing: 0.3, marginTop: 12, textTransform: "uppercase" },
  codeBlock: { borderWidth: 1, padding: 12, marginTop: 12 },
  newKeyBox: { borderWidth: 1, padding: 14, marginTop: 12 },
  keyRow: { flexDirection: "row", alignItems: "center", gap: 12, borderWidth: 1, padding: 12 },
  buttonRow: { flexDirection: "row", marginTop: 12, gap: 8 },
  toggleRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 16,
    marginTop: 12,
  },
  aiList: { gap: 6, marginTop: 8 },
  themeRow: { flexDirection: "row", gap: 8, marginTop: 12, flexWrap: "wrap" },
  themeChoice: {
    flex: 1,
    minWidth: 90,
    paddingVertical: 10,
    paddingHorizontal: 14,
    borderWidth: 1.5,
    alignItems: "center",
  },
});
