import type { Entry, Folder, Registry, VenueKinds } from './libraryModel';
export const entryKind = (entry: Entry) => entry.venue_kind || entry.attributes.venue_kind || 'other';
export const cityOf = (entry: Entry) => entry.organization_city || entry.city || 'Unsorted';
export const uniqueEntries = (entries: Entry[]) => [...new Map(entries.map(e => [e.id, e])).values()];
export function countBy(entries: Entry[], key: (e: Entry) => string) {
  const counts: Record<string, number> = {};
  for (const e of uniqueEntries(entries)) { const value = key(e); counts[value] = (counts[value] || 0) + 1; }
  return counts;
}
export function venueSnapshot(entries: Entry[], registry: Registry) {
  const all = uniqueEntries(entries), places = all.filter(e => e.content_type === 'place');
  const map = all.filter(e => registry[e.content_type]?.geo !== 'never' && e.place_id && typeof e.lat === 'number' && typeof e.lng === 'number' && Number.isFinite(e.lat) && Number.isFinite(e.lng) && Math.abs(e.lat) <= 90 && Math.abs(e.lng) <= 180);
  return { all, places, map, kinds: countBy(places, entryKind), cities: countBy(places, cityOf) };
}
export function typeFolders(entries: Entry[], kinds: VenueKinds, parent?: Folder): Folder[] {
  const counts = countBy(entries.filter(e => e.content_type === 'place'), entryKind);
  return Object.entries(kinds).filter(([key]) => counts[key]).map(([key, spec], index) => ({ id: 'venue:' + key, name: spec.plural_label || spec.label, kind: 'auto_facet', content_type: 'place', facet_key: 'venue_kind', facet_value: key, parent_folder_id: parent?.id, sort_order: index }));
}
export const distanceSystem = (region: string | null | undefined, override = 'auto') => override === 'auto' ? ['US', 'GB'].includes((region || '').toUpperCase()) ? 'imperial' : 'metric' : override;
export function formatDistance(km: number, system: string) {
  if (system === 'imperial') { const miles = km / 1.609344; return `${miles.toFixed(miles < 10 ? 1 : 0)} mi`; }
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(km < 10 ? 1 : 0)} km`;
}
export function majorityKind(entries: Entry[]) {
  const counts = countBy(entries, e => e.content_type === 'place' ? entryKind(e) : 'other');
  return Object.keys(counts).find(key => counts[key] > entries.length / 2) || null;
}
