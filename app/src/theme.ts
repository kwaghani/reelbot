import type { TextStyle, ViewStyle } from "react-native";

// ReelBot's palette is intentionally closer to ink on paper than a typical
// pastel SaaS theme. One saturated blue carries every interactive state.
export const colors = {
  background: "#F4F1EA",
  card: "#FFFDF8",
  cardMuted: "#ECE9E1",
  border: "#D7D2C8",
  borderStrong: "#B9B3A8",
  textPrimary: "#191915",
  textSecondary: "#605E57",
  accent: "#2454D3",
  accentPressed: "#173DA7",
  accentSoft: "#E4E9F7",
  ink: "#111A3D",
  inkText: "#FFFDF8",
  red: "#D85C48",
  success: "#23704A",
  successSoft: "#E1ECE5",
  warningBackground: "#F4E9D5",
  warningBorder: "#D6BE98",
  warningText: "#71501D",
  danger: "#A94435",
  dangerSoft: "#F2E2DE",
  chatInk: "#151719",
  chatInkPressed: "#080A0C",
  chatSurface: "#EAE7DF",
  chatSurfaceStrong: "#DCD8CF",
  chatLavender: "#E5E7F0",
  chatLavenderInk: "#3F496D",
  chatAqua: "#E1E9E5",
  chatAquaInk: "#315F4D",
  chatGold: "#EEE5D5",
  chatGoldInk: "#735B2E"
} as const;

// Category colors stay muted and print-like so content, rather than color,
// creates the visual hierarchy.
export const folderTints = [
  { background: "#E3E7F2", foreground: "#294681" },
  { background: "#E7E3DA", foreground: "#5B5549" },
  { background: "#DEE8E1", foreground: "#315E47" },
  { background: "#EEE1DC", foreground: "#7A453B" },
  { background: "#E8E1EA", foreground: "#5D4966" }
] as const;

export const spacing = {
  xxs: 4,
  xs: 8,
  sm: 12,
  md: 16,
  lg: 24,
  xl: 32
} as const;

export const radius = {
  card: 12,
  tile: 8,
  button: 8,
  chip: 5
} as const;

export const fonts = {
  regular: "Inter_400Regular",
  semibold: "Inter_600SemiBold",
  bold: "Inter_700Bold",
  display: "Georgia"
} as const;

export const typography = {
  heroTitle: {
    color: colors.textPrimary,
    fontFamily: fonts.display,
    fontSize: 28,
    fontWeight: "700",
    letterSpacing: -0.55,
    lineHeight: 34
  },
  screenTitle: {
    color: colors.textPrimary,
    fontFamily: fonts.display,
    fontSize: 27,
    fontWeight: "700",
    letterSpacing: -0.45,
    lineHeight: 33
  },
  cardTitle: {
    color: colors.textPrimary,
    fontFamily: fonts.semibold,
    fontSize: 16,
    fontWeight: "600",
    lineHeight: 22
  },
  body: {
    color: colors.textPrimary,
    fontFamily: fonts.regular,
    fontSize: 15,
    fontWeight: "400",
    lineHeight: 22
  },
  meta: {
    color: colors.textSecondary,
    fontFamily: fonts.regular,
    fontSize: 12,
    fontWeight: "400",
    lineHeight: 17
  }
} satisfies Record<string, TextStyle>;

export const shadow = {
  shadowColor: "#191915",
  shadowOffset: { width: 0, height: 2 },
  shadowOpacity: 0.035,
  shadowRadius: 5,
  elevation: 1
} satisfies ViewStyle;

export const shadowSoft = {
  shadowColor: "#191915",
  shadowOffset: { width: 0, height: 1 },
  shadowOpacity: 0.025,
  shadowRadius: 2,
  elevation: 0
} satisfies ViewStyle;

export function folderTint(name: string): (typeof folderTints)[number] {
  let hash = 0;
  for (let index = 0; index < name.length; index += 1) {
    hash = (hash * 31 + name.charCodeAt(index)) >>> 0;
  }
  return folderTints[hash % folderTints.length];
}

export const theme = {
  colors,
  spacing,
  radius,
  fonts,
  typography,
  shadow,
  shadowSoft,
  folderTint
};
