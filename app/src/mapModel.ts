import type { Entry } from './libraryModel';
export type Coordinate = { latitude: number; longitude: number };
export const wrapLongitude = (n: number) => ((n + 540) % 360) - 180;
export function anchored(entries: Entry[]): Entry[] {
  return entries.filter(e => !!e.place_id && typeof e.lat === 'number' && typeof e.lng === 'number' && Number.isFinite(e.lat) && Number.isFinite(e.lng) && Math.abs(e.lat) <= 90 && Math.abs(e.lng) <= 180);
}
export function distanceKm(a: Coordinate, b: Coordinate): number {
  const rad = Math.PI / 180, dLat = (b.latitude - a.latitude) * rad, dLon = wrapLongitude(b.longitude - a.longitude) * rad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.latitude * rad) * Math.cos(b.latitude * rad) * Math.sin(dLon / 2) ** 2;
  return 6371.0088 * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(Math.max(0, 1 - h)));
}
export function nearby(entries: Entry[], location: Coordinate | null, radius: number | null = null) {
  return anchored(entries).map(entry => ({ entry, distance: location ? distanceKm(location, { latitude: entry.lat!, longitude: entry.lng! }) : null }))
    .filter(row => radius == null || row.distance == null || row.distance <= radius)
    .sort((a, b) => location ? a.distance! - b.distance! : a.entry.title.localeCompare(b.entry.title));
}
export function bounds(entries: Entry[]) {
  const points = anchored(entries);
  if (!points.length) return null;
  const ref = points[0].lng!, longs = points.map(e => ref + wrapLongitude(e.lng! - ref)), lats = points.map(e => e.lat!);
  return { latitude: (Math.min(...lats) + Math.max(...lats)) / 2, longitude: wrapLongitude((Math.min(...longs) + Math.max(...longs)) / 2), latitudeDelta: Math.max(.02, (Math.max(...lats) - Math.min(...lats)) * 1.3), longitudeDelta: Math.max(.02, (Math.max(...longs) - Math.min(...longs)) * 1.3) };
}
export function clusters(entries: Entry[], latitudeDelta: number, centerLongitude = 0) {
  const cells = new Map<string, Entry[]>();
  const cell = latitudeDelta > .08 ? latitudeDelta / 7 : 0;
  for (const e of anchored(entries)) {
    const key = cell ? `${Math.floor(e.lat! / cell)}:${Math.floor(wrapLongitude(e.lng! - centerLongitude) / cell)}` : e.id;
    cells.set(key, [...(cells.get(key) || []), e]);
  }
  return [...cells.entries()].map(([id, members]) => ({ id, members, coordinate: {
    latitude: members.reduce((sum, e) => sum + e.lat!, 0) / members.length,
    longitude: wrapLongitude(centerLongitude + members.reduce((sum, e) => sum + wrapLongitude(e.lng! - centerLongitude), 0) / members.length),
  } }));
}
export const distanceLabel = (km: number) => km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(km < 10 ? 1 : 0)} km`;

/** Screen-space collision clusters also handle co-located entries at maximum zoom. */
export function collisionClusters(entries: Entry[], region: { latitude: number; longitude: number; latitudeDelta: number; longitudeDelta: number }, width: number, height: number) {
  const points = anchored(entries).map(e => ({ e, x: wrapLongitude(e.lng! - region.longitude) / region.longitudeDelta * width, y: (e.lat! - region.latitude) / region.latitudeDelta * height }));
  const groups = points.map(p => [p]);
  // Merge while any points could overlap. Centroids are then checked too, so a cluster
  // count cannot collide with another cluster formed in the same pass.
  const center = (g: typeof points) => ({ x: g.reduce((n, p) => n + p.x, 0) / g.length, y: g.reduce((n, p) => n + p.y, 0) / g.length });
  let changed = true;
  while (changed) {
    changed = false;
    outer: for (let i = 0; i < groups.length; i++) for (let j = i + 1; j < groups.length; j++) {
      const a = center(groups[i]), b = center(groups[j]);
      if (Math.hypot(a.x - b.x, a.y - b.y) < 52 || groups[i].some(p => groups[j].some(q => Math.hypot(p.x - q.x, p.y - q.y) < 44))) {
        groups[i].push(...groups[j]); groups.splice(j, 1); changed = true; break outer;
      }
    }
  }
  return groups.map(g => { const p = center(g); return { id: g.map(p => p.e.id).sort().join(','), members: g.map(p => p.e), coordinate: { latitude: region.latitude + p.y / height * region.latitudeDelta, longitude: wrapLongitude(region.longitude + p.x / width * region.longitudeDelta) } }; });
}
