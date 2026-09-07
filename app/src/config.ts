import Constants from "expo-constants";

type Extra = {
  apiUrl?: string;
  apiKey?: string;
  testGroupId?: string;
  appGroupIdentifier?: string;
};

const extra = (Constants.expoConfig?.extra ?? {}) as Extra;

function cleanUrl(value: string | undefined): string {
  return (value ?? "").trim().replace(/^["']|["']$/g, "").replace(/\/+$/, "");
}

function isPlaceholder(value: string): boolean {
  return /YOUR_|your-|example\.com|replace-with|00000000-0000-0000-0000-000000000000/i.test(value);
}

export const appConfig = {
  apiUrl: cleanUrl(extra.apiUrl),
  apiKey: (extra.apiKey ?? "").trim(),
  testGroupId: (extra.testGroupId ?? "").trim(),
  appGroupIdentifier: extra.appGroupIdentifier ?? ""
};

export function getApiConfigIssue(): string | null {
  if (!appConfig.apiUrl) {
    return "API URL is missing from this build.";
  }

  if (isPlaceholder(appConfig.apiUrl)) {
    return "API URL is still a placeholder. Rebuild with the real Render URL.";
  }

  let parsed: URL;
  try {
    parsed = new URL(appConfig.apiUrl);
  } catch {
    return "API URL is not valid.";
  }

  const isLocalHttp =
    parsed.protocol === "http:" &&
    ["localhost", "127.0.0.1", "::1"].includes(parsed.hostname);

  if (parsed.protocol !== "https:" && !isLocalHttp) {
    return "API URL must be HTTPS for TestFlight.";
  }

  if (!appConfig.apiKey) {
    return "API key is missing from this build.";
  }

  if (isPlaceholder(appConfig.apiKey)) {
    return "API key is still a placeholder.";
  }

  if (!appConfig.testGroupId) {
    return "Test group ID is missing from this build.";
  }

  if (isPlaceholder(appConfig.testGroupId)) {
    return "Test group ID is still a placeholder.";
  }

  return null;
}

export function apiHostLabel(): string {
  try {
    return new URL(appConfig.apiUrl).host;
  } catch {
    return appConfig.apiUrl || "configured API";
  }
}
