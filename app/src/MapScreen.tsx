import { MapPeekSheet } from './MapPeekSheet';
import type { ReactNode } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { ActionSheetIOS, Alert, AppState, Linking, ScrollView, View, useWindowDimensions } from 'react-native';
import { measureMotion } from './motion/Profiler';
import Animated, { useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated';
import { motion, timing } from './theme/motion';
import { Reveal, ChangeFade } from './motion/Motion';
import { useScrollTop } from './motion/scrollTop';
import { feedback } from './motion/haptics';
import { MovingMarker, useMarkerTransitions } from './motion/MapMotion';
import { Pressable, Text } from './controls';
import MapView, { Marker, type Region } from 'react-native-maps';
import * as Location from 'expo-location';
import { useTheme, fonts, useReducedMotion } from './theme';
import { Chip, Icon, Sheet, useUI } from './ui';
import { VenueKindIcon, VenueMarker, useVenueKinds } from './VenueKindIcon';
import { countBy, entryKind, majorityKind, venueSnapshot } from './venueModel';
import { useDistance } from './useDistance';
import { bounds, collisionClusters, nearby, type Coordinate } from './mapModel';
import type { Entry, Registry } from './libraryModel';
export function MapScreen({ entries, registry, location, onLocation, rationaleSeen, rememberRationale, open, renderDetail }: { entries: Entry[]; registry: Registry; location: Coordinate | null; onLocation: (p: Coordinate) => void; rationaleSeen: boolean; rememberRationale: () => void; open: (entry: Entry) => void; renderDetail: (entry: Entry, close: () => void) => ReactNode }) {
  const c = useTheme(), reduced = useReducedMotion(), listRef = useScrollTop(), cameraOpacity = useSharedValue(1);
  const cameraStyle = useAnimatedStyle(() => ({ opacity: cameraOpacity.value }));
  function moveCamera(target: Region, token: 'base' | 'page') { if (reduced) cameraOpacity.value = 0; map.current?.animateToRegion(target, reduced ? motion.reducedPosition : motion.duration[token]); cameraOpacity.value = withTiming(1, timing('quick')); }
  const s = useUI();
  const map = useRef<MapView>(null), kinds = useVenueKinds(), distance = useDistance(), { width, fontScale } = useWindowDimensions();
  const [filter, setFilter] = useState<string | null>(null), [radius, setRadius] = useState<number | null>(null), [list, setList] = useState(false);
  const [selected, setSelected] = useState<string | null>(null), [region, setRegion] = useState<Region | null>(null), [permission, setPermission] = useState('undetermined');
  const [collapsed, setCollapsed] = useState(false), [height, setHeight] = useState(600), [expanded, setExpanded] = useState<Entry[]>([]), [chipsHeight, setChipsHeight] = useState(62);
  const eligible = useMemo(() => venueSnapshot(entries, registry).map, [entries, registry]);
  const ranged = nearby(eligible, permission === 'granted' ? location : null, permission === 'granted' ? radius : null);
  const counts = countBy(ranged.map(row => row.entry), e => e.content_type === 'place' ? entryKind(e) : 'type:' + e.content_type);
  const active = filter && counts[filter] ? filter : null;
  const data = ranged.filter(({ entry: e }) => !active || (e.content_type === 'place' ? entryKind(e) : 'type:' + e.content_type) === active), visible = data.map(r => r.entry), fallback = bounds(visible);
  useEffect(() => {
    let alive = true;
    async function locate(request: boolean) {
      const status = request ? await Location.requestForegroundPermissionsAsync() : await Location.getForegroundPermissionsAsync();
      if (!alive) return; setPermission(status.status);
      if (status.granted) {
        const cached = await Location.getLastKnownPositionAsync(); if (cached && alive) onLocation(cached.coords);
        try { const point = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }); if (alive) onLocation(point.coords); } catch { /* Pins work without a fix. */ }
      }
    }
    if (!rationaleSeen) { rememberRationale(); Alert.alert('See what’s nearby', 'Use your location to show distances to saved places.', [{ text: 'Not now', style: 'cancel', onPress: () => void locate(false) }, { text: 'Continue', onPress: () => void locate(true) }]); }
    else void locate(false).catch(() => {});
    const observer = AppState.addEventListener('change', state => { if (state === 'active') void locate(false).catch(() => {}); });
    return () => { alive = false; observer.remove(); };
  }, []);
  useEffect(() => { if (fallback) moveCamera(fallback, 'base'); }, [active, entries.length]);
  const currentRegion = region || fallback || { latitude: 20, longitude: 0, latitudeDelta: 100, longitudeDelta: 150 };
  const clustered = useMemo(() => collisionClusters(visible, currentRegion, width, height), [visible.map(e => e.id + e.lat + e.lng).join(), currentRegion.latitude, currentRegion.longitude, currentRegion.latitudeDelta, currentRegion.longitudeDelta, width, height]);
  const markers = useMarkerTransitions(clustered);
  const previousCamera = useRef<Region | null>(null), snap = useRef(0);
  const [collapseRequest,setCollapseRequest]=useState(0);
  function tapMapBackground(){if(selected && snap.current>0)setCollapseRequest(value=>value+1);else closePeek();}
  const selectedEntry = visible.find(entry => entry.id === selected);
  function closePeek() { setSelected(null); snap.current=0; if(previousCamera.current){moveCamera(previousCamera.current,'page');previousCamera.current=null;} }
  function focusEntry(entry:Entry,index=0) {
    const occupied=index===0?190:index===1?height*.55:height*.92;
    moveCamera({...currentRegion,latitude:entry.lat!-currentRegion.latitudeDelta*occupied/(2*height),longitude:entry.lng!},'page');
  }
  function selectEntry(entry:Entry) { if(!selected)previousCamera.current=currentRegion;setSelected(entry.id);focusEntry(entry,snap.current); }
  useEffect(()=>{if(selected&&!selectedEntry)closePeek();},[selected,selectedEntry?.id]);
  const entryRow = (entry: Entry, km?: number | null) => <Pressable accessibilityRole="button" key={entry.id} accessibilityLabel={entry.title} onPress={() => { setExpanded([]); open(entry); }} style={s.card}><View style={s.row}>{entry.content_type === 'place' ? <VenueKindIcon kind={entryKind(entry)} /> : <Icon name={registry[entry.content_type]?.icon || 'bookmark'} />}<Text style={[s.strong, { flex: 1 }]}>{entry.title}</Text></View><Text style={s.small}>{entry.formatted_address}</Text>{km != null ? <Text style={s.small}>{distance.label(km)}</Text> : null}</Pressable>;
  function distances() { const values = [null, 1, 5, 25, 100]; ActionSheetIOS.showActionSheetWithOptions({ title: 'Distance', options: ['Any distance', ...values.slice(1).map(n => `${n} ${distance.unit}`), 'Cancel'], cancelButtonIndex: 5 }, i => { if (i < 5) setRadius(values[i] == null ? null : values[i]! * distance.factor); }); }
  return <View style={{ flex: 1 }}>
    <View style={{ paddingHorizontal: 20 }}><View style={[s.between, { minHeight: 44 }]}><Text accessibilityRole="header" style={s.strong}>{collapsed ? 'Map' : ''}</Text><View accessibilityRole="tablist" style={{ flexDirection: 'row', borderWidth: 1, borderColor: c.border, flexShrink: 1 }}>{['Map', 'List'].map((label, i) => <Pressable key={label} accessibilityRole="tab" accessibilityLabel={label + ' view'} accessibilityState={{ selected: list === !!i }} onPress={() => { feedback('selection'); setList(!!i); if(i)closePeek(); }} style={{ paddingHorizontal: 16, paddingVertical: 9, backgroundColor: list === !!i ? c.ink : c.card }}><Text style={[s.strong, { color: list === !!i ? c.card : c.textPrimary }]}>{label}</Text></Pressable>)}</View></View>{!collapsed ? <Text accessibilityRole="header" style={[s.title, { paddingBottom: 8 }]}>Map</Text> : null}</View>
    <View style={{ flex: 1 }} onLayout={e => setHeight(e.nativeEvent.layout.height)}>
      {list ? <ScrollView ref={listRef} onScroll={e => setCollapsed(e.nativeEvent.contentOffset.y > 30)} scrollEventThrottle={32} contentContainerStyle={[s.body, { paddingTop: chipsHeight + 8, gap: 12 }]}>{data.map(({ entry, distance: km }) => entryRow(entry, km))}{!data.length ? <Text style={s.text}>Save a place to see it here, or clear your distance filter.</Text> : null}</ScrollView>
        : <Animated.View style={[{ flex: 1 }, cameraStyle]}><MapView ref={map} style={{ flex: 1 }} initialRegion={currentRegion} onPanDrag={() => setCollapsed(true)} onRegionChangeComplete={setRegion} showsUserLocation={permission === 'granted'} showsMyLocationButton={false} userInterfaceStyle={c.background === '#15231E' ? 'dark' : 'light'} onPress={tapMapBackground}>
          {markers.map(marker => { const majority = majorityKind(marker.members); return <Marker key={marker.id} coordinate={marker.coordinate} anchor={{ x: .5, y: 1 }} tracksViewChanges accessibilityLabel={marker.members.length > 1 ? `${marker.members.length} saved entries${marker.members.every(entry => entry.city === marker.members[0].city) ? ' in ' + marker.members[0].city : ''}` : marker.members[0].title} onPress={event => {
            event.stopPropagation(); measureMotion('map-cluster'); feedback('selection');
            if (marker.members.length > 1) {
              const spread = bounds(marker.members)!;
              if (currentRegion.latitudeDelta <= .001 || marker.members.every(e => Math.abs(e.lat! - marker.coordinate.latitude) + Math.abs(e.lng! - marker.coordinate.longitude) < .00005)) setExpanded(marker.members);
              else moveCamera({ ...spread, latitudeDelta: Math.min(spread.latitudeDelta, currentRegion.latitudeDelta / 2), longitudeDelta: Math.min(spread.longitudeDelta, currentRegion.longitudeDelta / 2) }, 'page');
            } else { selectEntry(marker.members[0]); }
          }}><MovingMarker marker={marker} region={currentRegion} width={width} height={height}><VenueMarker kind={entryKind(marker.members[0])} selected={selected === marker.members[0].id && marker.members.length === 1} count={marker.members.length > 1 ? marker.members.length : undefined} fill={marker.members.length > 1 ? kinds[majority || 'other']?.color : undefined} /></MovingMarker></Marker>; })}
        </MapView></Animated.View>}
      <View style={{ position: 'absolute', top: 0, left: 0, right: 0 }} onLayout={e => setChipsHeight(e.nativeEvent.layout.height)}><ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, padding: 12 }}>{Object.entries(counts).map(([key, count]) => <Chip key={key} label={`${key.startsWith('type:') ? registry[key.slice(5)]?.plural_label || registry[key.slice(5)]?.label : kinds[key]?.plural_label || kinds[key]?.label || key} ${count}`} active={active === key} onPress={() => setFilter(active === key ? null : key)} icon={key.startsWith('type:') ? registry[key.slice(5)]?.icon : undefined} iconElement={!key.startsWith('type:') ? <VenueKindIcon kind={key} size={20} /> : undefined} />)}</ScrollView></View>
      {permission === 'granted' ? <Pressable accessibilityRole="button" accessibilityLabel="Distance filter" onPress={distances} style={{ position: 'absolute', right: 12, bottom: selectedEntry ? 204 : 18, backgroundColor: c.card, borderColor: c.border, borderWidth: 1, padding: 12 }}><Text style={[s.strong, fontScale > 1.6 && s.small]}>{radius == null ? 'Distance' : distance.label(radius)}</Text></Pressable> : null}
      {!visible.length && !list ? <View pointerEvents="none" style={{ position: 'absolute', top: '45%', left: 30, right: 30 }}><View style={s.card}><Text style={s.heading}>Save a place</Text><Text style={s.text}>{eligible.length ? 'Clear your distance filter to see more places.' : 'Share a reel with ReelBot. Places you save will appear on this map.'}</Text></View></View> : null}
      {!list ? <View style={{ position: 'absolute', bottom: 4, right: 8 }}>{[...new Map(visible.filter(e => e.resolution_attribution).map(e => [e.resolution_attribution!.url, e.resolution_attribution!])).values()].map(a => <Pressable key={a.url} accessibilityRole="link" onPress={() => void Linking.openURL(a.url)}><Text style={[s.small, { fontSize: 10, backgroundColor: c.card }]}>{a.label}</Text></Pressable>)}</View> : null}
      {selectedEntry && !list ? <MapPeekSheet entry={selectedEntry} height={height} collapseRequest={collapseRequest} distanceKm={data.find(row=>row.entry.id===selectedEntry.id)?.distance} onClose={closePeek} onSnap={index=>{snap.current=index;if(index>=0)focusEntry(selectedEntry,index);}} renderDetail={renderDetail}/> : null}
    </View>
    <Sheet visible={!!expanded.length} title={`${expanded.length} saved entries here`} onClose={() => setExpanded([])}>{expanded.map(e => entryRow(e))}</Sheet>
  </View>;
}
