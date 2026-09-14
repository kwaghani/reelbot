import { emptyLibrary, type Entry, type Folder } from './libraryModel';
import { appConfig, fixtureCount, fixtureGrayscale } from './config';
// Used only with native Debug or MOTION_PROFILE launch flags. Never written to SQLite or synced.
export function makeVisualFixture() {
  const state = emptyLibrary(); state.registry = appConfig.contentTypes; state.venue_kinds = JSON.parse(JSON.stringify(appConfig.venueKinds));
  if (fixtureGrayscale) for (const spec of Object.values(state.venue_kinds)) { const rgb = [1, 3, 5].map(i => parseInt(spec.color.slice(i, i + 2), 16)); const gray = Math.round(rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722).toString(16).padStart(2, '0'); spec.color = '#' + gray.repeat(3); }
  state.preferences.locationRationaleSeen = true;
  const root: Folder = { id: 'places', kind: 'auto_type', content_type: 'place', name: 'Places', icon: 'venue', sort_order: 0 };
  const cities = ['Los Angeles', 'San Francisco', 'New York'];
  state.folders = [root, ...cities.map((city, i): Folder => ({ id: 'city' + i, kind: 'auto_facet', content_type: 'place', facet_key: 'city', facet_value: city, parent_folder_id: root.id, name: city, sort_order: i + 1 })), { id: 'weekend', kind: 'custom', name: 'Weekend', sort_order: 4 }];
  const keys = Object.keys(state.venue_kinds).sort();
  const names: Record<string, string> = { bar: 'Bar Flores', cafe: 'Dayglow', restaurant: 'Bavel', bakery: 'Tartine', outdoors: 'Griffith Park', culture: 'The Broad' };
  state.items = Array.from({ length: fixtureCount }, (_, i): Entry => {
    const kind = keys[i % keys.length], cityIndex = fixtureCount <= 17 ? 0 : i % 3;
    // The 16-kind atlas spreads genuine rendered pins over one map viewport.
    const row = Math.floor(i / 4), col = i % 4;
    const lat = fixtureCount > 17 && i === 1 ? 37.79 : fixtureCount <= 17 ? 34.095 - row * .018 : [34.06, 37.77, 40.74][cityIndex] + (i % 8) * .001;
    const lng = fixtureCount > 17 && i === 1 ? -122.47 : fixtureCount <= 17 ? -118.33 + col * .025 : [-118.25, -122.43, -73.99][cityIndex] + (i % 6) * .001;
    const title = (names[kind] || state.venue_kinds[kind].label + ' saved place') + (i >= keys.length ? ' ' + (i + 1) : '');
    return { id: 'fixture' + i, save_id: 'save' + i, place_id: i === 1 ? '11111111-1111-4111-8111-111111111111' : 'place' + i, content_type: 'place', venue_kind: kind, venue_kind_source: 'provider', title, name: title, summary: 'Saved for a weekend visit.', note: 'Try the patio before sunset.', city: cities[cityIndex], organization_city: cities[cityIndex], formatted_address: cities[cityIndex], lat, lng, attributes: { venue_kind: kind, neighborhood: i % 2 ? 'Echo Park' : 'Downtown' }, candidate: i === 1 ? { venue_name: title, city_hint: cities[cityIndex], place_candidates: [{ score: .9, place: { id: 'place' + i, displayName: { text: title }, formattedAddress: cities[cityIndex], location: { latitude: lat, longitude: lng } } }] } : undefined, needs_review: i === 1 && fixtureCount > 17, review_reason: 'low_confidence', confidence: .9, created_at: new Date(Date.UTC(2026, 8, 8, 0, 0, fixtureCount - i)).toISOString(), source_url: 'https://instagram.com/reel/visual-fixture/', folders: [root, state.folders[cityIndex + 1], state.folders[4]] };
  });
  state.saves = state.items.map(e => ({ id: e.save_id, source_url: e.source_url, status: 'resolved', created_at: e.created_at }));
  if (fixtureCount < 0) state.saves = [{ id: 'source-fixture', source_url: 'https://instagram.com/reel/visual-fixture/', status: 'needs_source_info', created_at: '2026-09-08' }];
  return state;
}
