/**
 * Animated shimmer placeholder.
 *
 * Used while waiting on the analyzer.  Pure RN — no external deps.
 */

import React, { useEffect, useRef } from "react";
import { Animated, StyleSheet, View } from "react-native";

import { useTheme } from "../useTheme";

export interface SkeletonProps {
  width?: number | string;
  height?: number;
  radius?: number;
  style?: any;
}

export function Skeleton({ width = "100%", height = 16, radius, style }: SkeletonProps) {
  const theme = useTheme();
  const resolvedRadius = radius ?? theme.radius.none;
  const shimmer = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(shimmer, { toValue: 1, duration: 900, useNativeDriver: false }),
        Animated.timing(shimmer, { toValue: 0, duration: 900, useNativeDriver: false }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [shimmer]);

  const opacity = shimmer.interpolate({ inputRange: [0, 1], outputRange: [0.45, 0.85] });

  return (
    <Animated.View
      style={[
        styles.base,
        {
          width: width as any,
          height,
          borderRadius: resolvedRadius,
          backgroundColor: theme.colors.surface2,
          opacity,
        },
        style,
      ]}
    />
  );
}

export function SkeletonBlock() {
  return (
    <View style={{ gap: 8 }}>
      <Skeleton width="60%" height={20} />
      <Skeleton width="100%" height={14} />
      <Skeleton width="92%" height={14} />
      <Skeleton width="80%" height={14} />
    </View>
  );
}

const styles = StyleSheet.create({
  base: { overflow: "hidden" },
});
