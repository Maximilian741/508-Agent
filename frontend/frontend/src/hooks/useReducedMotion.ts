/**
 * useReducedMotion - SSR-safe hook around prefers-reduced-motion.
 *
 * Returns true when the user has opted out of motion. Components should skip
 * non-essential animation (tweened scales, slide-ins, confetti) when this is
 * true and snap to the final state instead.
 */
import { useEffect, useState } from "react";
import { Platform } from "react-native";

export function reducedMotionNow(): boolean {
  if (Platform.OS !== "web" || typeof window === "undefined" || !window.matchMedia) return false;
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (Platform.OS !== "web" || typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = () => setReduced(mq.matches);
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if ((mq as any).addListener) (mq as any).addListener(onChange);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener("change", onChange);
      else if ((mq as any).removeListener) (mq as any).removeListener(onChange);
    };
  }, []);

  return reduced;
}

export default useReducedMotion;
