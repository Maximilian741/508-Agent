/**
 * ShaderBackground — a soft, flowing gradient field (WebGL fragment shader)
 * that sits BEHIND content, leans toward the pointer, and brightens with an
 * `intensity` prop so a page can make it respond to activity (e.g. while a
 * document is being processed).
 *
 * Contract:
 *  - Web only. Renders nothing on native.
 *  - prefers-reduced-motion -> a static CSS gradient built from the same four
 *    colours; no WebGL, no animation loop.
 *  - No WebGL / context lost / shader compile failure -> the same CSS gradient.
 *  - Pauses when the tab is hidden and when the element scrolls off-screen.
 *  - Caps devicePixelRatio and renders below CSS resolution (a soft field
 *    upsamples invisibly), so it stays cheap on 4K and laptop GPUs alike.
 *  - Draws a scrim on top. Every shader pixel is a CONVEX mix of the four
 *    palette colours (plus <= 1/255 dither), so scripts/check-contrast.mjs can
 *    bound the lightest/darkest pixel and prove text over scrim passes AA.
 *  - pointer-events: none and aria-hidden: it never intercepts input or
 *    reaches assistive technology.
 */
import React, { useEffect, useRef, useState } from "react";
import { Platform, StyleSheet, View } from "react-native";

import { useTheme } from "../useTheme";

export interface ShaderBackgroundProps {
  /** 0..1 — how lively/bright the field is. Eased, so it can change often. */
  intensity?: number;
  /** Lean toward the pointer. Default true. */
  interactive?: boolean;
  /** Draw the contrast scrim (default true). Only disable behind no text. */
  scrim?: boolean;
}

const VERT = "attribute vec2 a_pos;void main(){gl_Position=vec4(a_pos,0.0,1.0);}";

// GLSL ES 1.0. Every colour operation is a mix() between palette colours with
// a weight clamped to [0,1] — never an add — so output stays inside the
// palette's colour box (see the contrast contract above).
const FRAG = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 u_res;
uniform float u_time;
uniform vec2 u_pointer;
uniform float u_energy;
uniform float u_intensity;
uniform vec3 u_base;
uniform vec3 u_deep;
uniform vec3 u_glow;
uniform vec3 u_glow2;

float hash(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
float noise(vec2 p){
  vec2 i = floor(p); vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 p){
  float v = 0.0; float a = 0.5;
  mat2 r = mat2(0.8, -0.6, 0.6, 0.8);
  for (int i = 0; i < 4; i++) { v += a * noise(p); p = r * p * 2.03 + 7.1; a *= 0.5; }
  return v;
}
void main(){
  vec2 uv = gl_FragCoord.xy / u_res;
  float aspect = u_res.x / max(u_res.y, 1.0);
  vec2 p = vec2(uv.x * aspect, uv.y);
  vec2 m = vec2(u_pointer.x * aspect, u_pointer.y);
  float t = u_time * (0.045 + 0.07 * u_intensity);

  vec2 q = vec2(fbm(p * 1.4 + vec2(0.0, t)), fbm(p * 1.4 + vec2(5.2, -t * 0.8)));
  vec2 d = p - m;
  float pull = exp(-dot(d, d) * 5.0);
  vec2 warp = q * 1.5 - d * pull * (0.35 + 0.8 * u_energy);
  float f = fbm(p * 1.1 + warp + vec2(t * 0.6, -t * 0.35));

  // Base: ink -> deep navy, following the warped noise.
  vec3 col = mix(u_base, u_deep, smoothstep(0.15, 0.75, f));
  // Aurora ribbons: two soft bands that drift and bend with the field.
  float wave = sin(p.x * 1.7 + t * 2.4 + q.y * 3.0) * 0.18;
  float band1 = 1.0 - smoothstep(0.0, 0.22, abs(uv.y - 0.62 - wave - (q.x - 0.5) * 0.5));
  float band2 = 1.0 - smoothstep(0.0, 0.3, abs(uv.y - 0.3 + wave * 0.8 - (q.y - 0.5) * 0.6));
  col = mix(col, u_glow2, clamp(band1 * (0.55 + 0.35 * f), 0.0, 1.0));
  float g = max(band2 * (0.45 + 0.4 * f), smoothstep(0.55, 0.95, f * (0.6 + q.y)));
  col = mix(col, u_glow, clamp(g * (0.7 + 0.3 * u_intensity), 0.0, 1.0));
  // The pointer: a soft light that follows the cursor and swells with motion.
  float halo = pull * (0.25 + 0.35 * u_energy + 0.3 * u_intensity);
  col = mix(col, u_glow, clamp(halo, 0.0, 0.9));
  // Quieter edges so the panel reads as lit from within.
  float vig = smoothstep(1.4, 0.3, length((uv - vec2(0.55, 0.55)) * vec2(0.9, 1.3)));
  col = mix(u_base, col, clamp(0.35 + 0.65 * vig, 0.0, 1.0));

  col += (hash(mod(gl_FragCoord.xy, 256.0) * 0.37 + fract(u_time)) - 0.5) / 255.0;
  gl_FragColor = vec4(col, 1.0);
}
`;

function hexToVec3(hex: string): [number, number, number] {
  const n = parseInt(hex.replace("#", "").slice(0, 6), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

function prefersReducedMotion(): boolean {
  try {
    return typeof window !== "undefined" && !!window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/** The static gradient: used for reduced motion, no WebGL, and as the layer the canvas fades in over. */
export function shaderFallbackGradient(c: { shaderBase: string; shaderDeep: string; shaderGlow: string; shaderGlow2: string }): string {
  return [
    `radial-gradient(60% 90% at 18% 0%, ${c.shaderGlow2} 0%, transparent 70%)`,
    `radial-gradient(55% 80% at 88% 20%, ${c.shaderGlow} 0%, transparent 68%)`,
    `linear-gradient(165deg, ${c.shaderDeep} 0%, ${c.shaderBase} 78%)`,
  ].join(", ");
}

const MAX_DPR = 1.5;
/** Render at a fraction of CSS pixels: the field is soft, upsampling is invisible. */
const RENDER_SCALE = 0.6;
const MIN_FRAME_MS = 24;

export function ShaderBackground({ intensity = 0, interactive = true, scrim = true }: ShaderBackgroundProps) {
  const theme = useTheme();
  const c = theme.colors;
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [live, setLive] = useState(false); // canvas has drawn at least one frame
  // Follows the OS setting live: switching reduced motion on mid-visit stops
  // the loop at once (the effect below tears down and does not restart).
  const [reduced, setReduced] = useState(prefersReducedMotion);
  const intensityRef = useRef(intensity);
  const colorsRef = useRef(c);
  const interactiveRef = useRef(interactive);
  intensityRef.current = Math.max(0, Math.min(1, intensity));
  colorsRef.current = c;
  interactiveRef.current = interactive;

  useEffect(() => {
    if (Platform.OS !== "web" || typeof window === "undefined" || !window.matchMedia) return;
    let mq: MediaQueryList;
    try {
      mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    } catch {
      return;
    }
    const onChange = () => setReduced(mq.matches);
    onChange();
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if ((mq as any).addListener) (mq as any).addListener(onChange);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener("change", onChange);
      else if ((mq as any).removeListener) (mq as any).removeListener(onChange);
    };
  }, []);

  useEffect(() => {
    if (Platform.OS !== "web" || typeof window === "undefined") return;
    if (reduced) {
      setLive(false); // the static CSS gradient underneath takes over
      return;
    }
    const canvas = canvasRef.current;
    if (!canvas) return;

    let gl: WebGLRenderingContext | null = null;
    try {
      gl = (canvas.getContext("webgl", { antialias: false, alpha: false, powerPreference: "low-power", preserveDrawingBuffer: false }) ||
        canvas.getContext("experimental-webgl")) as WebGLRenderingContext | null;
    } catch {
      gl = null;
    }
    if (!gl) return;
    const g = gl;

    const compile = (type: number, src: string) => {
      const s = g.createShader(type);
      if (!s) return null;
      g.shaderSource(s, src);
      g.compileShader(s);
      if (!g.getShaderParameter(s, g.COMPILE_STATUS)) {
        g.deleteShader(s);
        return null;
      }
      return s;
    };
    const vs = compile(g.VERTEX_SHADER, VERT);
    const fs = compile(g.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return;
    const prog = g.createProgram();
    if (!prog) return;
    g.attachShader(prog, vs);
    g.attachShader(prog, fs);
    g.linkProgram(prog);
    if (!g.getProgramParameter(prog, g.LINK_STATUS)) return;
    g.useProgram(prog);

    const buf = g.createBuffer();
    g.bindBuffer(g.ARRAY_BUFFER, buf);
    g.bufferData(g.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), g.STATIC_DRAW);
    const aPos = g.getAttribLocation(prog, "a_pos");
    g.enableVertexAttribArray(aPos);
    g.vertexAttribPointer(aPos, 2, g.FLOAT, false, 0, 0);

    const u = (name: string) => g.getUniformLocation(prog, name);
    const uRes = u("u_res");
    const uTime = u("u_time");
    const uPointer = u("u_pointer");
    const uEnergy = u("u_energy");
    const uIntensity = u("u_intensity");
    const uBase = u("u_base");
    const uDeep = u("u_deep");
    const uGlow = u("u_glow");
    const uGlow2 = u("u_glow2");

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
      const w = Math.max(1, Math.floor((canvas.clientWidth || 1) * dpr * RENDER_SCALE));
      const h = Math.max(1, Math.floor((canvas.clientHeight || 1) * dpr * RENDER_SCALE));
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
        g.viewport(0, 0, w, h);
      }
    };
    resize();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    if (ro) ro.observe(canvas);
    else window.addEventListener("resize", resize);

    // Pointer: target position (0..1, y up) + an "energy" that spikes with
    // movement speed and decays — the field swirls a little when you move.
    const target = [0.7, 0.6];
    const pos = [0.7, 0.6];
    let energy = 0;
    const onPointer = (e: PointerEvent) => {
      if (!interactiveRef.current) return;
      const r = canvas.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return;
      const nx = (e.clientX - r.left) / r.width;
      const ny = 1 - (e.clientY - r.top) / r.height;
      const dx = nx - target[0];
      const dy = ny - target[1];
      target[0] = Math.max(-0.2, Math.min(1.2, nx));
      target[1] = Math.max(-0.2, Math.min(1.2, ny));
      energy = Math.min(1, energy + Math.sqrt(dx * dx + dy * dy) * 2.5);
    };
    window.addEventListener("pointermove", onPointer, { passive: true });

    let raf = 0;
    let onScreen = true;
    let lost = false;
    let smoothedIntensity = intensityRef.current;
    let lastFrame = performance.now();
    let t = 0;
    let drewFirst = false;

    const frame = (now: number) => {
      raf = 0;
      if (lost) return;
      // ~40fps is plenty for a slow field and halves the GPU time of 60.
      if (drewFirst && now - lastFrame < MIN_FRAME_MS) {
        schedule();
        return;
      }
      const dt = Math.min(0.05, (now - lastFrame) / 1000);
      lastFrame = now;
      t += dt;
      smoothedIntensity += (intensityRef.current - smoothedIntensity) * Math.min(1, dt * 2.5);
      pos[0] += (target[0] - pos[0]) * Math.min(1, dt * 3);
      pos[1] += (target[1] - pos[1]) * Math.min(1, dt * 3);
      energy *= Math.pow(0.25, dt);

      const col = colorsRef.current;
      g.uniform2f(uRes, canvas.width, canvas.height);
      g.uniform1f(uTime, t);
      g.uniform2f(uPointer, pos[0], pos[1]);
      g.uniform1f(uEnergy, energy);
      g.uniform1f(uIntensity, smoothedIntensity);
      g.uniform3fv(uBase, hexToVec3(col.shaderBase));
      g.uniform3fv(uDeep, hexToVec3(col.shaderDeep));
      g.uniform3fv(uGlow, hexToVec3(col.shaderGlow));
      g.uniform3fv(uGlow2, hexToVec3(col.shaderGlow2));
      g.drawArrays(g.TRIANGLES, 0, 6);
      if (!drewFirst) {
        drewFirst = true;
        setLive(true);
      }
      schedule();
    };
    const schedule = () => {
      if (raf !== 0 || lost) return;
      if (!onScreen || document.visibilityState === "hidden") return;
      raf = requestAnimationFrame(frame);
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        if (raf !== 0) cancelAnimationFrame(raf);
        raf = 0;
      } else {
        lastFrame = performance.now();
        schedule();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    let io: IntersectionObserver | null = null;
    if (typeof IntersectionObserver !== "undefined") {
      io = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          onScreen = entry.isIntersecting;
          if (onScreen) {
            lastFrame = performance.now();
            schedule();
          } else if (raf !== 0) {
            cancelAnimationFrame(raf);
            raf = 0;
          }
        }
      });
      io.observe(canvas);
    }

    const onLost = (e: Event) => {
      e.preventDefault();
      lost = true;
      if (raf !== 0) cancelAnimationFrame(raf);
      raf = 0;
      setLive(false); // CSS gradient shows through
    };
    canvas.addEventListener("webglcontextlost", onLost as EventListener, false);

    schedule();

    return () => {
      lost = true; // stops any callback already in flight from rescheduling
      if (raf !== 0) cancelAnimationFrame(raf);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pointermove", onPointer);
      canvas.removeEventListener("webglcontextlost", onLost as EventListener, false);
      if (io) io.disconnect();
      if (ro) ro.disconnect();
      else window.removeEventListener("resize", resize);
      g.deleteBuffer(buf);
      g.deleteProgram(prog);
      g.deleteShader(vs);
      g.deleteShader(fs);
    };
  }, [reduced]);

  if (Platform.OS !== "web") return null;

  return (
    <View
      pointerEvents="none"
      // @ts-ignore - web a11y attribute
      aria-hidden={true}
      style={[
        StyleSheet.absoluteFill,
        {
          overflow: "hidden",
          backgroundColor: c.shaderBase,
          // The static gradient always sits underneath: it IS the reduced-
          // motion / no-WebGL rendering, and what shows before the first frame.
          backgroundImage: shaderFallbackGradient(c),
        } as any,
      ]}
    >
      <canvas
        ref={canvasRef}
        aria-hidden={true}
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          display: "block",
          opacity: live ? 1 : 0,
          transition: "opacity 900ms ease",
        }}
      />
      {scrim ? <View style={[StyleSheet.absoluteFill, { backgroundColor: c.scrim }]} /> : null}
    </View>
  );
}

export default ShaderBackground;
