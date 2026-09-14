import type { Entry } from './libraryModel';
export type ImageSource = 'google' | 'site' | 'commons' | 'cover';
export type VenueImage = { key: string; source: ImageSource; uri: string; thumbnail_bytes: number; attribution: { label: string; url?: string; source_url?: string; license_url?: string; authors?: { displayName: string; uri?: string; photoUri?: string }[] }; license: string };
export type ImageryResult = { place_id: string | null; save_id: string; selected: Pick<VenueImage, 'key' | 'source'> | null; gallery: VenueImage[]; choices: { key: string; source: ImageSource; score: number }[]; selection: { selected: string; runner_up: string | null; reason: string; cover_rejection?: string | null } };
export function siblingCount(entry: Entry, entries: Entry[]) { return Math.max(entry.save_entry_count || 0, entries.filter(e => e.save_id === entry.save_id).length); }
export function allowedImages(result: ImageryResult | undefined, entry: Entry, entries: Entry[], context: string) {
  if (result && (result.place_id !== (entry.place_id || null) || result.save_id !== entry.save_id)) return [];
  return result?.gallery.filter(image => (image.source !== 'cover' || siblingCount(entry, entries) === 1) && (context !== 'map' || image.source !== 'google')) || [];
}
export function chosenImage(result: ImageryResult | undefined, entry: Entry, entries: Entry[], context: string) {
  const images = allowedImages(result, entry, entries, context);
  return images.find(image => image.key === entry.image_choice) || images.find(image => image.key === result?.selected?.key) || images[0] || null;
}
export function nextImageChoice(result: ImageryResult, current: string) {
  const keys = [...new Set(result.choices.map(choice => choice.key))];
  return keys[(keys.indexOf(current) + 1) % keys.length] || 'auto';
}

// Compare only active display-session images; Google content is never persisted.
export function mergeImageryRecords(previous: Record<string, ImageryResult>, incoming: Record<string, ImageryResult>, entries: Entry[]) {
  const owners = new Map(entries.map(entry => [entry.id, entry]));
  const claims = new Map<string, Entry>();
  const next = { ...previous };
  for (const [key, result] of Object.entries(previous)) {
    const entry = owners.get(key.slice(key.indexOf(':') + 1));
    if (entry) for (const image of allowedImages(result, entry, entries, key.split(':')[0])) claims.set(image.uri, entry);
  }
  for (const [key, result] of Object.entries(incoming)) {
    const entry = owners.get(key.slice(key.indexOf(':') + 1));
    if (!entry) continue;
    const rejected = new Set<string>();
    const gallery = result.gallery.filter(image => {
      const owner = claims.get(image.uri);
      const sameVenue = owner && ((owner.place_id && owner.place_id === entry.place_id) || (owner.google_place_id && owner.google_place_id === entry.google_place_id));
      if (owner && owner.id !== entry.id && !sameVenue) { rejected.add(image.key); return false; }
      claims.set(image.uri,entry); return true;
    });
    delete next[key];
    next[key] = { ...result, gallery, selected: gallery.find(image => image.key === result.selected?.key) || gallery[0] || null, choices: result.choices.filter(choice => !rejected.has(choice.key)) };
  }
  return Object.fromEntries(Object.entries(next).slice(-64));
}
