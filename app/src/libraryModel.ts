export type SaveStatus = 'queued' | 'processing' | 'resolved' | 'needs_review' | 'no_places_found' | 'failed';
export type Save = { id: string; source_url: string; status: SaveStatus; created_at: string; error_reason?: string | null; cost?: Record<string, number>; local?: boolean };
export type Folder = { id: string; name: string; kind: 'custom' | 'auto_city' | 'auto_category' | 'needs_review'; hidden?: boolean; sort_order: number };
export type Place = { id: string; save_id: string; name: string; city: string; note: string; needs_review: boolean; confidence: number; source_url: string; formatted_address?: string; lat?: number; lng?: number; folders: Pick<Folder, 'id' | 'name' | 'kind'>[]; candidate?: { review_reason?: string }; };
export type Operation = { id: string; method: string; path: string; body?: any; error?: string; kind: 'folder_create' | 'folder_edit' | 'folder_delete' | 'assign' | 'note' | 'item_delete'; target?: string };
export type Library = { saves: Save[]; items: Place[]; folders: Folder[]; outbox: Operation[]; apple_linked: boolean; sync_error: string | null; last_synced?: string };
export const emptyLibrary = (): Library => ({ saves: [], items: [], folders: [], outbox: [], apple_linked: false, sync_error: null });
export function applyOperation(state: Library, operation: Operation): void {
  const { body, target, kind } = operation;
  if (kind === 'folder_create' && !state.folders.some(f => f.id === body.id)) state.folders.push({ ...body, kind: 'custom', sort_order: state.folders.length });
  if (kind === 'folder_edit') {
    state.folders = state.folders.map(f => f.id === target ? { ...f, ...body } : f);
    if (body.name) state.items.forEach(p => { p.folders = p.folders.map(f => f.id === target ? { ...f, name: body.name } : f); });
  }
  if (kind === 'folder_delete') { state.folders = state.folders.filter(f => f.id !== target); state.items.forEach(p => { p.folders = p.folders.filter(f => f.id !== target); }); }
  if (kind === 'note') state.items = state.items.map(p => p.id === target ? { ...p, note: body.note } : p);
  if (kind === 'item_delete') state.items = state.items.filter(p => p.id !== target);
  if (kind === 'assign') {
    const destination = state.folders.find(f => f.id === body.destination_id);
    if (destination) state.items.filter(p => body.item_ids.includes(p.id)).forEach(p => {
      if (body.move && body.source_id !== destination.id) p.folders = p.folders.filter(f => f.id !== body.source_id || f.kind !== 'custom');
      if (!p.folders.some(f => f.id === destination.id)) p.folders.push(destination);
    });
  }
}
export function searchLocal(items: Place[], query: string) {
  const tokens = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  return items.filter(p => tokens.every(t => [p.name, p.city, p.note, ...p.folders.map(f => f.name)].join(' ').toLocaleLowerCase().includes(t)));
}
export const statusLabels: Record<SaveStatus, string> = { queued: 'Queued', processing: 'Finding places', resolved: 'Saved', needs_review: 'Needs review', no_places_found: 'No places found', failed: 'Could not process' };
