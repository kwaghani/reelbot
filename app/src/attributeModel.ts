import type { AttributeSpec, Entry, Registry, VenueKinds } from './libraryModel';
export function attributeFields(entry: Pick<Entry, 'content_type' | 'attributes' | 'venue_kind'>, registry: Registry, kinds: VenueKinds = {}): Record<string, AttributeSpec> {
  const spec = registry[entry.content_type];
  if (!spec) return {};
  const kind = entry.venue_kind || entry.attributes.venue_kind || 'other';
  return { ...spec.attributes, ...(entry.content_type === 'place' ? kinds[kind]?.attributes || spec.kind_attributes?.[kind] || {} : {}) };
}
export const hasAttributeValue = (value: unknown) => value != null && value !== '' && (!Array.isArray(value) || value.length > 0);
export function visibleAttributes(entry: Entry, registry: Registry, kinds: VenueKinds = {}) {
  return Object.entries(attributeFields(entry, registry, kinds)).filter(([key]) => hasAttributeValue(entry.attributes[key]));
}
export function cleanAttributes(attributes: Record<string, any>, fields: Record<string, AttributeSpec>) {
  return Object.fromEntries(Object.entries(attributes).filter(([key]) => key in fields));
}
export function attributeText(value: any) {
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return (Array.isArray(value) ? value : [value]).map(v => String(v).replace(/_/g, ' ')).join(', ');
}
