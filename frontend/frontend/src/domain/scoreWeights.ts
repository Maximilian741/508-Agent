/**
 * User-tunable severity weights used by the live audit score.
 *
 * Default {error: 2, warning: 1, info: 0} matches the backend formula in
 * `_build_score()`.  Teams whose reviewers care more (or less) about
 * warnings can override.  Stored in `localStorage` so the choice sticks
 * across sessions on the same browser.
 */

import { Platform } from "react-native";

export interface ScoreWeights {
  error: number;
  warning: number;
  info: number;
}

const STORAGE_KEY = "508-score-weights-v1";

export const DEFAULT_WEIGHTS: ScoreWeights = { error: 2, warning: 1, info: 0 };

export function loadWeights(): ScoreWeights {
  if (Platform.OS !== "web") return { ...DEFAULT_WEIGHTS };
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_WEIGHTS };
    const parsed = JSON.parse(raw);
    return {
      error: clamp(parsed.error, 0, 10, DEFAULT_WEIGHTS.error),
      warning: clamp(parsed.warning, 0, 10, DEFAULT_WEIGHTS.warning),
      info: clamp(parsed.info, 0, 10, DEFAULT_WEIGHTS.info),
    };
  } catch {
    return { ...DEFAULT_WEIGHTS };
  }
}

export function saveWeights(weights: ScoreWeights): void {
  if (Platform.OS !== "web") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(weights));
  } catch {
    // ignore quota
  }
}

export function resetWeights(): ScoreWeights {
  saveWeights(DEFAULT_WEIGHTS);
  return { ...DEFAULT_WEIGHTS };
}

function clamp(n: any, min: number, max: number, fallback: number): number {
  if (typeof n !== "number" || !Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}
