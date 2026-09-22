import { Pressable, StyleSheet, Text, View } from "react-native";
import { alpha } from "../theme";
import { useTheme } from "../useTheme";
import { Icon, IconName } from "./Icon";

interface InlineNoticeProps {
  title: string;
  message: string;
  tone?: "info" | "success" | "warning" | "danger";
  /** Optional inline action button rendered to the right of the message. */
  actionLabel?: string;
  /** Required if actionLabel is provided. */
  onAction?: () => void;
}

export function InlineNotice({
  title,
  message,
  tone = "info",
  actionLabel,
  onAction,
}: InlineNoticeProps) {
  const theme = useTheme();
  const styles = createStyles(theme);
  const color = toneColor(theme, tone);

  return (
    <View style={[styles.container, { borderColor: alpha(color, 0.35), backgroundColor: `${color}1A` }]}>
      <Icon name={iconName(tone)} size={18} color={color} style={{ marginTop: 2 }} />
      <View style={styles.textWrap}>
        <Text style={[styles.title, { color }]}>{title}</Text>
        <Text style={styles.message}>{message}</Text>
        {actionLabel && onAction ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={actionLabel}
            onPress={onAction}
            style={({ hovered, pressed }: any) => [
              styles.actionBtn,
              { borderColor: alpha(color, 0.5), backgroundColor: pressed ? `${color}33` : `${color}22` },
              hovered ? { opacity: 0.9 } : null,
            ]}
          >
            <Text style={[styles.actionText, { color }]}>{actionLabel}</Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

const toneColor = (theme: ReturnType<typeof useTheme>, tone: InlineNoticeProps["tone"]) => {
  if (tone === "success") return theme.colors.success;
  if (tone === "warning") return theme.colors.warning;
  if (tone === "danger") return theme.colors.danger;
  return theme.colors.info;
};

const iconName = (tone: InlineNoticeProps["tone"]): IconName => {
  if (tone === "success") return "check-circle";
  if (tone === "warning") return "alert-triangle";
  if (tone === "danger") return "alert-circle";
  return "info";
};

const createStyles = (theme: ReturnType<typeof useTheme>) =>
  StyleSheet.create({
    container: {
      flexDirection: "row",
      alignItems: "flex-start",
      gap: theme.spacing.sm,
      padding: theme.spacing.md,
      borderRadius: theme.radius.md,
      borderWidth: 1,
    },
    textWrap: {
      flex: 1,
      gap: theme.spacing.xs,
    },
    title: {
      ...theme.typography.body,
      fontWeight: "600",
    },
    message: {
      ...theme.typography.body,
      color: theme.colors.textMuted,
    },
    actionBtn: {
      alignSelf: "flex-start",
      marginTop: theme.spacing.xs,
      paddingHorizontal: theme.spacing.md,
      paddingVertical: theme.spacing.xs,
      borderRadius: theme.radius.sm,
      borderWidth: 1,
    },
    actionText: {
      ...theme.typography.body,
      fontWeight: "700",
      fontSize: 13,
    },
  });
