import { useEffect, type ReactNode } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, View } from 'react-native';
import { Pressable, Text } from './controls';
import { SafeAreaView } from 'react-native-safe-area-context';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import { useTheme, fonts, useReducedMotion } from './theme';
import Animated, { useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated';
import { measureMotion } from './motion/Profiler';
import { motion, timing, reflow } from './theme/motion';
import { feedback } from './motion/haptics';
export { MotionSheet as Sheet } from './motion/MotionSheet';
import { VenueKindIcon } from './VenueKindIcon';
import type { Registry } from './libraryModel';
export function Icon({ name, size = 22, color }: { name: string; size?: number; color?: string }) {
  const c = useTheme(), s = useUI();
  if (name === 'venue') return <VenueKindIcon kind="other" size={size} />;
  const aliases: Record<string, string> = { barbell: 'weight-lifter', shirt: 'tshirt-crew', 'shopping-bag': 'shopping-outline', bookmark: 'bookmark-outline' };
  const glyph = aliases[name] || name;
  return <MaterialCommunityIcons name={(glyph in MaterialCommunityIcons.glyphMap ? glyph : 'bookmark-outline') as any} size={size} color={color || c.ink} />;
}
export function Button({ title, onPress, secondary = false, danger = false, disabled = false, loading = false }: { title: string; onPress: () => void; secondary?: boolean; danger?: boolean; disabled?: boolean; loading?: boolean }) {
  const c = useTheme(), s = useUI(), progress = useSharedValue(loading ? 1 : 0);
  useEffect(() => { progress.value = withTiming(loading ? 1 : 0, timing('quick')); }, [loading]);
  const label = useAnimatedStyle(() => ({ opacity: 1 - progress.value })), spinner = useAnimatedStyle(() => ({ opacity: progress.value }));
  return <Pressable accessibilityRole="button" accessibilityLabel={title} accessibilityState={{ busy: loading, disabled: disabled || loading }} destructive={danger} disabled={disabled || loading} onPress={onPress} style={[s.button, secondary && { backgroundColor: c.cardMuted }, danger && { backgroundColor: c.dangerSoft }, disabled && { backgroundColor: c.cardMuted }]}><Animated.View style={label}><Text style={{ color: disabled ? c.textSecondary : danger ? c.danger : secondary ? c.textPrimary : c.inkText, fontWeight: '600', fontSize: 16, lineHeight: 23, paddingVertical: 10 }}>{title}</Text></Animated.View><Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, { alignItems: 'center', justifyContent: 'center' }, spinner]}>{loading ? <ActivityIndicator color={secondary ? c.textPrimary : c.inkText} /> : null}</Animated.View></Pressable>;
}
export function Chip({ label, active, onPress, icon, iconElement }: { label: string; active?: boolean; onPress: () => void; icon?: string; iconElement?: ReactNode }) {
  const c = useTheme(), s = useUI(), reduced = useReducedMotion(), progress = useSharedValue(active ? 1 : 0);
  useEffect(() => { progress.value = withTiming(active ? 1 : 0, timing(reduced ? 'quick' : 'instant')); }, [active, reduced]);
  const inactive = useAnimatedStyle(() => ({ opacity: 1 - progress.value })), selected = useAnimatedStyle(() => ({ opacity: progress.value }));
  const content = (color: string) => <>{iconElement || (icon ? <Icon name={icon} size={15} color={color} /> : null)}<Text style={{ color, fontSize: 13, fontWeight: '600' }}>{label}</Text></>;
  return <Animated.View layout={reflow(reduced)}><Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityState={{ selected: !!active }} pressScale={motion.scale.chip} onPress={() => { feedback('selection'); measureMotion('filter-change'); onPress(); }} style={s.chip}>
    <Animated.View style={[{ flexDirection: 'row', alignItems: 'center', gap: 6 }, inactive]}>{content(c.textSecondary)}</Animated.View>
    <Animated.View pointerEvents="none" accessibilityElementsHidden style={[StyleSheet.absoluteFill, { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 12, backgroundColor: c.ink, borderRadius: 6 }, selected]}>{content(c.card)}</Animated.View>
  </Pressable></Animated.View>;
}
export function TypeFilters({ registry, value, onChange }: { registry: Registry; value: string | null; onChange: (key: string | null) => void }) {
  const c = useTheme(), s = useUI();
  return <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.chips}><Chip label="All" active={!value} onPress={() => onChange(null)} />{Object.entries(registry).map(([key, type]) => <Chip key={key} label={type.plural_label || type.label} icon={type.icon} active={value === key} onPress={() => onChange(value === key ? null : key)} />)}</ScrollView>;
}
export function useUI() { const c = useTheme(); return StyleSheet.create({
  safe: { flex: 1, backgroundColor: c.background }, body: { paddingHorizontal: 22, paddingBottom: 32 }, row: { flexDirection: 'row', alignItems: 'center', gap: 10 }, between: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  heading: { fontFamily: fonts.display, fontSize: 23, color: c.textPrimary }, title: { fontFamily: fonts.display, fontSize: 34, letterSpacing: -.6, color: c.textPrimary }, small: { fontSize: 12, lineHeight: 17, color: c.textSecondary }, text: { fontSize: 16, lineHeight: 23, color: c.textSecondary }, strong: { fontSize: 15, fontWeight: '600', color: c.textPrimary }, fieldLabel: { fontSize: 13, letterSpacing: 0, fontWeight: '600', color: c.textSecondary }, link: { color: c.accent, fontSize: 13, fontWeight: '600', paddingVertical: 10 },
  card: { backgroundColor: c.card, borderWidth: 1, borderColor: c.border, borderRadius: 3, padding: 16, gap: 12 }, input: { borderWidth: 1, borderColor: c.border, backgroundColor: c.card, borderRadius: 3, padding: 14, minHeight: 48, fontSize: 15, color: c.textPrimary }, button: { minHeight: 46, backgroundColor: c.accent, borderRadius: 3, paddingHorizontal: 16, justifyContent: 'center', alignItems: 'center' },
  chips: { flexDirection: 'row', gap: 8, paddingVertical: 14 }, chip: { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: c.card, paddingHorizontal: 12, paddingVertical: 9, borderRadius: 6, borderWidth: 1, borderColor: c.border }, wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, tag: { fontSize: 10, color: c.textSecondary, backgroundColor: c.cardMuted, borderRadius: 4, paddingHorizontal: 7, paddingVertical: 4 }, empty: { alignItems: 'center', paddingVertical: 42, gap: 14 },
}); }
