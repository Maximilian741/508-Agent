/**
 * Browser notifications + a tiny success chime.
 *
 * Both are opt-in.  We intentionally don't prompt for permission until the
 * user takes an action that explicitly enables notifications, to avoid the
 * "Allow notifications?" pop-up appearing on page load.
 */

import { Platform } from "react-native";

const NOTIFY_KEY = "508-notify-enabled";

export function notificationsAvailable(): boolean {
  return (
    Platform.OS === "web" &&
    typeof window !== "undefined" &&
    "Notification" in window
  );
}

export function notificationsEnabled(): boolean {
  if (Platform.OS !== "web") return false;
  try {
    return window.localStorage.getItem(NOTIFY_KEY) === "1";
  } catch {
    return false;
  }
}

export function setNotificationsEnabled(enabled: boolean): void {
  if (Platform.OS !== "web") return;
  try {
    if (enabled) window.localStorage.setItem(NOTIFY_KEY, "1");
    else window.localStorage.removeItem(NOTIFY_KEY);
  } catch {
    // ignore
  }
}

export async function requestPermission(): Promise<NotificationPermission | "unsupported"> {
  if (!notificationsAvailable()) return "unsupported";
  // @ts-ignore -- DOM type only on web
  if (Notification.permission === "granted") return "granted";
  // @ts-ignore
  return await Notification.requestPermission();
}

export function notify(title: string, body?: string): void {
  if (!notificationsAvailable()) return;
  if (!notificationsEnabled()) return;
  // @ts-ignore
  if (Notification.permission !== "granted") return;
  try {
    // @ts-ignore
    new Notification(title, { body, icon: "/favicon.png" });
  } catch {
    // browser may throw if called outside user gesture
  }
}

export function playChime(): void {
  if (Platform.OS !== "web") return;
  if (!notificationsEnabled()) return;
  if (typeof window === "undefined" || typeof (window as any).AudioContext === "undefined") {
    return;
  }
  try {
    const Ctx = (window as any).AudioContext || (window as any).webkitAudioContext;
    const ctx = new Ctx();
    const now = ctx.currentTime;
    const tones = [880, 1175];
    tones.forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0, now + i * 0.15);
      gain.gain.linearRampToValueAtTime(0.18, now + i * 0.15 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, now + i * 0.15 + 0.4);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now + i * 0.15);
      osc.stop(now + i * 0.15 + 0.42);
    });
    setTimeout(() => ctx.close(), 800);
  } catch {
    // user-gesture restrictions can throw — silently swallow
  }
}
