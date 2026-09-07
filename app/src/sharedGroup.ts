import SharedGroupPreferences from "react-native-shared-group-preferences";

import { getDeviceCredentials } from "./api";
import { appConfig } from "./config";

export const SHARE_SETTINGS_KEY = "reelbot.shareSettings";
export const SHARE_RECEIPT_KEY = "reelbot.lastShareReceipt";

export type ShareSettings = {
  apiUrl: string;
  apiKey: string;
  userName: string;
  testGroupId: string;
  deviceId?: string;
  deviceToken?: string;
  activeGroupId?: string;
  activeGroupName?: string;
};

export type ActiveGroup = {
  id: string;
  name: string;
};

export type ShareReceipt = {
  url: string;
  createdAt: number;
  status: "queued";
  jobId?: string;
  groupId?: string;
};

// The installed native preferences bridge can return the JSON string despite
// its generic object type declaration. Accept both bridge representations.
function decodePreference<T>(value: unknown): Partial<T> | null {
  const parsed: unknown = typeof value === "string" ? JSON.parse(value) : value;
  return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Partial<T> : null;
}

export async function writeShareSettings(
  userName: string,
  extras: { deviceId?: string; activeGroup?: ActiveGroup | null } = {}
): Promise<void> {
  if (!appConfig.appGroupIdentifier) {
    throw new Error("App Group is missing in app config.");
  }

  const settings: ShareSettings = {
    apiUrl: appConfig.apiUrl,
    apiKey: appConfig.apiKey,
    userName,
    testGroupId: appConfig.testGroupId,
    deviceId: extras.deviceId,
    deviceToken: getDeviceCredentials().token,
    activeGroupId: extras.activeGroup?.id,
    activeGroupName: extras.activeGroup?.name
  };

  await SharedGroupPreferences.setItem(
    SHARE_SETTINGS_KEY,
    settings,
    appConfig.appGroupIdentifier
  );
}

export async function readShareSettings(): Promise<ShareSettings | null> {
  if (!appConfig.appGroupIdentifier) {
    return null;
  }

  try {
    const settings = decodePreference<ShareSettings>(await SharedGroupPreferences.getItem<unknown>(
      SHARE_SETTINGS_KEY,
      appConfig.appGroupIdentifier
    ));

    if (!settings?.apiUrl || !settings.apiKey || !settings.userName || !settings.testGroupId) {
      return null;
    }

    return {
      apiUrl: settings.apiUrl.replace(/\/+$/, ""),
      apiKey: settings.apiKey,
      userName: settings.userName,
      testGroupId: settings.testGroupId ?? "",
      deviceId: settings.deviceId,
      deviceToken: settings.deviceToken,
      activeGroupId: settings.activeGroupId,
      activeGroupName: settings.activeGroupName
    };
  } catch {
    return null;
  }
}

export async function writeLastShareReceipt(url: string, jobId: string, groupId: string): Promise<void> {
  if (!appConfig.appGroupIdentifier) {
    return;
  }

  const receipt: ShareReceipt = {
    url,
    createdAt: Date.now(),
    status: "queued",
    jobId,
    groupId
  };

  await SharedGroupPreferences.setItem(
    SHARE_RECEIPT_KEY,
    receipt,
    appConfig.appGroupIdentifier
  );
}

export async function readLastShareReceipt(): Promise<ShareReceipt | null> {
  if (!appConfig.appGroupIdentifier) {
    return null;
  }

  try {
    const receipt = decodePreference<ShareReceipt>(await SharedGroupPreferences.getItem<unknown>(
      SHARE_RECEIPT_KEY,
      appConfig.appGroupIdentifier
    ));

    if (!receipt?.url || typeof receipt.createdAt !== "number") {
      return null;
    }

    return {
      url: receipt.url,
      createdAt: receipt.createdAt,
      status: "queued",
      jobId: receipt.jobId,
      groupId: receipt.groupId
    };
  } catch {
    return null;
  }
}
