import type { ReactNode } from 'react';
import { Modal, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import { colors as c } from './theme';
import type { Registry } from './libraryModel';
export function Icon({ name, size = 22, color = c.ink }: { name: string; size?: number; color?: string }) {
  const aliases: Record<string, string> = { 'map-pin': 'map-marker', barbell: 'weight-lifter', shirt: 'tshirt-crew', 'shopping-bag': 'shopping-outline', bookmark: 'bookmark-outline' };
  const glyph = aliases[name] || name;
  return <MaterialCommunityIcons name={(glyph in MaterialCommunityIcons.glyphMap ? glyph : 'bookmark-outline') as any} size={size} color={color} />;
}
export function Button({ title, onPress, secondary = false, danger = false, disabled = false }: { title: string; onPress: () => void; secondary?: boolean; danger?: boolean; disabled?: boolean }) {
  return <Pressable accessibilityRole="button" accessibilityLabel={title} disabled={disabled} onPress={onPress} style={[s.button, secondary && { backgroundColor: c.cardMuted }, danger && { backgroundColor: c.dangerSoft }, disabled && { opacity: .5 }]}><Text style={{ color: danger ? c.danger : secondary ? c.textPrimary : c.card, fontWeight: '600' }}>{title}</Text></Pressable>;
}
export function Chip({ label, active, onPress, icon }: { label: string; active?: boolean; onPress: () => void; icon?: string }) {
  return <Pressable accessibilityRole="button" accessibilityState={{ selected: !!active }} onPress={onPress} style={[s.chip, active && { backgroundColor: c.ink, borderColor: c.ink }]}>{icon ? <Icon name={icon} size={15} color={active ? c.card : c.ink} /> : null}<Text style={{ color: active ? c.card : c.textSecondary, fontSize: 12, fontWeight: '600' }}>{label}</Text></Pressable>;
}
export function TypeFilters({ registry, value, onChange }: { registry: Registry; value: string | null; onChange: (key: string | null) => void }) {
  return <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.chips}><Chip label="All" active={!value} onPress={() => onChange(null)} />{Object.entries(registry).map(([key, type]) => <Chip key={key} label={type.plural_label || type.label} icon={type.icon} active={value === key} onPress={() => onChange(value === key ? null : key)} />)}</ScrollView>;
}
export function Sheet({ visible, title, onClose, children }: { visible: boolean; title: string; onClose: () => void; children: ReactNode }) {
  return <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}><SafeAreaView style={s.safe}><View style={[s.row, { padding: 22 }]}><Text style={[s.heading, { flex: 1 }]}>{title}</Text><Pressable accessibilityRole="button" accessibilityLabel="Close" onPress={onClose} hitSlop={12}><Icon name="close" /></Pressable></View><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={[s.body, { gap: 18 }]}>{children}</ScrollView></SafeAreaView></Modal>;
}
export const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: c.background }, body: { paddingHorizontal: 22, paddingBottom: 32 }, row: { flexDirection: 'row', alignItems: 'center', gap: 10 }, between: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  heading: { fontFamily: 'Georgia', fontSize: 28, color: c.textPrimary }, title: { fontFamily: 'Georgia', fontSize: 35, letterSpacing: -.7, color: c.textPrimary }, small: { fontSize: 12, lineHeight: 18, color: c.textSecondary }, text: { fontSize: 15, lineHeight: 22, color: c.textSecondary }, strong: { fontSize: 15, fontWeight: '600', color: c.textPrimary }, eyebrow: { fontSize: 10, letterSpacing: 1.7, fontWeight: '700', color: c.textSecondary }, link: { color: c.accent, fontSize: 13, fontWeight: '600', paddingVertical: 10 },
  card: { backgroundColor: c.card, borderWidth: 1, borderColor: c.border, borderRadius: 14, padding: 16, gap: 12 }, input: { borderWidth: 1, borderColor: c.border, backgroundColor: c.card, borderRadius: 9, padding: 14, minHeight: 48, fontSize: 15, color: c.textPrimary }, button: { minHeight: 46, backgroundColor: c.accent, borderRadius: 9, paddingHorizontal: 16, justifyContent: 'center', alignItems: 'center' },
  chips: { flexDirection: 'row', gap: 8, paddingVertical: 14 }, chip: { flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: c.card, paddingHorizontal: 12, paddingVertical: 9, borderRadius: 22, borderWidth: 1, borderColor: c.border }, wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, tag: { fontSize: 10, color: c.textSecondary, backgroundColor: c.cardMuted, borderRadius: 4, paddingHorizontal: 7, paddingVertical: 4 }, empty: { alignItems: 'center', paddingVertical: 42, gap: 14 },
});
