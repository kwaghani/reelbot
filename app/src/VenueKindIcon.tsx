import { useTheme, useReducedMotion } from './theme';
import { createContext, useContext, useEffect, useRef } from 'react';
import { View } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSpring, withTiming } from 'react-native-reanimated';
import { motion, timing } from './theme/motion';
import { Text } from './controls';
import Svg, { Path, Circle, G } from 'react-native-svg';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import { entryKind } from './venueModel';
export { entryKind } from './venueModel';
import { appConfig } from './config';
import type { Entry, VenueKinds } from './libraryModel';

export const VenueKindsContext = createContext<VenueKinds>(appConfig.venueKinds);
export const useVenueKinds = () => useContext(VenueKindsContext);
// A single 24-unit drawing system; every surface uses this exact geometry.
const drawings: Record<string, string> = {
  'fork-knife': 'M5 3v6m3-6v6M2 3v6c0 3 6 3 6 0M5 11v10M19 21V3c-5 0-5 9 0 10',
  coffee: 'M3 8h13v6a6.5 6.5 0 0 1-13 0V8Zm13 1h2a3 3 0 0 1 0 6h-2M2 21h16M6 2v3m5-3v3',
  martini: 'M3 4h18L12 14 3 4Zm9 10v7m-5 0h10M15 2l-4 7',
  'disco-ball': 'M12 1v3M4 12a8 8 0 1 0 16 0 8 8 0 1 0-16 0Zm0 0h16M6 7h12M6 17h12M12 4c-5 4-5 12 0 16 5-4 5-12 0-16Z',
  croissant: 'M3 18C0 12 5 5 12 4c7 1 12 8 9 14l-4-3c-1-5-9-5-10 0l-4 3ZM5 9l4 3M9 5l1 6m5-6-1 6m5-2-4 3',
  'ice-cream': 'M6 12 12 22l6-10H6Zm0 0c-4 0-4-6 0-6 0-6 12-6 12 0 4 0 4 6 0 6M9 16h6',
  bed: 'M3 4v17M21 9v12M3 16h18M3 8h6v5H3m6-5h8c3 0 4 2 4 5H9V8Z',
  'shopping-bag': 'M4 7h16l1 14H3L4 7Zm4 1V6a4 4 0 0 1 8 0v2',
  frame: 'M3 3h18v18H3V3Zm4 4h10v10H7V7Zm-4-4 4 4m10 0 4-4M3 21l4-4m10 0 4 4',
  camera: 'M3 7h4l2-3h6l2 3h4v13H3V7Zm5 6a4 4 0 1 0 8 0 4 4 0 1 0-8 0Z',
  tree: 'M12 2 6 10h3l-5 7h16l-5-7h3L12 2Zm0 15v5m-4 0h8',
  wave: 'M2 14c4 2 8 0 8-4 0-5 7-6 9-2-4-1-5 3-2 5 1 1 3 1 5 0M2 20c3 2 5-2 8 0s5-2 8 0c2 1 3 1 4 0',
  dumbbell: 'M1 9h3v6H1V9Zm3-3h4v12H4V6Zm4 5h8v2H8m8-7h4v12h-4V6Zm4 3h3v6h-3',
  lotus: 'M12 20C4 20 2 15 2 10c5 0 8 3 10 10Zm0 0c8 0 10-5 10-10-5 0-8 3-10 10Zm0-17c-6 6-5 10 0 17 5-7 6-11 0-17Z',
  ticket: 'M3 4h18v5a3 3 0 0 0 0 6v5H3v-5a3 3 0 0 0 0-6V4Zm12 2v2m0 3v2m0 3v2',
  'map-pin': 'M12 22S4 14 4 9a8 8 0 1 1 16 0c0 5-8 13-8 13Zm-3-13a3 3 0 1 0 6 0 3 3 0 1 0-6 0Z',
};
export function VenueKindIcon({ kind, size = 24, glyphOnly = false }: { kind: string; size?: number; glyphOnly?: boolean }) {
  const c = useTheme();
  const kinds = useVenueKinds(), spec = kinds[kind] || kinds.other;
  const color = spec?.color || '#536059', icon = spec?.icon || 'map-pin';
  return <View accessibilityLabel={spec?.label || 'Other'} style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center', backgroundColor: glyphOnly ? 'transparent' : color }}>
    {drawings[icon] ? <Svg width={size * .76} height={size * .76} viewBox="0 0 24 24"><Path d={drawings[icon]} stroke="#FFFFFF" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" fill="none" /></Svg>
      : <MaterialCommunityIcons name={(icon in MaterialCommunityIcons.glyphMap ? icon : 'bookmark-outline') as any} size={size * .76} color="#FFFFFF" />}
  </View>;
}
export function VenueMarker({ kind, selected = false, count, fill }: { kind: string; selected?: boolean; count?: number; fill?: string }) {
  const c = useTheme();
  const reduced = useReducedMotion(), scale = useSharedValue(1), opacity = useSharedValue(0);
  useEffect(() => { scale.value = reduced ? 1 : selected ? withSpring(motion.scale.marker, motion.spring) : withTiming(1, timing('quick')); if (reduced) opacity.value = 0; opacity.value = withTiming(1, timing('quick')); }, [selected, reduced]);
  const animated = useAnimatedStyle(() => ({ opacity: opacity.value, transform: [{ scale: scale.value }] }));
  const kinds = useVenueKinds(), size = 32, color = fill || (kinds[kind] || kinds.other)?.color || '#536059';
  return <Animated.View style={[animated, { transformOrigin: 'bottom', width: size + 8, height: size + 13, alignItems: 'center', shadowColor: '#12211B', shadowOpacity: selected ? .35 : 0, shadowRadius: 4, shadowOffset: { width: 0, height: 3 } }]}>
    <Svg width={size + 4} height={size + 9} viewBox="0 0 36 41" style={{ position: 'absolute' }}><Path d="M13 30 18 39 23 30" fill={color} stroke="#FFFFFF" strokeWidth={1.5} /><Circle cx={18} cy={17} r={16} fill={color} stroke="#FFFFFF" strokeWidth={1.5} /></Svg>
    <View style={{ marginTop: 2, width: size - 2, height: size - 2, alignItems: 'center', justifyContent: 'center' }}>{count ? <Text style={{ color: '#FFFFFF', fontSize: 14, fontWeight: '700', fontVariant: ['tabular-nums'] }}>{count}</Text> : <VenueKindIcon kind={kind} size={size * .75} glyphOnly />}</View>
  </Animated.View>;
}
