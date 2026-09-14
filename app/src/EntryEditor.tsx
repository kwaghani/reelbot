import { RetryVenueImage } from './VenueImagery';
import { attributeFields, cleanAttributes } from './attributeModel';
import { useTheme } from './theme';
import { useState } from 'react';
import { View } from 'react-native';
import { Text, TextInput } from './controls';
import { Button, Chip, useUI, TypeFilters } from './ui';
import { VenueKindIcon, useVenueKinds } from './VenueKindIcon';
import { humanize, fieldLabel, type Entry, type Registry } from './libraryModel';
export function EntryEditor({ entry, registry, places, onSave, onResolve }: { entry: Entry; registry: Registry; places: Entry[]; onSave: (body: Record<string, any>) => Promise<void>; onResolve: (name: string, city: string, body: Record<string, any>) => Promise<void> }) {
  const c = useTheme();
  const [pending, setPending] = useState<'save' | 'resolve' | null>(null);
  async function submit(kind: 'save' | 'resolve', action: () => Promise<void>) {
    if (pending) return;
    setPending(kind);
    try { await action(); } finally { setPending(null); }
  }
  const s = useUI();
  const [type, setType] = useState(entry.content_type), [title, setTitle] = useState(entry.title), [summary, setSummary] = useState(entry.summary);
  const [attributes, setAttributes] = useState<Record<string, any>>({ ...entry.attributes }), [place, setPlace] = useState(entry.place_id || null);
  const [venue, setVenue] = useState(entry.candidate?.venue_name || entry.place_name || entry.title), [city, setCity] = useState(entry.city || entry.candidate?.city_hint || '');
  const spec = registry[type], kinds = useVenueKinds();
  const fields = attributeFields({ content_type: type, attributes, venue_kind: attributes.venue_kind }, registry, kinds);
  const submittedAttributes = cleanAttributes(attributes, fields);
  return <View style={{ gap: 18 }}>{entry.content_type === 'place' ? <RetryVenueImage entry={entry} /> : null}<Text style={s.fieldLabel}>Content type</Text><TypeFilters registry={registry} value={type} onChange={key => { if (key && key !== type) { setType(key); setAttributes({}); } }} />
    <TextInput accessibilityLabel="Entry title" value={title} onChangeText={setTitle} maxLength={200} style={s.input} placeholder="Title" />
    <TextInput accessibilityLabel="Entry summary" value={summary} onChangeText={setSummary} maxLength={140} style={s.input} placeholder="One sentence to remember" multiline />
    {Object.entries(fields).map(([key, field]) => <View key={key} style={{ gap: 9 }}><Text style={s.strong}>{fieldLabel(key)}{field.required ? ' (required)' : ''}</Text>
      {field.type === 'boolean' ? <View style={s.wrap}>{[true, false].map(value => <Chip key={String(value)} label={value ? 'Yes' : 'No'} active={attributes[key] === value} onPress={() => setAttributes(a => ({ ...a, [key]: a[key] === value ? null : value }))} />)}</View> : field.type === 'enum' ? <View style={s.wrap}>{field.values?.map(value => {
        const current = attributes[key], selected = field.multi ? Array.isArray(current) && current.includes(value) : current === value;
        return <Chip key={value} label={key === 'venue_kind' ? kinds[value]?.label || humanize(value) : humanize(value)} iconElement={key === 'venue_kind' ? <VenueKindIcon kind={value} size={22} /> : undefined} active={selected} onPress={() => setAttributes(a => ({ ...a, [key]: field.multi ? selected ? current.filter((x: string) => x !== value) : [...(Array.isArray(current) ? current : []), value] : selected && key !== 'venue_kind' ? null : value }))} />;
      })}</View> : <TextInput accessibilityLabel={fieldLabel(key)} style={s.input} keyboardType={field.type === 'integer' ? 'number-pad' : field.type === 'number' ? 'decimal-pad' : 'default'}
        value={Array.isArray(attributes[key]) ? attributes[key].join(', ') : String(attributes[key] ?? '')}
        placeholder={field.multi ? 'Separate values with commas' : 'Leave blank if unknown'} maxLength={1000}
        onChangeText={text => setAttributes(a => ({ ...a, [key]: !text.trim() ? null : field.multi ? text.split(',').map(v => v.trim()).filter(Boolean) : ['integer', 'number'].includes(field.type) ? Number(text) : text }))} />}
    </View>)}
    <Text style={s.fieldLabel}>Linked place</Text><View style={s.wrap}><Chip label="No place" active={!place} onPress={() => setPlace(null)} />{places.filter((p, i, all) => p.place_id && all.findIndex(x => x.place_id === p.place_id) === i).map(p => <Chip key={p.place_id} label={p.place_name || p.title} active={place === p.place_id} onPress={() => setPlace(p.place_id!)} />)}</View>
    {spec?.geo !== 'never' ? <View style={{ gap: 10 }}><TextInput accessibilityLabel="Venue name" value={venue} onChangeText={setVenue} style={s.input} placeholder="Venue name" /><TextInput accessibilityLabel="Venue city" value={city} onChangeText={setCity} style={s.input} placeholder="City" /><Button secondary title="Find this place" loading={pending === 'resolve'} disabled={!!pending || !venue.trim() || !city.trim()} onPress={() => void submit('resolve', () => onResolve(venue, city, { content_type: type, title: title.trim(), summary, attributes: submittedAttributes, place_id: place }))} /></View> : null}
    <Button title="Save" loading={pending === 'save'} disabled={!!pending || !title.trim()} onPress={() => void submit('save', () => onSave({ content_type: type, title: title.trim(), summary, attributes: submittedAttributes, place_id: place }))} />
  </View>;
}
