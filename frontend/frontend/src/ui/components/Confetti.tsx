/**
 * Confetti - dependency-free 24-particle burst.
 *
 * Mounts when `trigger` toggles to a new value, runs a 1.4s animation, then
 * unmounts. No-ops if reduced motion is preferred. Pointer-events disabled so
 * it never blocks the underlying UI.
 */
import React, { useEffect, useState } from "react";
import { Animated, Easing, Platform, View } from "react-native";

import { useReducedMotion } from "../../hooks/useReducedMotion";

const COLORS = ["#2D5BFF", "#16A34A", "#F59E0B", "#DC2626", "#7C3AED", "#EC4899"];
const PARTICLE_COUNT = 24;

interface Particle {
  id: number;
  color: string;
  x: number;
  y: number;
  rot: number;
  scale: number;
  anim: Animated.Value;
}

function makeParticles(): Particle[] {
  const out: Particle[] = [];
  for (let i = 0; i < PARTICLE_COUNT; i++) {
    const angle = (Math.PI * 2 * i) / PARTICLE_COUNT + (Math.random() - 0.5) * 0.4;
    const dist = 80 + Math.random() * 100;
    out.push({
      id: i,
      color: COLORS[i % COLORS.length],
      x: Math.cos(angle) * dist,
      y: Math.sin(angle) * dist,
      rot: Math.random() * 360,
      scale: 0.6 + Math.random() * 0.6,
      anim: new Animated.Value(0),
    });
  }
  return out;
}

export function Confetti({ trigger }: { trigger: number }) {
  const reduced = useReducedMotion();
  const [particles, setParticles] = useState<Particle[]>([]);

  useEffect(() => {
    if (Platform.OS !== "web" || reduced || trigger <= 0) return;
    const next = makeParticles();
    setParticles(next);
    Animated.parallel(
      next.map((p) =>
        Animated.timing(p.anim, {
          toValue: 1,
          duration: 1400,
          easing: Easing.out(Easing.cubic),
          useNativeDriver: false,
        }),
      ),
    ).start(() => setParticles([]));
  }, [trigger, reduced]);

  if (particles.length === 0) return null;

  return (
    <View
      pointerEvents="none"
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      {particles.map((p) => {
        const tx = p.anim.interpolate({ inputRange: [0, 1], outputRange: [0, p.x] });
        const ty = p.anim.interpolate({ inputRange: [0, 1], outputRange: [0, p.y + 60] });
        const opacity = p.anim.interpolate({ inputRange: [0, 0.7, 1], outputRange: [1, 1, 0] });
        return (
          <Animated.View
            key={p.id}
            style={{
              position: "absolute",
              width: 8,
              height: 8,
              borderRadius: 2,
              backgroundColor: p.color,
              opacity,
              transform: [{ translateX: tx }, { translateY: ty }, { scale: p.scale }],
            }}
          />
        );
      })}
    </View>
  );
}

export default Confetti;
