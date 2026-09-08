import type { ExpoConfig } from "expo/config";

const fs = require('node:fs');
const path = require('node:path');
const yaml = require('js-yaml');
const contentTypes = yaml.load(fs.readFileSync(path.resolve(__dirname, '../config/content_types.yaml'), 'utf8'));

const bundleIdentifier =
  process.env.EXPO_PUBLIC_IOS_BUNDLE_IDENTIFIER || "com.krishwaghani.reelbot";
const appGroupIdentifier =
  `group.${bundleIdentifier}`;
const appleTeamId =
  process.env.EXPO_PUBLIC_APPLE_TEAM_ID || process.env.APPLE_TEAM_ID || undefined;

const config: ExpoConfig = {
  owner: "kwaghani",
  name: "ReelBot",
  slug: "reelbot-ios",
  version: "1.0.0",
  platforms: ["ios"],
  orientation: "portrait",
  icon: "./assets/icon.png",
  scheme: "reelbot",
  userInterfaceStyle: "light",
  newArchEnabled: false,
  ios: {
    supportsTablet: false,
    usesAppleSignIn: true,
    bundleIdentifier,
    buildNumber: "21",
    ...(appleTeamId ? { appleTeamId } : {}),
    infoPlist: {
      AppGroupIdentifier: appGroupIdentifier,
      ITSAppUsesNonExemptEncryption: false,
      NSLocationWhenInUseUsageDescription: "Show how far your saved places are from you.",
      NSLocalNetworkUsageDescription: "Connect to the ReelBot test service on your Mac.",
      ...(process.env.EXPO_PUBLIC_API_URL?.startsWith('http://') ? {
        NSAppTransportSecurity: {
          NSAllowsLocalNetworking: true,
          NSExceptionDomains: { [new URL(process.env.EXPO_PUBLIC_API_URL).hostname]: { NSExceptionAllowsInsecureHTTPLoads: true } }
        }
      } : {})
    },
    entitlements: {
      "com.apple.security.application-groups": [appGroupIdentifier]
    }
  },
  plugins: [
    [
      "expo-splash-screen",
      {
        image: "./assets/splash.png",
        imageWidth: 88,
        resizeMode: "contain",
        backgroundColor: "#F4F1EA"
      }
    ],
    "expo-font",
    "expo-apple-authentication",
    "expo-secure-store",
    "expo-background-task",
    "expo-sqlite",
    "./plugins/withXcode26FmtFix",
    [
      "expo-share-extension",
      {
        height: 220,
        activationRules: [
          {
            type: "url",
            max: 1
          },
          {
            type: "text"
          }
        ],
        backgroundColor: {
          red: 244,
          green: 241,
          blue: 234,
          alpha: 1
        }
      }
    ],
    "./plugins/withPersonalQueue"
  ],
  extra: {
    contentTypes,
    apiUrl: process.env.EXPO_PUBLIC_API_URL || "",
    appGroupIdentifier,
    appleTeamId,
    eas: {
      projectId: "b98273c3-3b3c-421d-8a9f-ba1546e77b93"
    }
  }
};

export default config;
