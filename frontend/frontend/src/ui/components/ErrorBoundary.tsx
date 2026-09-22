/**
 * App-level error boundary.
 *
 * Catches React render-time errors and shows a friendly recovery screen
 * instead of a blank white page.  In dev the stack trace is shown so we
 * can copy/paste it into a bug report.
 */

import React, { Component, ErrorInfo, ReactNode } from "react";
import { Platform, ScrollView, StyleSheet, Text, View } from "react-native";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
  info: ErrorInfo | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: null };

  static getDerivedStateFromError(error: Error): State {
    return { error, info: null };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.setState({ error, info });
    // eslint-disable-next-line no-console
    console.error("[508 Agent] render error:", error, info);
  }

  reset = () => {
    this.setState({ error: null, info: null });
    if (Platform.OS === "web" && typeof window !== "undefined") {
      window.location.reload();
    }
  };

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <ScrollView contentContainerStyle={styles.wrap}>
        <View style={styles.card}>
          <Text style={styles.eyebrow}>Something went wrong</Text>
          <Text style={styles.title}>The app hit an unexpected error</Text>
          <Text style={styles.body}>
            Your work isn't lost — audit history and any in-progress audit are saved to local
            storage. Reload the page to recover.
          </Text>
          <View style={styles.errBox}>
            <Text style={styles.errLabel}>Error</Text>
            <Text style={styles.errMessage}>{this.state.error.message}</Text>
            {/* Stack traces are developer information — never show customers. */}
            {__DEV__ && this.state.error.stack ? (
              <Text style={styles.errStack} numberOfLines={6}>
                {this.state.error.stack}
              </Text>
            ) : null}
          </View>
          <Text style={styles.hint}>
            Click anywhere on this screen and we'll reload the page for you.
          </Text>
          <Text onPress={this.reset} style={styles.button}>
            Reload app
          </Text>
        </View>
      </ScrollView>
    );
  }
}

const styles = StyleSheet.create({
  wrap: {
    flexGrow: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
    backgroundColor: "#0B0F1A",
  },
  card: {
    maxWidth: 600,
    width: "100%",
    backgroundColor: "#121826",
    borderRadius: 4,
    borderWidth: 1,
    borderColor: "#243048",
    padding: 32,
    gap: 12,
  },
  eyebrow: { color: "#F87171", fontSize: 11, fontWeight: "800", letterSpacing: 1.4, textTransform: "uppercase" },
  title: { color: "#F8FAFC", fontSize: 24, fontWeight: "800", letterSpacing: -0.4 },
  body: { color: "#A0AEC0", fontSize: 14, lineHeight: 22 },
  errBox: {
    marginTop: 8,
    padding: 12,
    borderRadius: 4,
    backgroundColor: "#0B0F1A",
    borderWidth: 1,
    borderColor: "#243048",
  },
  errLabel: { color: "#F87171", fontSize: 11, fontWeight: "800", letterSpacing: 0.6, textTransform: "uppercase" },
  errMessage: { color: "#F8FAFC", fontSize: 13, marginTop: 4, fontFamily: Platform.select({ web: "ui-monospace, monospace", default: undefined }) },
  errStack: {
    color: "#A0AEC0",
    fontSize: 11,
    marginTop: 8,
    fontFamily: Platform.select({ web: "ui-monospace, monospace", default: undefined }),
  },
  hint: { color: "#A0AEC0", fontSize: 12, marginTop: 8 },
  button: {
    alignSelf: "flex-start",
    marginTop: 4,
    backgroundColor: "#5EEAD4",
    color: "#04241F",
    paddingVertical: 10,
    paddingHorizontal: 18,
    borderRadius: 4,
    fontWeight: "700",
    overflow: "hidden",
  },
});
