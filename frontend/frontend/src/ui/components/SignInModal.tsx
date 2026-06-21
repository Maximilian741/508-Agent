/**
 * SignInModal - welcoming single-form sign in / sign up.
 *
 * Welcome step: email + optional display name + password (8+ chars, required —
 * the backend creates the account on first sign-in with these credentials).
 * A "Forgot password?" link under the password field emails a single-use
 * reset link (always shows the same neutral confirmation — no account
 * enumeration).
 *
 * Success step: a celebratory beat ("Welcome, X!") shown for ~800ms before
 * the modal hands off to onSignedIn (or reloads the page on web when no
 * handler is provided).
 *
 * This delegates the network calls to domain/account (signIn,
 * requestPasswordReset) and respects the "Keep me signed in" toggle by
 * passing remember through, which stores the JWT in localStorage (true) or
 * sessionStorage (false).
 */

import React, { useEffect, useMemo, useRef, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { requestPasswordReset, signIn } from "../../domain/account";
import { useTheme } from "../useTheme";
import { Button } from "./Button";
import { PixelIcon } from "./PixelIcon";

// Web-only portal: render the modal directly under document.body so it
// escapes any ancestor that has a CSS transform (which would otherwise
// break position: fixed and the modal would render off-screen relative
// to the transformed ancestor instead of the viewport).
function ModalPortal({ children }: { children: React.ReactNode }) {
  if (Platform.OS !== "web" || typeof document === "undefined") {
    return <>{children}</>;
  }
  const reactDom = require("react-dom");
  if (typeof reactDom.createPortal !== "function") {
    return <>{children}</>;
  }
  return reactDom.createPortal(children, document.body);
}

export interface SignInModalProps {
  open: boolean;
  onCancel: () => void;
  /** Called after the account has been minted. Defaults to a page reload. */
  onSignedIn?: () => void;
  /**
   * Optional headline override. Pass a more direct line like "Sign in to keep
   * auditing - your first scan was free." when the modal is opened from a
   * gate. When omitted, the welcoming default copy is used.
   */
  reason?: string;
  /**
   * Called when the user picks "Maybe later". Behaves like onCancel but lets
   * the caller log a softer dismissal. Defaults to onCancel.
   */
  onMaybeLater?: () => void;
}

type Step = "welcome" | "success";

// Pragmatic email regex. Not RFC-perfect, but rejects the common
// fat-finger cases (missing "@", missing TLD, trailing space).
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function isValidEmail(value: string): boolean {
  return EMAIL_RE.test(value.trim());
}

interface BenefitRowProps {
  text: string;
  color: string;
  bullet: string;
}

function BenefitRow({ text, color, bullet }: BenefitRowProps) {
  return (
    <View style={{ flexDirection: "row", alignItems: "flex-start", marginBottom: 6 }}>
      <Text
        style={{
          color: bullet,
          fontSize: 14,
          lineHeight: 20,
          width: 16,
          fontWeight: "700",
        }}
      >
        {"-"}
      </Text>
      <Text style={{ color, fontSize: 14, lineHeight: 20, flex: 1 }}>{text}</Text>
    </View>
  );
}

export function SignInModal({
  open,
  onCancel,
  onSignedIn,
  reason,
  onMaybeLater,
}: SignInModalProps) {
  const theme = useTheme();
  const [step, setStep] = useState<Step>("welcome");
  const [email, setEmail] = useState("");
  const [emailTouched, setEmailTouched] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [signedInName, setSignedInName] = useState<string>("");
  const [forgotBusy, setForgotBusy] = useState(false);
  const [forgotSent, setForgotSent] = useState(false);
  const emailRef = useRef<TextInput | null>(null);

  // Reset every time the modal opens.
  useEffect(() => {
    if (!open) return;
    setStep("welcome");
    setEmail("");
    setEmailTouched(false);
    setDisplayName("");
    setPassword("");
    setRemember(true);
    setError(null);
    setSubmitting(false);
    setSignedInName("");
    setForgotBusy(false);
    setForgotSent(false);
    const t = setTimeout(() => {
      try {
        emailRef.current?.focus();
      } catch {
        // ignore
      }
    }, 30);
    return () => clearTimeout(t);
  }, [open]);

  // Esc closes (web only). The success state ignores Esc - the modal will
  // dismiss itself after the success beat.
  useEffect(() => {
    if (!open) return;
    if (Platform.OS !== "web") return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && step !== "success") {
        e.preventDefault();
        onCancel();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onCancel, step]);

  const inlineEmailError = useMemo(() => {
    if (!emailTouched) return null;
    if (email.trim().length === 0) return "Email is required.";
    if (!isValidEmail(email)) return "That does not look like a valid email.";
    return null;
  }, [email, emailTouched]);

  if (!open) return null;

  const finishAndDismiss = (name: string) => {
    setSignedInName(name);
    setStep("success");
    // Brief beat so the user sees "Welcome, X!" before the page reloads.
    // 800ms feels celebratory without dragging.
    setTimeout(() => {
      if (onSignedIn) {
        onSignedIn();
      } else if (Platform.OS === "web" && typeof window !== "undefined") {
        try {
          window.location.reload();
        } catch {
          // ignore
        }
      }
    }, 800);
  };

  const requestReset = async () => {
    const trimmed = email.trim();
    setEmailTouched(true);
    if (!isValidEmail(trimmed)) {
      setError("Enter your account email above first, then tap Forgot password.");
      return;
    }
    setForgotBusy(true);
    setError(null);
    try {
      await requestPasswordReset(trimmed);
      setForgotSent(true);
    } catch {
      // Even failures show the same neutral copy — no account enumeration.
      setForgotSent(true);
    } finally {
      setForgotBusy(false);
    }
  };

  const submit = async () => {
    const trimmed = email.trim();
    setEmailTouched(true);
    if (!isValidEmail(trimmed)) {
      // inline error handles this; don't double up
      setError(null);
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await signIn(
        trimmed,
        displayName.trim(),
        password,
        remember,
      );
      const niceName =
        (result && result.user && result.user.displayName) ||
        displayName.trim() ||
        trimmed.split("@")[0] ||
        "friend";
      setSubmitting(false);
      finishAndDismiss(niceName);
    } catch (e: any) {
      setSubmitting(false);
      setError(
        e?.message ||
          "Could not sign in. Check your connection and try again.",
      );
    }
  };

  // Use a slightly warmer surface than the page background so the modal
  // pops against whatever is behind it.
  const panelBg = theme.colors.surface2;

  return (
    <ModalPortal>
    <View
      // @ts-ignore web-only role
      accessibilityRole={Platform.OS === "web" ? ("dialog" as any) : undefined}
      accessibilityLabel="Sign in"
      style={styles.root}
    >
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Close sign in"
        onPress={() => {
          if (step !== "success") onCancel();
        }}
        style={[styles.backdrop, { backgroundColor: "rgba(20, 12, 6, 0.55)" }]}
      />
      <View
        style={[
          styles.panel,
          {
            backgroundColor: panelBg,
            borderColor: theme.colors.border,
            borderRadius: theme.radius.md,
          },
        ]}
      >
        {step === "welcome" ? (
          <WelcomeStep
            theme={theme}
            reason={reason}
            email={email}
            setEmail={(v) => {
              setEmail(v);
              if (error) setError(null);
            }}
            onEmailBlur={() => setEmailTouched(true)}
            inlineEmailError={inlineEmailError}
            displayName={displayName}
            setDisplayName={setDisplayName}
            remember={remember}
            setRemember={setRemember}
            error={error}
            submitting={submitting}
            emailRef={emailRef}
            password={password}
            setPassword={(v) => {
              setPassword(v);
              if (error) setError(null);
            }}
            onCancel={onCancel}
            onMaybeLater={onMaybeLater}
            onSubmit={() => submit()}
            onForgotPassword={() => void requestReset()}
            forgotBusy={forgotBusy}
            forgotSent={forgotSent}
          />
        ) : null}

        {step === "success" ? (
          <SuccessStep theme={theme} name={signedInName} />
        ) : null}
      </View>
    </View>
    </ModalPortal>
  );
}

interface WelcomeStepProps {
  theme: ReturnType<typeof useTheme>;
  reason?: string;
  email: string;
  setEmail: (v: string) => void;
  onEmailBlur: () => void;
  inlineEmailError: string | null;
  displayName: string;
  setDisplayName: (v: string) => void;
  password: string;
  setPassword: (v: string) => void;
  remember: boolean;
  setRemember: (v: boolean) => void;
  error: string | null;
  submitting: boolean;
  emailRef: React.MutableRefObject<TextInput | null>;
  onCancel: () => void;
  onMaybeLater?: () => void;
  onSubmit: () => void;
  onForgotPassword: () => void;
  forgotBusy: boolean;
  forgotSent: boolean;
}

function WelcomeStep(props: WelcomeStepProps) {
  const {
    theme,
    reason,
    email,
    setEmail,
    onEmailBlur,
    inlineEmailError,
    displayName,
    setDisplayName,
    password,
    setPassword,
    remember,
    setRemember,
    error,
    submitting,
    emailRef,
    onCancel,
    onMaybeLater,
    onSubmit,
    onForgotPassword,
    forgotBusy,
    forgotSent,
  } = props;

  return (
    <View>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
        <PixelIcon name="key" size={4} color={theme.colors.accent} />
        <Text
          style={[
            theme.typography.caption,
            { color: theme.colors.textMuted },
          ]}
        >
          Welcome
        </Text>
      </View>

      <Text
        style={[
          theme.typography.displaySmall,
          { color: theme.colors.text, marginTop: 8 },
        ]}
      >
        Save your work, keep going
      </Text>

      <Text
        style={{
          color: theme.colors.textMuted,
          fontSize: 14,
          lineHeight: 20,
          marginTop: 8,
          marginBottom: 14,
        }}
      >
        {reason ?? "An account lets the app remember you across sessions."}
      </Text>

      <View
        style={{
          backgroundColor: theme.colors.surface,
          borderColor: theme.colors.border,
          borderWidth: 1,
          borderRadius: theme.radius.md,
          paddingHorizontal: 14,
          paddingVertical: 12,
          marginBottom: 18,
        }}
      >
        <BenefitRow
          text="Audit history saved across sessions"
          color={theme.colors.text}
          bullet={theme.colors.accent}
        />
        <BenefitRow
          text="25 starter credits to remediate documents"
          color={theme.colors.text}
          bullet={theme.colors.accent}
        />
        <BenefitRow
          text="Pick up exactly where you left off"
          color={theme.colors.text}
          bullet={theme.colors.accent}
        />
      </View>

      <Text style={[styles.labelLarge, { color: theme.colors.text }]}>
        What is your email?
      </Text>
      <TextInput
        ref={(el) => {
          emailRef.current = el;
        }}
        value={email}
        onChangeText={setEmail}
        onBlur={onEmailBlur}
        placeholder="you@example.com"
        placeholderTextColor={theme.colors.textMuted}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="email-address"
        accessibilityLabel="Email address"
        style={[
          styles.inputLarge,
          {
            borderColor: inlineEmailError
              ? theme.colors.danger
              : theme.colors.border,
            backgroundColor: theme.colors.surface,
            color: theme.colors.text,
          },
        ]}
      />
      {inlineEmailError ? (
        <Text
          style={{
            color: theme.colors.danger,
            fontSize: 12,
            marginTop: 4,
          }}
        >
          {inlineEmailError}
        </Text>
      ) : null}

      <Text
        style={[
          styles.label,
          { color: theme.colors.text, marginTop: 14 },
        ]}
      >
        What should we call you?
      </Text>
      <TextInput
        value={displayName}
        onChangeText={setDisplayName}
        placeholder="Optional - your first name works"
        placeholderTextColor={theme.colors.textMuted}
        accessibilityLabel="Display name"
        style={[
          styles.input,
          {
            borderColor: theme.colors.border,
            backgroundColor: theme.colors.surface,
            color: theme.colors.text,
          },
        ]}
        returnKeyType="next"
      />

      <Text
        style={[
          styles.label,
          { color: theme.colors.text, marginTop: 14 },
        ]}
      >
        Choose a password
      </Text>
      <TextInput
        value={password}
        onChangeText={setPassword}
        placeholder="At least 8 characters"
        placeholderTextColor={theme.colors.textMuted}
        secureTextEntry
        autoCapitalize="none"
        autoCorrect={false}
        accessibilityLabel="Password"
        style={[
          styles.input,
          {
            borderColor: theme.colors.border,
            backgroundColor: theme.colors.surface,
            color: theme.colors.text,
          },
        ]}
        onSubmitEditing={onSubmit}
        returnKeyType="go"
      />

      {forgotSent ? (
        <Text style={{ color: theme.colors.textMuted, fontSize: 12, marginTop: 6 }}>
          If that address has an account, a reset link is on its way (valid for 1 hour).
        </Text>
      ) : (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Forgot password: email me a reset link"
          onPress={onForgotPassword}
          disabled={forgotBusy}
          style={({ hovered }: any) => [{ alignSelf: "flex-start", marginTop: 6 }, hovered ? { opacity: 0.7 } : null]}
        >
          <Text style={{ color: theme.colors.accent, fontSize: 12, fontWeight: "600" }}>
            {forgotBusy ? "Sending reset link…" : "Forgot password?"}
          </Text>
        </Pressable>
      )}

      <RememberCheckbox
        theme={theme}
        checked={remember}
        onToggle={() => setRemember(!remember)}
      />

      {error ? (
        <Text
          style={{
            color: theme.colors.danger,
            fontSize: 13,
            marginTop: 12,
          }}
        >
          {error}
        </Text>
      ) : null}

      <View style={styles.actions}>
        <Button
          title="Maybe later"
          onPress={onMaybeLater ?? onCancel}
          variant="ghost"
          disabled={submitting}
          accessibilityHint="Dismiss this prompt and keep exploring without an account."
        />
        <Button
          title={submitting ? "Signing in..." : "Continue"}
          onPress={onSubmit}
          variant="primary"
          loading={submitting}
          disabled={
            submitting ||
            !!inlineEmailError ||
            email.trim().length === 0 ||
            password.length < 8
          }
        />
      </View>
    </View>
  );
}

interface SuccessStepProps {
  theme: ReturnType<typeof useTheme>;
  name: string;
}

function SuccessStep({ theme, name }: SuccessStepProps) {
  return (
    <View
      // @ts-ignore web-only live region
      accessibilityLiveRegion={"polite" as any}
      style={{ alignItems: "center", paddingVertical: 18 }}
    >
      <PixelIcon name="check" size={6} color={theme.colors.success} />
      <Text
        style={[
          theme.typography.displaySmall,
          {
            color: theme.colors.text,
            marginTop: 14,
            textAlign: "center",
          },
        ]}
      >
        {"Welcome, " + (name || "friend") + "!"}
      </Text>
      <Text
        style={{
          color: theme.colors.textMuted,
          fontSize: 14,
          marginTop: 8,
          textAlign: "center",
        }}
      >
        Loading your workspace...
      </Text>
    </View>
  );
}

interface RememberCheckboxProps {
  theme: ReturnType<typeof useTheme>;
  checked: boolean;
  onToggle: () => void;
}

function RememberCheckbox({ theme, checked, onToggle }: RememberCheckboxProps) {
  return (
    <Pressable
      accessibilityRole="checkbox"
      accessibilityState={{ checked }}
      accessibilityLabel="Keep me signed in on this device"
      onPress={onToggle}
      style={{
        flexDirection: "row",
        alignItems: "center",
        marginTop: 14,
        paddingVertical: 4,
      }}
    >
      <View
        style={{
          width: 18,
          height: 18,
          borderRadius: theme.radius.xs,
          borderWidth: 2,
          borderColor: checked ? theme.colors.accent : theme.colors.border,
          backgroundColor: checked ? theme.colors.accent : "transparent",
          alignItems: "center",
          justifyContent: "center",
          marginRight: 8,
        }}
      >
        {checked ? (
          <Text
            style={{
              color: theme.colors.surface,
              fontSize: 11,
              fontWeight: "900",
              lineHeight: 12,
            }}
          >
            {"x"}
          </Text>
        ) : null}
      </View>
      <Text style={{ color: theme.colors.text, fontSize: 13 }}>
        Keep me signed in on this device
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: {
    position: (Platform.OS === "web" ? "fixed" : "absolute") as any,
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    alignItems: "center",
    justifyContent: "center",
    zIndex: 10000,
    padding: 16,
  },
  backdrop: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
  },
  panel: {
    // position: relative + zIndex bumps the panel above the absolute
    // backdrop. Without this, CSS paints positioned siblings (backdrop)
    // in front of static ones (panel) and the modal contents disappear.
    position: "relative",
    zIndex: 1,
    width: "100%",
    maxWidth: 480,
    borderWidth: 1,
    borderRadius: 16,
    padding: 30,
    // @ts-ignore web-only shadow
    boxShadow: "0 18px 48px rgba(31, 20, 10, 0.34)",
  },
  label: {
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.6,
    marginBottom: 6,
  },
  labelLarge: {
    fontSize: 13,
    fontWeight: "700",
    letterSpacing: 0.4,
    marginBottom: 6,
  },
  input: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
  },
  inputLarge: {
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 14,
    fontSize: 16,
  },
  actions: {
    flexDirection: "row",
    justifyContent: "flex-end",
    gap: 8,
    marginTop: 22,
  },
});
