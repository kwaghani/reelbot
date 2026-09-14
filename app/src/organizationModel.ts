import { searchLocal, humanize, type Entry, type Folder, type Registry, type VenueKinds } from './libraryModel';

import { countBy, entryKind } from './venueModel';

export const entryCount = (count: number) => `${count} ${count === 1 ? 'entry' : 'entries'}`;
export const organizationCity = (entry: Entry) => entry.organization_city ?? entry.city ?? '';
export function primaryFacet(entry: Entry, registry: Registry): string {
  const key = registry[entry.content_type]?.primary_facet;
  const value = key === 'city' ? organizationCity(entry) : entry.attributes[key || 'topic'];
  return humanize(Array.isArray(value) ? value[0] : value || '');
}

/** One entry snapshot supplies the grid, folder counts and contextual chip counts. */
export function libraryProjection(entries: Entry[], folders: Folder[], registry: Registry, options: {
  kinds?: VenueKinds; folderId?: string | null; query?: string; type?: string | null; review?: boolean; facet?: string | null; venueKind?: string | null; semanticIds?: string[];
} = {}) {
  const all = [...new Map(entries.map(entry => [entry.id, entry])).values()]
    .sort((a, b) => b.created_at.localeCompare(a.created_at) || a.id.localeCompare(b.id));
  const folderCounts: Record<string, number> = {};
  for (const entry of all) for (const id of new Set(entry.folders.map(f => f.id))) folderCounts[id] = (folderCounts[id] || 0) + 1;
  const folder = folders.find(f => f.id === options.folderId);
  const scoped = all.filter(e => !folder || (folder.facet_key === 'venue_kind' ? e.content_type === 'place' && entryKind(e) === folder.facet_value : e.folders.some(f => f.id === folder.id)));
  for (const f of folders.filter(f => f.facet_key === 'venue_kind')) folderCounts[f.id] = all.filter(e => e.content_type === 'place' && entryKind(e) === f.facet_value).length;
  const matches = new Set(searchLocal(scoped, options.query || '', options.kinds).map(e => e.id));
  const scope = scoped.filter(e => matches.has(e.id) || (!!options.query?.trim() && options.semanticIds?.includes(e.id)));
  const reviewCount = scope.filter(e => e.needs_review).length;
  const review = !!options.review && reviewCount > 0;
  const reviewed = scope.filter(e => !review || e.needs_review);
  const typeCounts: Record<string, number> = {};
  for (const entry of reviewed) typeCounts[entry.content_type] = (typeCounts[entry.content_type] || 0) + 1;
  const type = !folder && options.type && typeCounts[options.type] ? options.type : null;
  const kindCounts = countBy(reviewed.filter(e => e.content_type === 'place' && (!type || type === 'place')), entryKind);
  const venueKind = options.venueKind && kindCounts[options.venueKind] ? options.venueKind : null;
  const typed = reviewed.filter(e => !venueKind || e.content_type === 'place' && entryKind(e) === venueKind).filter(e => !type || e.content_type === type);
  const facetValue = (entry: Entry) => folder?.kind === 'auto_facet' && folder.content_type === 'place' && folder.facet_key !== 'venue_kind'
    ? humanize(entry.attributes.neighborhood || '') : primaryFacet(entry, registry);
  const facetCounts: Record<string, number> = {};
  if (folder) for (const entry of typed) {
    const value = facetValue(entry);
    if (value) facetCounts[value] = (facetCounts[value] || 0) + 1;
  }
  const facet = options.facet && facetCounts[options.facet] ? options.facet : null;
  const filteredItems = typed.filter(e => !facet || facetValue(e) === facet);
  const filteredFolderCounts: Record<string, number> = {};
  for (const f of folders) filteredFolderCounts[f.id] = filteredItems.filter(e => f.facet_key === 'venue_kind' ? e.content_type === 'place' && entryKind(e) === f.facet_value : e.folders.some(link => link.id === f.id)).length;
  return { filteredFolderCounts, all, folder, kindCounts, venueKind, folderCounts, scope, typeCounts, reviewCount, facetCounts, type, review, facet,
    items: filteredItems,
    tier: all.length === 0 ? 'empty' : all.length < 20 ? 'small' : 'large',
    showTypeChips: all.length >= 5 && !folder,
  };
}
