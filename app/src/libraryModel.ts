export type AttributeSpec = { type: 'enum' | 'string' | 'integer'; multi?: boolean; required?: boolean; values?: string[] };
export type ContentType = { label: string; plural_label?: string; icon: string; geo: 'required' | 'optional' | 'never'; primary_facet: string | null; attributes: Record<string, AttributeSpec> };
export type Registry = Record<string, ContentType>;
export type SaveStatus = 'queued' | 'processing' | 'resolved' | 'needs_review' | 'no_content_found' | 'failed';
export type Save = { id: string; source_url: string; status: SaveStatus; created_at: string; error_reason?: string | null; cost?: Record<string, number>; local?: boolean };
export type Folder = { id: string; name: string; kind: 'custom' | 'auto_type' | 'auto_facet'; content_type?: string; facet_key?: string; facet_value?: string; parent_folder_id?: string | null; icon?: string; hidden?: boolean; sort_order: number };
export type Entry = { id: string; save_id: string; content_type: string; title: string; summary: string; attributes: Record<string, any>; name: string; city: string; note: string; needs_review: boolean; review_reason?: string | null; verified_at?: string | null; confidence: number; created_at: string; source_url: string; place_id?: string | null; place_name?: string; thumbnail?: string; formatted_address?: string; lat?: number | null; lng?: number | null; folders: Pick<Folder, 'id' | 'name' | 'kind' | 'parent_folder_id'>[]; candidate?: { venue_name?: string; city_hint?: string; attributes?: Record<string, any> } };
export type Operation = { id: string; method: string; path: string; body?: any; error?: string; kind: 'folder_create' | 'folder_edit' | 'folder_delete' | 'assign' | 'note' | 'entry_edit' | 'dismiss_review' | 'item_delete' | 'delete_all'; target?: string };
export type Library = { saves: Save[]; items: Entry[]; folders: Folder[]; registry: Registry; outbox: Operation[]; apple_linked: boolean; sync_error: string | null; last_synced?: string; preferences: { notifications: boolean; locationRationaleSeen: boolean } };
export const emptyLibrary = (): Library => ({ saves: [], items: [], folders: [], registry: {}, outbox: [], apple_linked: false, sync_error: null, preferences: { notifications: false, locationRationaleSeen: false } });
export function upgradeLibrary(raw: any): Library {
  const state = { ...emptyLibrary(), ...raw, preferences: { ...emptyLibrary().preferences, ...raw?.preferences } };
  state.items = state.items.map((p: any) => ({ content_type: 'place', title: p.name || 'Saved entry', summary: '', attributes: {}, ...p }));
  state.saves = state.saves.map((s: any) => ({ ...s, status: s.status === 'no_places_found' ? 'no_content_found' : s.status }));
  return state;
}
export function applyOperation(state: Library, operation: Operation): void {
  const { body, target, kind } = operation;
  if (kind === 'folder_create' && !state.folders.some(f => f.id === body.id)) state.folders.push({ ...body, kind: 'custom', sort_order: state.folders.length });
  if (kind === 'folder_edit') {
    state.folders = state.folders.map(f => f.id === target ? { ...f, ...body } : f);
    if (body.name) state.items.forEach(p => { p.folders = p.folders.map(f => f.id === target ? { ...f, name: body.name } : f); });
  }
  if (kind === 'folder_delete') { state.folders = state.folders.filter(f => f.id !== target); state.items.forEach(p => { p.folders = p.folders.filter(f => f.id !== target); }); }
  if (kind === 'note') state.items = state.items.map(p => p.id === target ? { ...p, note: body.note } : p);
  if (kind === 'entry_edit') state.items = state.items.map(p => {
    if (p.id !== target) return p;
    const next = { ...p, ...body, name: body.title || p.title }, type = state.registry[next.content_type];
    if (type) {
      const missing = Object.entries(type.attributes).filter(([key, spec]) => spec.required && (next.attributes[key] == null || next.attributes[key] === '' || Array.isArray(next.attributes[key]) && !next.attributes[key].length)).map(([key]) => 'missing_required:' + key);
      if (type.geo === 'required' && !next.place_id) missing.push('unresolved_place');
      next.needs_review = !!missing.length; next.review_reason = missing.join(';') || null; next.verified_at = missing.length ? null : new Date().toISOString();
    }
    return next;
  });
  if (kind === 'dismiss_review') state.items = state.items.map(p => p.id === target ? { ...p, needs_review: false, review_reason: null, verified_at: new Date().toISOString() } : p);
  if (kind === 'item_delete') state.items = state.items.filter(p => p.id !== target);
  if (kind === 'delete_all') { state.items = []; state.saves = []; state.folders = []; state.apple_linked = false; state.outbox = []; state.preferences = emptyLibrary().preferences; }
  if (kind === 'assign') {
    const destination = state.folders.find(f => f.id === body.destination_id);
    if (destination) state.items.filter(p => body.item_ids.includes(p.id)).forEach(p => {
      if (body.move && body.source_id !== destination.id) p.folders = p.folders.filter(f => f.id !== body.source_id || f.kind !== 'custom');
      if (!p.folders.some(f => f.id === destination.id)) p.folders.push(destination);
    });
  }
}
export function searchLocal(items: Entry[], query: string) {
  const tokens = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  return items.filter(p => tokens.every(t => [p.title, p.summary, JSON.stringify(p.attributes), p.place_name, p.name, p.city, p.note, ...p.folders.map(f => f.name)].join(' ').toLocaleLowerCase().includes(t)));
}
export const humanize = (value: unknown): string => String(value ?? '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
export function facetValues(entry: Entry, registry: Registry): string[] {
  const key = registry[entry.content_type]?.primary_facet;
  const value = key === 'city' ? entry.city : entry.attributes[key || 'topic'];
  return (Array.isArray(value) ? value : [value]).filter(Boolean).map(humanize);
}
export function reviewQuestion(entry: Entry): string {
  const reason = (entry.review_reason || '').split(';')[0];
  if (reason.startsWith('missing_required:')) return `Which ${reason.split(':')[1].replace(/_/g, ' ')}?`;
  if (reason === 'unresolved_place') return "Couldn't find this place";
  return 'Does this match the reel?';
}
export const statusLabels: Record<SaveStatus, string> = { queued: 'Queued on your device', processing: 'Sorting your reel', resolved: 'Saved', needs_review: 'Saved', no_content_found: 'Nothing to extract', failed: 'Could not process' };
