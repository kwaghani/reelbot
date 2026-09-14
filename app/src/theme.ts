import { useSyncExternalStore } from 'react';
import { AccessibilityInfo, useColorScheme } from 'react-native';
export const lightColors = {
  background: '#E9EDE7', card: '#F7F9F4', cardMuted: '#DCE3DA', border: '#BEC9BE', borderStrong: '#718577',
  textPrimary: '#12211B', textSecondary: '#526157', accent: '#C0316B', accentPressed: '#882044', accentSoft: '#F3D9E4',
  ink: '#12211B', inkText: '#FFFFFF', red: '#A63242', success: '#315E41', successSoft: '#D7E5D7',
  warningBackground: '#E8E1CC', warningBorder: '#A59764', warningText: '#60502B', danger: '#9E293D', dangerSoft: '#F4DDE2', water: '#9FBFC9',
};
export const darkColors: typeof lightColors = {
  background: '#15231E', card: '#1C2D26', cardMuted: '#2B4035', border: '#3B5144', borderStrong: '#839E8D',
  textPrimary: '#E4EBE1', textSecondary: '#ABBCAF', accent: '#F08AB2', accentPressed: '#F6B2CB', accentSoft: '#45273A',
  ink: '#E4EBE1', inkText: '#15231E', red: '#F1A3B0', success: '#A5D7B0', successSoft: '#243E2C',
  warningBackground: '#3E3829', warningBorder: '#A99B6D', warningText: '#E2D2A0', danger: '#F4A5B4', dangerSoft: '#452832', water: '#243E47',
};
export const colors = lightColors;
export function useTheme() { return useColorScheme() === 'dark' ? darkColors : lightColors; }
let reducedMotion = true;
const motionListeners = new Set<() => void>();
let motionSubscription: ReturnType<typeof AccessibilityInfo.addEventListener> | undefined;
function updateMotion(value: boolean) { reducedMotion = value; motionListeners.forEach(listener => listener()); }
function subscribeMotion(listener: () => void) {
  motionListeners.add(listener);
  if (!motionSubscription) { motionSubscription = AccessibilityInfo.addEventListener('reduceMotionChanged', updateMotion); void AccessibilityInfo.isReduceMotionEnabled().then(updateMotion).catch(() => {}); }
  return () => { motionListeners.delete(listener); if (!motionListeners.size) { motionSubscription?.remove(); motionSubscription = undefined; } };
}
export function useReducedMotion() { return useSyncExternalStore(subscribeMotion, () => reducedMotion, () => true); }
export const fonts = { regular: 'Switzer-Regular', semibold: 'Switzer-Semibold', bold: 'Switzer-Semibold', display: 'CabinetGrotesk-Bold' };
export const spacing = { xxs: 4, xs: 8, sm: 12, md: 16, lg: 24, xl: 32 };
export const radius = { card: 3, tile: 2, button: 3, chip: 6 };
export const folderTints = [
  { background: '#CEDDE0', foreground: '#254651' },
  { background: '#D8DFCD', foreground: '#3D5130' },
  { background: '#E4D6DC', foreground: '#68364C' },
];
export function folderTint(name: string, palette = lightColors) { let hash = 0; for (const letter of name) hash = (hash * 31 + letter.charCodeAt(0)) >>> 0; return palette === darkColors ? [{ background: '#243E47', foreground: '#BDD3DB' }, { background: '#30442D', foreground: '#D0DDC6' }, { background: '#452F3B', foreground: '#E6C8D5' }][hash % 3] : folderTints[hash % folderTints.length]; }
export const typeScale = {
  large: { fontFamily: fonts.display, fontSize: 34, letterSpacing: -.6, lineHeight: 39 },
  title: { fontFamily: fonts.display, fontSize: 23, letterSpacing: -.25, lineHeight: 29 },
  section: { fontFamily: fonts.display, fontSize: 20, letterSpacing: -.15, lineHeight: 26 },
  navigation: { fontFamily: fonts.semibold, fontSize: 17, letterSpacing: 0, lineHeight: 23 },
  body: { fontFamily: fonts.regular, fontSize: 16, letterSpacing: 0, lineHeight: 23 },
  card: { fontFamily: fonts.semibold, fontSize: 15, letterSpacing: 0, lineHeight: 21 },
  label: { fontFamily: fonts.semibold, fontSize: 13, letterSpacing: 0, lineHeight: 18 },
  meta: { fontFamily: fonts.regular, fontSize: 12, letterSpacing: 0, lineHeight: 17 },
  legend: { fontFamily: fonts.semibold, fontSize: 11, letterSpacing: .1, lineHeight: 15 },
};
