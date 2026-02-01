import { Platform } from "react-native";

export type BackendUrlSource = "runtime" | "default" | "unavailable";

export type BackendUrlInfo = {
  url: string;
  source: BackendUrlSource;
  warning?: string;
};

const fallback = "http://localhost:8000";

export function getBackendUrlInfo(): BackendUrlInfo {
  if (Platform.OS !== "web") {
    return {
      url: fallback,
      source: "unavailable",
    };
  }

  return {
    url: fallback,
    source: "default",
  };
}

export async function fetchBackendUrlInfo(): Promise<BackendUrlInfo> {
  if (Platform.OS !== "web") {
    return { url: fallback, source: "unavailable" };
  }

  try {
    const response = await fetch("/backend_url.txt", { cache: "no-store" });
    if (response.ok) {
      const value = (await response.text()).trim();
      if (value && /^https?:\/\/.+/i.test(value)) {
        return { url: value.replace(/\/$/, ""), source: "runtime" };
      }
    }
  } catch (error) {
    return {
      url: fallback,
      source: "default",
      warning: "Unable to reach backend_url.txt. Using default http://localhost:8000.",
    };
  }

  return {
    url: fallback,
    source: "default",
  };
}
