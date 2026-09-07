import { useState } from 'react';
import { Text, TextInput, View } from 'react-native';
import { Button, Chip, s, TypeFilters } from './ui';
import { humanize, type Entry, type Registry } from './libraryModel';
export function EntryEditor({ entry, registry, places, onSave, onResolve }: { entry: Entry; registry: Registry; places: Entry[]; onSave: (body: Record<string, any>) => Promise<void>; onResolve: (name: string, city: string, body: Record<string, any>) => Promise<void> }) {
  const [type, setType] = useState(entry.content_type), [title, setTitle] = useState(entry.title), [summary, setSummary] = useState(entry.summary);
  const [attributes, setAttributes] = useState<Record<string, any>>({ ...entry.attributes }), [place, setPlace] = useState(entry.place_id || null);
  const [venue, setVenue] = useState(entry.candidate?.venue_name || entry.place_name || entry.title), [city, setCity] = useState(entry.city || entry.candidate?.city_hint || '');
  const spec = registry[type];
  return <View style={{ gap: 18 }}><Text style={s.eyebrow}>CONTENT TYPE</Text><TypeFilters registry={registry} value={type} onChange={key => { if (key && key !== type) { setType(key); setAttributes({}); } }} />
    <TextInput accessibilityLabel="Entry title" value={title} onChangeText={setTitle} maxLength={200} style={s.input} placeholder="Title" />
    <TextInput accessibilityLabel="Entry summary" value={summary} onChangeText={setSummary} maxLength={140} style={s.input} placeholder="One sentence to remember" multiline />
    {Object.entries(spec?.attributes || {}).map(([key, field]) => <View key={key} style={{ gap: 9 }}><Text style={s.strong}>{humanize(key)}{field.required ? ' · required' : ''}</Text>
      {field.type === 'enum' ? <View style={s.wrap}>{field.values?.map(value => {
        const current = attributes[key], selected = field.multi ? Array.isArray(current) && current.includes(value) : current === value;
        return <Chip key={value} label={humanize(value)} active={selected} onPress={() => setAttributes(a => ({ ...a, [key]: field.multi ? selected ? current.filter((x: string) => x !== value) : [...(Array.isArray(current) ? current : []), value] : selected ? null : value }))} />;
      })}</View> : <TextInput accessibilityLabel={humanize(key)} style={s.input} keyboardType={field.type === 'integer' ? 'number-pad' : 'default'}
        value={Array.isArray(attributes[key]) ? attributes[key].join(', ') : String(attributes[key] ?? '')}
        placeholder={field.multi ? 'Separate values with commas' : 'Leave blank if unknown'} maxLength={1000}
        onChangeText={text => setAttributes(a => ({ ...a, [key]: !text.trim() ? null : field.multi ? text.split(',').map(v => v.trim()).filter(Boolean) : field.type === 'integer' ? Number(text) : text }))} />}
    </View>)}
    <Text style={s.eyebrow}>LINKED PLACE</Text><View style={s.wrap}><Chip label="No place" active={!place} onPress={() => setPlace(null)} />{places.filter((p, i, all) => p.place_id && all.findIndex(x => x.place_id === p.place_id) === i).map(p => <Chip key={p.place_id} label={p.place_name || p.title} active={place === p.place_id} onPress={() => setPlace(p.place_id!)} />)}</View>
    {spec?.geo !== 'never' ? <View style={{ gap: 10 }}><TextInput accessibilityLabel="Venue name" value={venue} onChangeText={setVenue} style={s.input} placeholder="Venue name" /><TextInput accessibilityLabel="Venue city" value={city} onChangeText={setCity} style={s.input} placeholder="City" /><Button secondary title="Find this place" disabled={!venue.trim() || !city.trim()} onPress={() => void onResolve(venue, city, { content_type: type, title: title.trim(), summary, attributes, place_id: place })} /></View> : null}
    <Button title="Save changes" disabled={!title.trim()} onPress={() => void onSave({ content_type: type, title: title.trim(), summary, attributes, place_id: place })} />
  </View>;
}
