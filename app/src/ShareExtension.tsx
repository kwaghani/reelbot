import { close, type InitialProps, Text, View } from "expo-share-extension";
import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet } from "react-native";

import { reelUrls } from "./reelUrls";
import { readShareSettings, writeLastShareReceipt } from "./sharedGroup";
import { theme } from "./theme";


type ShareProps = InitialProps & Record<string, unknown>;
type ShareState = "saving" | "success" | "error";

function collectStrings(value: unknown, output: string[], depth = 0): void {
  if (depth > 4 || value == null) {
    return;
  }

  if (typeof value === "string") {
    output.push(value);
    return;
  }

  if (Array.isArray(value)) {
    value.forEach((item) => collectStrings(item, output, depth + 1));
    return;
  }

  if (typeof value === "object") {
    Object.values(value).forEach((item) => collectStrings(item, output, depth + 1));
  }
}

function extractSupportedUrl(props: ShareProps): string | null {
  const candidates: string[] = [];
  collectStrings(props.url, candidates);
  collectStrings(props.text, candidates);
  collectStrings(props.files, candidates);
  collectStrings(props.images, candidates);
  collectStrings(props.videos, candidates);
  collectStrings(props.preprocessingResults, candidates);

  for (const candidate of candidates) {
    const url = reelUrls(candidate)[0];
    if (url) return url;
  }

  return null;
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail || `ReelBot returned ${response.status}.`;
  } catch {
    return `ReelBot returned ${response.status}.`;
  }
}

function isPlaceholder(value: string): boolean {
  return /YOUR_|your-|example\.com|replace-with|00000000-0000-0000-0000-000000000000/i.test(value);
}

function apiHostLabel(apiUrl: string): string {
  try {
    return new URL(apiUrl).host;
  } catch {
    return apiUrl || "configured API";
  }
}

function validateShareSettings(apiUrl: string, apiKey: string, testGroupId: string): string | null {
  if (isPlaceholder(apiUrl) || isPlaceholder(apiKey) || isPlaceholder(testGroupId)) {
    return "ReelBot was built with placeholder API settings.";
  }

  let parsed: URL;
  try {
    parsed = new URL(apiUrl);
  } catch {
    return "ReelBot API URL is invalid.";
  }

  const localDevelopment = parsed.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname);
  if (parsed.protocol !== "https:" && !localDevelopment) {
    return "ReelBot API URL must use HTTPS.";
  }

  return null;
}

async function resolveShareSettings() {
  const storedSettings = await readShareSettings();
  if (storedSettings?.deviceId && storedSettings.deviceToken && storedSettings.activeGroupId) {
    return storedSettings;
  }

  throw new Error("Open ReelBot and choose a library before sharing.");
}

function normalizeSaveError(error: unknown, apiUrl: string): string {
  const errorName = error && typeof error === "object" ? (error as { name?: string }).name : "";

  if (errorName === "AbortError") {
    return `Timed out reaching ${apiHostLabel(apiUrl)}.`;
  }

  if (error instanceof TypeError || (error instanceof Error && /Network request failed/i.test(error.message))) {
    return "Cannot connect right now. Check your connection and retry.";
  }

  return error instanceof Error ? error.message : "Could not send this reel.";
}

export default function ShareExtension(props: ShareProps) {
  const sharedUrl = useMemo(() => extractSupportedUrl(props), [props]);
  const [attempt, setAttempt] = useState(0);
  const [message, setMessage] = useState("Sending to ReelBot...");
  const [state, setState] = useState<ShareState>("saving");

  useEffect(() => {
    let active = true;
    let closeTimer: ReturnType<typeof setTimeout> | undefined;
    setState("saving");
    setMessage("Sending to ReelBot…");
    let apiUrlForErrors = "";
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 9000);

    async function save() {
      if (!sharedUrl) {
        setState("error");
        setMessage("No Instagram, TikTok, or YouTube link found.");
        return;
      }

      const settings = await resolveShareSettings();
      apiUrlForErrors = settings.apiUrl;

      const settingsIssue = validateShareSettings(settings.apiUrl, settings.apiKey, settings.testGroupId);
      if (settingsIssue) {
        setState("error");
        setMessage(settingsIssue);
        return;
      }

      const response = await fetch(`${settings.apiUrl}/share`, {
        method: "POST",
        signal: controller.signal,
        headers: {
          "Content-Type": "application/json",
          "x-api-key": settings.apiKey,
          Authorization: `Bearer ${settings.deviceToken}`
        },
        body: JSON.stringify({
          url: sharedUrl,
          user_name: settings.userName,
          device_id: settings.deviceId || undefined,
          group_id: settings.activeGroupId || undefined
        })
      });

      if (!response.ok) {
        throw new Error(await parseError(response));
      }

      const body = await response.json() as { status: string; job_id: string };
      if (body.status !== "queued" || !body.job_id) throw new Error("Import was not confirmed. Open ReelBot to check and retry.");
      await writeLastShareReceipt(sharedUrl, body.job_id, settings.activeGroupId || settings.testGroupId);

      if (active) {
        setState("success");
        setMessage(settings.activeGroupName ? `Queued for ${settings.activeGroupName}` : "Queued for processing");
        closeTimer = setTimeout(close, 1500);
      }
    }

    save()
      .catch((error: unknown) => {
        if (!active) {
          return;
        }

        setState("error");
        setMessage(normalizeSaveError(error, apiUrlForErrors));
      })
      .finally(() => clearTimeout(timeout));

    return () => {
      active = false;
      clearTimeout(timeout);
      if (closeTimer) clearTimeout(closeTimer);
      controller.abort();
    };
  }, [sharedUrl, attempt]);

  return (
    <View style={styles.container}>
      <View style={styles.card}>
        <View style={[styles.icon, state === "success" && styles.successIcon, state === "error" && styles.errorIcon]}>
          {state === "saving" ? (
            <ActivityIndicator color={theme.colors.accent} size="small" />
          ) : (
            <Text style={styles.iconText}>{state === "success" ? "✓" : "!"}</Text>
          )}
        </View>
        <Text style={styles.title}>{message}</Text>
        {state === "error" ? <Pressable accessibilityRole="button" onPress={() => setAttempt((value) => value + 1)} style={styles.button}><Text style={styles.buttonText}>Retry</Text></Pressable> : null}
        {state !== "success" ? (
          <Pressable accessibilityRole="button" onPress={close} style={styles.button}>
            <Text style={styles.buttonText}>Close</Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: theme.colors.background,
    padding: theme.spacing.lg
  },
  card: {
    width: "100%",
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.button,
    borderColor: theme.colors.border,
    borderWidth: 1,
    backgroundColor: theme.colors.card,
    borderTopColor: theme.colors.accent,
    borderTopWidth: 3,
    padding: theme.spacing.lg
  },
  icon: {
    width: 38,
    height: 38,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.button,
    backgroundColor: theme.colors.background,
    borderColor: theme.colors.border,
    borderWidth: 1,
    marginBottom: theme.spacing.sm
  },
  successIcon: {
    backgroundColor: theme.colors.success,
    borderColor: theme.colors.success
  },
  errorIcon: {
    backgroundColor: theme.colors.danger,
    borderColor: theme.colors.danger
  },
  iconText: {
    color: theme.colors.card,
    fontSize: 20,
    fontWeight: "700"
  },
  title: {
    color: theme.colors.textPrimary,
    fontFamily: theme.fonts.display,
    fontSize: 18,
    fontWeight: "700",
    lineHeight: 23,
    textAlign: "center"
  },
  button: {
    minHeight: 44,
    justifyContent: "center",
    borderColor: theme.colors.borderStrong,
    borderRadius: theme.radius.button,
    borderWidth: 1,
    backgroundColor: theme.colors.cardMuted,
    marginTop: theme.spacing.md,
    paddingHorizontal: theme.spacing.lg
  },
  buttonText: {
    color: theme.colors.textPrimary,
    fontSize: 15,
    fontWeight: "700"
  }
});
