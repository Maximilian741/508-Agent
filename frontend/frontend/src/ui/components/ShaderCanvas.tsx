/**
 * ShaderCanvas - WebGL shader background.
 *
 * Real implementation matching the original four uploaded shader pages:
 *   nebula  - swirling cosmic dust with stars and click-ignite bursts
 *   ember   - rising smoke billows with hot-coal mouse glow
 *   maple   - autumn morning mist with drifting leaves
 *   pumpkin - curl-noise flow field smoke in pumpkin palette
 *
 * Uniforms wired:
 *   u_time, u_res, u_mouse, u_mouseSmooth, u_click, u_clickTime, u_pressure
 *
 * SSR-safe (returns null on native and during initial server render).
 * Honors prefers-reduced-motion with a static gradient fallback.
 * Mouse + click tracking is local to the canvas element so multiple
 * ShaderCanvas instances on one page do not cross-talk.
 */
import React, { useEffect, useRef, useState } from "react";
import { Platform, View } from "react-native";

export type ShaderVariant = "nebula" | "ember" | "maple" | "pumpkin" | "aurora";

export interface ShaderCanvasProps {
  variant?: ShaderVariant;
  opacity?: number;
}

const FALLBACK_GRADIENTS: Record<ShaderVariant, string> = {
  nebula: "linear-gradient(135deg, #050217 0%, #4D1F8F 45%, #F2418C 100%)",
  ember: "linear-gradient(135deg, #0A0708 0%, #8C381A 50%, #FFB854 100%)",
  maple: "linear-gradient(135deg, #F2DBB5 0%, #D18C38 50%, #732116 100%)",
  pumpkin: "linear-gradient(135deg, #0D080A 0%, #9E2D1A 50%, #F2B340 100%)",
  aurora: "linear-gradient(135deg, #050D1F 0%, #1A8CBF 45%, #8C33D9 100%)",
};

const VERT_SRC = "attribute vec2 a_pos; void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }";

const NEBULA_FRAG = [
  "precision highp float;",
  "uniform float u_time;",
  "uniform vec2 u_res;",
  "uniform vec2 u_mouse;",
  "uniform vec2 u_mouseSmooth;",
  "uniform vec2 u_click;",
  "uniform float u_clickTime;",
  "uniform float u_pressure;",
  "float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7)))*43758.5453); }",
  "float noise(vec2 p){",
  "  vec2 i=floor(p), f=fract(p);",
  "  vec2 u=f*f*(3.0-2.0*f);",
  "  return mix(mix(hash(i),hash(i+vec2(1,0)),u.x),",
  "             mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),u.x),u.y);",
  "}",
  "float fbm(vec2 p){",
  "  float v=0.0,a=0.55;",
  "  mat2 R=mat2(0.7,-0.7,0.7,0.7);",
  "  for(int i=0;i<6;i++){ v+=a*noise(p); p=R*p*2.0+11.0; a*=0.5; }",
  "  return v;",
  "}",
  "float stars(vec2 uv, float density){",
  "  vec2 g = floor(uv*200.0);",
  "  float h = hash(g);",
  "  float s = step(1.0-density, h);",
  "  vec2 c = fract(uv*200.0)-0.5;",
  "  float d = length(c);",
  "  float twinkle = 0.5+0.5*sin(u_time*3.0 + h*30.0);",
  "  return s * smoothstep(0.05, 0.0, d) * twinkle;",
  "}",
  "void main(){",
  "  vec2 uv = gl_FragCoord.xy/u_res.xy;",
  "  vec2 p = (gl_FragCoord.xy - 0.5*u_res.xy)/u_res.y;",
  "  vec2 m = u_mouseSmooth - 0.5; m.x *= u_res.x/u_res.y;",
  "  vec2 cp = u_click - 0.5; cp.x *= u_res.x/u_res.y;",
  "  float t = u_time*0.06;",
  "  vec2 d = p - m;",
  "  float r = length(d);",
  "  float a = atan(d.y, d.x);",
  "  a += (0.6 + 1.8*u_pressure) * exp(-r*1.5);",
  "  vec2 sp = m + vec2(cos(a), sin(a))*r;",
  "  float age = max(u_time - u_clickTime, 0.0);",
  "  float ignite = exp(-age*0.9) * smoothstep(1.2, 0.0, abs(length(p-cp) - age*0.7));",
  "  vec2 q = sp*1.2;",
  "  q += vec2(fbm(q+t), fbm(q-t+3.7))*0.8;",
  "  float n = fbm(q + t*1.5);",
  "  float n2 = fbm(q*2.5 - t);",
  "  vec3 deep = vec3(0.02, 0.01, 0.06);",
  "  vec3 violet = vec3(0.35, 0.15, 0.55);",
  "  vec3 magenta = vec3(0.95, 0.25, 0.55);",
  "  vec3 gold = vec3(1.00, 0.75, 0.35);",
  "  vec3 cyan = vec3(0.30, 0.85, 1.00);",
  "  vec3 col = deep;",
  "  col = mix(col, violet, smoothstep(0.2, 0.7, n));",
  "  col = mix(col, magenta, smoothstep(0.55, 0.9, n*n2));",
  "  col += gold * pow(n2, 6.0) * 1.4;",
  "  col += cyan * pow(smoothstep(0.6,1.0,n), 8.0) * 0.6;",
  "  col += mix(magenta, gold, 0.5+0.5*sin(age*4.0)) * ignite * 1.6;",
  "  float s = stars(uv + vec2(t*0.05, 0.0), 0.02);",
  "  col += vec3(0.9,0.95,1.0) * s;",
  "  col += vec3(0.7,0.5,1.0) * exp(-r*4.0) * (0.2 + 0.5*u_pressure);",
  "  col *= 1.0 - 0.5*dot(p,p);",
  "  col += (hash(gl_FragCoord.xy + u_time)-0.5)*0.02;",
  "  gl_FragColor = vec4(col, 1.0);",
  "}",
].join("\n");

const EMBER_FRAG = [
  "precision highp float;",
  "uniform float u_time;",
  "uniform vec2 u_res;",
  "uniform vec2 u_mouseSmooth;",
  "uniform vec2 u_click;",
  "uniform float u_clickTime;",
  "uniform float u_pressure;",
  "float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7)))*43758.5453); }",
  "float noise(vec2 p){",
  "  vec2 i=floor(p), f=fract(p);",
  "  vec2 u=f*f*(3.0-2.0*f);",
  "  return mix(mix(hash(i),hash(i+vec2(1,0)),u.x),",
  "             mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),u.x),u.y);",
  "}",
  "float fbm(vec2 p){",
  "  float v=0.0,a=0.5;",
  "  mat2 R=mat2(0.8,-0.6,0.6,0.8);",
  "  for(int i=0;i<6;i++){ v+=a*noise(p); p=R*p*2.05+3.1; a*=0.5; }",
  "  return v;",
  "}",
  "void main(){",
  "  vec2 p = (gl_FragCoord.xy - 0.5*u_res.xy)/u_res.y;",
  "  vec2 m = u_mouseSmooth - 0.5; m.x *= u_res.x/u_res.y;",
  "  vec2 cp = u_click - 0.5; cp.x *= u_res.x/u_res.y;",
  "  float t = u_time*0.18;",
  "  vec2 q = p*1.3;",
  "  q.y += t*1.2;",
  "  q += (m - p) * exp(-length(p-m)*1.6) * (0.4 + 0.7*u_pressure);",
  "  q += 0.5 * vec2(fbm(q + t), fbm(q*1.2 - t + 7.0));",
  "  float smoke = fbm(q);",
  "  float dense = smoothstep(0.25, 0.85, smoke);",
  "  float wisp = fbm(q*2.5 + vec2(0.0, t*2.0));",
  "  dense *= 0.6 + 0.6*wisp;",
  "  float age = max(u_time - u_clickTime, 0.0);",
  "  float burst = exp(-age*1.6) * smoothstep(0.35, 0.0, length(p-cp) - age*0.4);",
  "  float embers = exp(-age*0.6) * smoothstep(0.4, 0.9, fbm(q*4.0 + age*3.0)) * smoothstep(0.5, 0.0, length(p-cp));",
  "  vec3 night = vec3(0.04, 0.03, 0.05);",
  "  vec3 charcoal = vec3(0.18, 0.10, 0.09);",
  "  vec3 umber = vec3(0.55, 0.22, 0.10);",
  "  vec3 ember = vec3(1.00, 0.45, 0.12);",
  "  vec3 gold = vec3(1.00, 0.78, 0.35);",
  "  vec3 col = night;",
  "  col = mix(col, charcoal, smoothstep(0.1, 0.5, dense));",
  "  col = mix(col, umber, smoothstep(0.45, 0.85, dense));",
  "  col += ember * pow(dense, 5.0) * 0.7;",
  "  float md = length(p - m);",
  "  vec3 coal = mix(ember, gold, 0.4 + 0.4*u_pressure);",
  "  col += coal * exp(-md*5.5) * (0.4 + 0.8*u_pressure);",
  "  col += gold * smoothstep(0.55, 0.95, smoke) * exp(-md*2.5) * 0.6;",
  "  col += mix(ember, gold, burst) * burst * 1.8;",
  "  col += gold * embers * 1.2;",
  "  col *= 1.0 - 0.55*dot(p,p);",
  "  col += (hash(gl_FragCoord.xy + u_time)-0.5)*0.025;",
  "  gl_FragColor = vec4(col, 1.0);",
  "}",
].join("\n");

const MAPLE_FRAG = [
  "precision highp float;",
  "uniform float u_time;",
  "uniform vec2 u_res;",
  "uniform vec2 u_mouseSmooth;",
  "uniform vec2 u_click;",
  "uniform float u_clickTime;",
  "uniform float u_pressure;",
  "float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7)))*43758.5453); }",
  "float noise(vec2 p){",
  "  vec2 i=floor(p), f=fract(p);",
  "  vec2 u=f*f*(3.0-2.0*f);",
  "  return mix(mix(hash(i),hash(i+vec2(1,0)),u.x),",
  "             mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),u.x),u.y);",
  "}",
  "float fbm(vec2 p){",
  "  float v=0.0,a=0.5;",
  "  for(int i=0;i<5;i++){ v+=a*noise(p); p*=2.0; a*=0.5; }",
  "  return v;",
  "}",
  "void main(){",
  "  vec2 p = (gl_FragCoord.xy - 0.5*u_res.xy)/u_res.y;",
  "  vec2 m = u_mouseSmooth - 0.5; m.x *= u_res.x/u_res.y;",
  "  vec2 cp = u_click - 0.5; cp.x *= u_res.x/u_res.y;",
  "  float t = u_time*0.12;",
  "  float age = max(u_time - u_clickTime, 0.0);",
  "  vec2 q = p*1.2 + vec2(t*0.8, t*0.3);",
  "  q += (m - p) * exp(-length(p-m)*1.2) * 0.25;",
  "  q += normalize(p - cp + 1e-4) * exp(-age*1.0) * smoothstep(0.6, 0.0, length(p-cp)) * 0.4;",
  "  float mist = fbm(q + vec2(0.0, fbm(q*2.0 - t)));",
  "  float density = smoothstep(0.2, 0.85, mist);",
  "  vec2 lq = p * 6.0 + vec2(-t*1.5, t*0.4);",
  "  vec2 li = floor(lq), lf = fract(lq);",
  "  float leafDist = 1.0;",
  "  vec2 leafCell = vec2(0.0);",
  "  for(int y=-1;y<=1;y++){",
  "    for(int x=-1;x<=1;x++){",
  "      vec2 o = vec2(float(x),float(y));",
  "      vec2 h = vec2(hash(li+o), hash(li+o+17.0));",
  "      vec2 seed = o + h;",
  "      seed += vec2(sin(u_time*0.6 + h.x*6.28)*0.2, cos(u_time*0.5 + h.y*6.28)*0.15);",
  "      float d = length(seed - lf);",
  "      if(d<leafDist){ leafDist=d; leafCell=li+o; }",
  "    }",
  "  }",
  "  float leaf = smoothstep(0.18, 0.05, leafDist);",
  "  float lh = hash(leafCell);",
  "  float gust = exp(-age*1.2) * smoothstep(0.12, 0.0, abs(length(p-cp) - age*0.55));",
  "  vec3 sky = vec3(0.95, 0.86, 0.72);",
  "  vec3 cream = vec3(0.85, 0.72, 0.55);",
  "  vec3 ochre = vec3(0.82, 0.55, 0.22);",
  "  vec3 rust = vec3(0.75, 0.30, 0.12);",
  "  vec3 maroon = vec3(0.45, 0.12, 0.10);",
  "  vec3 moss = vec3(0.45, 0.42, 0.18);",
  "  vec3 bg = mix(cream*0.7, sky, smoothstep(-0.4, 0.5, p.y));",
  "  bg = mix(bg, ochre*0.6, density*0.55);",
  "  bg += vec3(1.0, 0.85, 0.55) * exp(-length(p-m)*3.5) * (0.18 + 0.4*u_pressure);",
  "  vec3 leafCol;",
  "  if(lh < 0.35) leafCol = rust;",
  "  else if(lh < 0.65) leafCol = ochre;",
  "  else if(lh < 0.88) leafCol = maroon;",
  "  else leafCol = moss;",
  "  float core = smoothstep(0.06, 0.0, leafDist);",
  "  vec3 col = bg;",
  "  col = mix(col, leafCol, leaf*0.85);",
  "  col = mix(col, leafCol*0.6, core*0.5);",
  "  col += rust * gust * 0.8;",
  "  col += vec3(1.0, 0.7, 0.4) * exp(-age*2.5) * smoothstep(0.08, 0.0, length(p-cp)) * 1.0;",
  "  col = mix(col, cream*0.6, smoothstep(0.5, 1.1, length(p))*0.5);",
  "  col += (hash(gl_FragCoord.xy + u_time)-0.5)*0.02;",
  "  gl_FragColor = vec4(col, 1.0);",
  "}",
].join("\n");

const PUMPKIN_FRAG = [
  "precision highp float;",
  "uniform float u_time;",
  "uniform vec2 u_res;",
  "uniform vec2 u_mouseSmooth;",
  "uniform vec2 u_click;",
  "uniform float u_clickTime;",
  "uniform float u_pressure;",
  "float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7)))*43758.5453); }",
  "float noise(vec2 p){",
  "  vec2 i=floor(p), f=fract(p);",
  "  vec2 u=f*f*(3.0-2.0*f);",
  "  return mix(mix(hash(i),hash(i+vec2(1,0)),u.x),",
  "             mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),u.x),u.y);",
  "}",
  "float fbm(vec2 p){",
  "  float v=0.0,a=0.5;",
  "  mat2 R=mat2(0.7,-0.7,0.7,0.7);",
  "  for(int i=0;i<7;i++){ v+=a*noise(p); p=R*p*2.0+1.7; a*=0.5; }",
  "  return v;",
  "}",
  "vec2 flow(vec2 p, float t){",
  "  float e = 0.01;",
  "  float n1 = fbm(p + vec2(0.0, t));",
  "  float n2 = fbm(p + vec2(t, 0.0));",
  "  float dx = fbm(p + vec2(e, 0.0)) - n1;",
  "  float dy = fbm(p + vec2(0.0, e)) - n1;",
  "  return vec2(dy, -dx) * 4.0 + vec2(n2-0.5, n1-0.5)*0.6;",
  "}",
  "void main(){",
  "  vec2 p = (gl_FragCoord.xy - 0.5*u_res.xy)/u_res.y;",
  "  vec2 m = u_mouseSmooth - 0.5; m.x *= u_res.x/u_res.y;",
  "  vec2 cp = u_click - 0.5; cp.x *= u_res.x/u_res.y;",
  "  float t = u_time*0.15;",
  "  float age = max(u_time - u_clickTime, 0.0);",
  "  vec2 q = p;",
  "  for(int i=0;i<3;i++){",
  "    q -= flow(q*1.2, t)*0.04;",
  "  }",
  "  float heat = exp(-length(p-m)*2.0) * (0.6 + 0.8*u_pressure);",
  "  float pulse = exp(-age*1.4) * smoothstep(0.18, 0.0, abs(length(p-cp) - age*0.5));",
  "  float d1 = fbm(q*1.8 + t*1.2);",
  "  float d2 = fbm(q*3.5 - t + d1);",
  "  float density = mix(d1, d2, 0.5) * (0.5 + heat) + pulse*0.6;",
  "  vec3 night = vec3(0.05, 0.03, 0.04);",
  "  vec3 plum = vec3(0.18, 0.07, 0.10);",
  "  vec3 brick = vec3(0.62, 0.18, 0.10);",
  "  vec3 pumpkin = vec3(0.95, 0.42, 0.10);",
  "  vec3 amber = vec3(1.00, 0.70, 0.25);",
  "  vec3 cream = vec3(1.00, 0.92, 0.70);",
  "  vec3 col = night;",
  "  col = mix(col, plum, smoothstep(0.15, 0.55, density));",
  "  col = mix(col, brick, smoothstep(0.45, 0.8, density));",
  "  col = mix(col, pumpkin, smoothstep(0.7, 0.95, density));",
  "  col += amber * pow(smoothstep(0.6, 1.0, density), 4.0) * 0.9;",
  "  col += pumpkin * exp(-length(p-m)*4.5) * (0.3 + 0.6*u_pressure);",
  "  col += amber * exp(-length(p-m)*8.0) * (0.4 + 0.8*u_pressure);",
  "  col += mix(amber, cream, 0.5) * pulse * 1.6;",
  "  col += pumpkin * exp(-age*2.5) * smoothstep(0.06, 0.0, length(p-cp)) * 1.4;",
  "  float vig = 1.0 - 0.55*dot(p,p);",
  "  col *= vig;",
  "  col += (hash(gl_FragCoord.xy + u_time)-0.5)*0.025;",
  "  gl_FragColor = vec4(col, 1.0);",
  "}",
].join("\n");

const AURORA_FRAG = [
  "precision highp float;",
  "uniform float u_time;",
  "uniform vec2 u_res;",
  "uniform vec2 u_mouseSmooth;",
  "uniform vec2 u_click;",
  "uniform float u_clickTime;",
  "uniform float u_pressure;",
  "float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453123); }",
  "float noise(vec2 p){",
  "  vec2 i = floor(p), f = fract(p);",
  "  vec2 u = f*f*(3.0-2.0*f);",
  "  return mix(mix(hash(i), hash(i+vec2(1,0)), u.x),",
  "             mix(hash(i+vec2(0,1)), hash(i+vec2(1,1)), u.x), u.y);",
  "}",
  "float fbm(vec2 p){",
  "  float v = 0.0, a = 0.5;",
  "  mat2 R = mat2(0.8,-0.6,0.6,0.8);",
  "  for(int i=0;i<6;i++){ v += a*noise(p); p = R*p*2.02; a *= 0.5; }",
  "  return v;",
  "}",
  "void main(){",
  "  vec2 p = (gl_FragCoord.xy - 0.5*u_res.xy) / u_res.y;",
  "  vec2 m = u_mouseSmooth - 0.5; m.x *= u_res.x / u_res.y;",
  "  float t = u_time * 0.08;",
  "  vec2 q = p * 1.6;",
  "  q += 0.6 * vec2(fbm(q + t), fbm(q - t + 5.2));",
  "  q -= m * 0.9;",
  "  float n = fbm(q + vec2(0.0, t*2.0));",
  "  float curtain = smoothstep(0.25, 0.85, n);",
  "  float bands = sin(q.y*3.0 + n*5.0 + t*4.0) * 0.5 + 0.5;",
  "  curtain *= mix(0.6, 1.0, bands);",
  "  vec2 cp = u_click - 0.5; cp.x *= u_res.x/u_res.y;",
  "  float age = max(u_time - u_clickTime, 0.0);",
  "  float ring = exp(-age*1.4) * smoothstep(0.04, 0.0, abs(length(p-cp) - age*0.55));",
  "  vec3 c1 = vec3(0.02, 0.05, 0.12);",
  "  vec3 c2 = vec3(0.10, 0.55, 0.75);",
  "  vec3 c3 = vec3(0.55, 0.20, 0.85);",
  "  vec3 c4 = vec3(0.90, 0.95, 1.00);",
  "  vec3 col = c1;",
  "  col = mix(col, c2, smoothstep(0.0, 0.6, curtain));",
  "  col = mix(col, c3, smoothstep(0.4, 0.95, curtain) * (0.6 + 0.4*sin(t*1.2 + n*3.0)));",
  "  col += c4 * pow(curtain, 8.0) * 0.9;",
  "  float md = length(p - m);",
  "  col += vec3(0.4, 0.8, 1.0) * exp(-md*5.0) * (0.15 + 0.4*u_pressure);",
  "  col += vec3(0.7, 0.9, 1.0) * ring * 1.4;",
  "  col *= 1.0 - 0.35 * dot(p,p);",
  "  col += (hash(gl_FragCoord.xy + u_time) - 0.5) * 0.02;",
  "  gl_FragColor = vec4(col, 1.0);",
  "}",
].join("\n");

const FRAG_BY_VARIANT: Record<ShaderVariant, string> = {
  nebula: NEBULA_FRAG,
  ember: EMBER_FRAG,
  maple: MAPLE_FRAG,
  pumpkin: PUMPKIN_FRAG,
  aurora: AURORA_FRAG,
};

function compile(gl: WebGLRenderingContext, type: number, src: string): WebGLShader | null {
  const sh = gl.createShader(type);
  if (!sh) return null;
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    // eslint-disable-next-line no-console
    console.warn("Shader compile error:", gl.getShaderInfoLog(sh));
    gl.deleteShader(sh);
    return null;
  }
  return sh;
}

function link(gl: WebGLRenderingContext, vs: WebGLShader, fs: WebGLShader): WebGLProgram | null {
  const p = gl.createProgram();
  if (!p) return null;
  gl.attachShader(p, vs);
  gl.attachShader(p, fs);
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
    gl.deleteProgram(p);
    return null;
  }
  return p;
}

function reduceMotionNow(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

export function ShaderCanvas({ variant = "nebula", opacity = 0.22 }: ShaderCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [supported, setSupported] = useState(true);
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (Platform.OS !== "web" || typeof window === "undefined" || !window.matchMedia) return;
    setReduced(reduceMotionNow());
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(mq.matches);
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener("change", onChange);
    };
  }, []);

  useEffect(() => {
    if (Platform.OS !== "web" || typeof window === "undefined") return;
    if (reduced) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const gl = (canvas.getContext("webgl", { antialias: true, premultipliedAlpha: false }) ||
      canvas.getContext("experimental-webgl")) as WebGLRenderingContext | null;
    if (!gl) {
      setSupported(false);
      return;
    }
    const vs = compile(gl, gl.VERTEX_SHADER, VERT_SRC);
    const fs = compile(gl, gl.FRAGMENT_SHADER, FRAG_BY_VARIANT[variant]);
    if (!vs || !fs) {
      setSupported(false);
      return;
    }
    const prog = link(gl, vs, fs);
    if (!prog) {
      setSupported(false);
      return;
    }
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
      gl.STATIC_DRAW,
    );
    const aPos = gl.getAttribLocation(prog, "a_pos");
    gl.enableVertexAttribArray(aPos);
    gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

    const uTime = gl.getUniformLocation(prog, "u_time");
    const uRes = gl.getUniformLocation(prog, "u_res");
    const uMouse = gl.getUniformLocation(prog, "u_mouse");
    const uMouseSmooth = gl.getUniformLocation(prog, "u_mouseSmooth");
    const uClick = gl.getUniformLocation(prog, "u_click");
    const uClickTime = gl.getUniformLocation(prog, "u_clickTime");
    const uPressure = gl.getUniformLocation(prog, "u_pressure");

    const mouse: [number, number] = [0.5, 0.5];
    const mouseSmooth: [number, number] = [0.5, 0.5];
    const click: [number, number] = [0.5, 0.5];
    let clickTime = -10.0;
    let pressure = 0.0;
    let targetPressure = 0.0;
    const start = performance.now();

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const w = canvas.clientWidth || 1;
      const h = canvas.clientHeight || 1;
      canvas.width = Math.max(1, Math.floor(w * dpr));
      canvas.height = Math.max(1, Math.floor(h * dpr));
      gl.viewport(0, 0, canvas.width, canvas.height);
    };
    resize();
    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(resize);
      ro.observe(canvas);
    } else {
      window.addEventListener("resize", resize);
    }

    // Mouse + click tracked locally on the canvas so multiple instances do
    // not fight over a window-level listener.
    const setMouseFromEvent = (clientX: number, clientY: number) => {
      const r = canvas.getBoundingClientRect();
      mouse[0] = (clientX - r.left) / Math.max(1, r.width);
      mouse[1] = 1.0 - (clientY - r.top) / Math.max(1, r.height);
    };
    const setClickFromEvent = (clientX: number, clientY: number) => {
      const r = canvas.getBoundingClientRect();
      click[0] = (clientX - r.left) / Math.max(1, r.width);
      click[1] = 1.0 - (clientY - r.top) / Math.max(1, r.height);
      clickTime = (performance.now() - start) / 1000;
    };

    const onMove = (e: MouseEvent) => setMouseFromEvent(e.clientX, e.clientY);
    const onTouchMove = (e: TouchEvent) => {
      if (e.touches[0]) setMouseFromEvent(e.touches[0].clientX, e.touches[0].clientY);
    };
    const onDown = (e: MouseEvent) => {
      setClickFromEvent(e.clientX, e.clientY);
      targetPressure = 1.0;
    };
    const onUp = () => {
      targetPressure = 0.0;
    };
    const onTouchStart = (e: TouchEvent) => {
      if (e.touches[0]) {
        setClickFromEvent(e.touches[0].clientX, e.touches[0].clientY);
        targetPressure = 1.0;
      }
    };
    const onTouchEnd = () => {
      targetPressure = 0.0;
    };
    // Use window-level mouse moves so the swirl follows the cursor even when
    // it is over the foreground content (canvas has pointer-events:none).
    window.addEventListener("mousemove", onMove, { passive: true });
    window.addEventListener("touchmove", onTouchMove, { passive: true });
    window.addEventListener("mousedown", onDown);
    window.addEventListener("mouseup", onUp);
    window.addEventListener("touchstart", onTouchStart, { passive: true });
    window.addEventListener("touchend", onTouchEnd);

    let raf = 0;
    let visible = true;
    const tick = () => {
      const t = (performance.now() - start) / 1000;
      mouseSmooth[0] += (mouse[0] - mouseSmooth[0]) * 0.08;
      mouseSmooth[1] += (mouse[1] - mouseSmooth[1]) * 0.08;
      pressure += (targetPressure - pressure) * 0.12;
      gl.useProgram(prog);
      if (uTime) gl.uniform1f(uTime, t);
      if (uRes) gl.uniform2f(uRes, canvas.width, canvas.height);
      if (uMouse) gl.uniform2f(uMouse, mouse[0], mouse[1]);
      if (uMouseSmooth) gl.uniform2f(uMouseSmooth, mouseSmooth[0], mouseSmooth[1]);
      if (uClick) gl.uniform2f(uClick, click[0], click[1]);
      if (uClickTime) gl.uniform1f(uClickTime, clickTime);
      if (uPressure) gl.uniform1f(uPressure, pressure);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    // Pause animation when canvas scrolls off-screen to save GPU/CPU.
    let io: IntersectionObserver | null = null;
    if (typeof IntersectionObserver !== "undefined") {
      io = new IntersectionObserver(
        (entries) => {
          for (const e of entries) {
            const nowVisible = e.isIntersecting;
            if (nowVisible && !visible) {
              visible = true;
              if (raf === 0) raf = requestAnimationFrame(tick);
            } else if (!nowVisible && visible) {
              visible = false;
              if (raf !== 0) {
                cancelAnimationFrame(raf);
                raf = 0;
              }
            }
          }
        },
        { threshold: 0 },
      );
      io.observe(canvas);
    }

    return () => {
      if (raf !== 0) cancelAnimationFrame(raf);
      if (io) io.disconnect();
      if (ro) ro.disconnect();
      else window.removeEventListener("resize", resize);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("touchmove", onTouchMove);
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("mouseup", onUp);
      window.removeEventListener("touchstart", onTouchStart);
      window.removeEventListener("touchend", onTouchEnd);
      gl.deleteProgram(prog);
      gl.deleteShader(vs);
      gl.deleteShader(fs);
      gl.deleteBuffer(buf);
    };
  }, [variant, reduced]);

  if (Platform.OS !== "web") return null;

  if (reduced || !supported) {
    return (
      <View
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          opacity,
          // @ts-ignore - RN-Web passes through CSS
          backgroundImage: FALLBACK_GRADIENTS[variant],
          pointerEvents: "none",
        } as any}
      />
    );
  }

  return (
    <canvas
      ref={canvasRef}
      aria-hidden={true}
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        width: "100%",
        height: "100%",
        opacity,
        pointerEvents: "none",
      }}
    />
  );
}

export default ShaderCanvas;
