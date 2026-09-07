import { useEffect, useRef, useState } from 'react';
import { Alert, Pressable, ScrollView, Text, View } from 'react-native';
import MapView, { Marker, type Region } from 'react-native-maps';
import * as Location from 'expo-location';
import { colors as c } from './theme';
import { Button, Chip, Icon, s, TypeFilters } from './ui';
import { bounds, clusters, distanceLabel, nearby, type Coordinate } from './mapModel';
import type { Entry, Registry } from './libraryModel';
export function MapScreen({ entries, registry, location, onLocation, rationaleSeen, rememberRationale, open }: { entries: Entry[]; registry: Registry; location: Coordinate | null; onLocation: (p: Coordinate) => void; rationaleSeen: boolean; rememberRationale: () => void; open: (entry: Entry) => void }) {
  const map = useRef<MapView>(null), [type, setType] = useState<string | null>(null), [radius, setRadius] = useState<number | null>(null), [list, setList] = useState(false);
  const [focused, setFocused] = useState<Entry | null>(null), [region, setRegion] = useState<Region | null>(null), [permission, setPermission] = useState('undetermined');
  const [headerHeight, setHeaderHeight] = useState(155);
  const data = nearby(entries.filter(e => !type || e.content_type === type), location, radius), visible = data.map(r => r.entry), fallback = bounds(visible);
  useEffect(() => {
    let active = true;
    async function locate(request: boolean) {
      const status = request ? await Location.requestForegroundPermissionsAsync() : await Location.getForegroundPermissionsAsync();
      if (!active) return;
      setPermission(status.status);
      if (status.granted) {
        const cached = await Location.getLastKnownPositionAsync();
        if (cached && active) onLocation(cached.coords);
        try { const point = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced }); if (active) onLocation(point.coords); } catch { /* Saved places remain available without a current fix. */ }
      }
    }
    if (!rationaleSeen) {
      rememberRationale();
      Alert.alert('See what’s nearby', 'Use your location to show distances to saved places.', [
        { text: 'Not now', style: 'cancel', onPress: () => void locate(false).catch(() => {}) },
        { text: 'Continue', onPress: () => void locate(true).catch(() => {}) },
      ]);
    } else void locate(false).catch(() => {});
    return () => { active = false; };
  }, []);
  useEffect(() => { if (fallback) map.current?.animateToRegion(fallback, 350); }, [type, entries.length]);
  const markers = clusters(visible, region?.latitudeDelta || fallback?.latitudeDelta || 1, region?.longitude || fallback?.longitude || 0);
  return <View style={{ flex: 1 }}>
    {list ? <ScrollView contentContainerStyle={[s.body, { paddingTop: headerHeight + 12, gap: 12 }]}>{data.map(({ entry, distance }) => <Pressable key={entry.id} onPress={() => open(entry)} style={s.card}><View style={s.row}><Icon name={registry[entry.content_type]?.icon || 'bookmark'} /><Text style={[s.strong, { flex: 1 }]}>{entry.title}</Text>{distance != null ? <Text style={s.small}>{distanceLabel(distance)}</Text> : null}</View><Text style={s.small}>{entry.formatted_address}</Text></Pressable>)}{!data.length ? <Text style={s.text}>No saved places match these filters.</Text> : null}</ScrollView>
      : <MapView ref={map} style={{ flex: 1 }} initialRegion={fallback || { latitude: 20, longitude: 0, latitudeDelta: 100, longitudeDelta: 150 }} onRegionChangeComplete={setRegion} showsUserLocation={permission === 'granted'} showsMyLocationButton={permission === 'granted'} onPress={() => setFocused(null)}>
        {markers.map(marker => <Marker key={marker.id} coordinate={marker.coordinate} accessibilityLabel={marker.members.length > 1 ? `${marker.members.length} saved entries` : marker.members[0].title} onPress={event => {
          event.stopPropagation();
          if (marker.members.length > 1 && (region?.latitudeDelta || 1) > .08) map.current?.animateToRegion({ ...marker.coordinate, latitudeDelta: (region?.latitudeDelta || 1) / 3, longitudeDelta: (region?.longitudeDelta || 1) / 3 });
          else setFocused(marker.members[0]);
        }}><View style={{ borderRadius: 22, padding: 9, backgroundColor: c.card, borderWidth: 2, borderColor: c.accent, flexDirection: 'row', alignItems: 'center', gap: 4 }}><Icon name={registry[marker.members[0].content_type]?.icon || 'map-pin'} size={20} />{marker.members.length > 1 ? <Text style={s.strong}>{marker.members.length}</Text> : null}</View></Marker>)}
      </MapView>}
    <View onLayout={event => setHeaderHeight(event.nativeEvent.layout.height)} style={{ position: 'absolute', top: 0, left: 0, right: 0, backgroundColor: c.background, paddingHorizontal: 18, paddingBottom: 8 }}>
      <View style={s.between}><Text style={s.heading}>Your map</Text><Pressable accessibilityRole="button" onPress={() => setList(!list)}><Text style={s.link}>{list ? 'Map view' : 'Near me · list'}</Text></Pressable></View>
      <TypeFilters registry={registry} value={type} onChange={setType} />
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}><Chip label="Any distance" active={radius == null} onPress={() => setRadius(null)} />{[1, 5, 25, 100].map(n => <Chip key={n} label={`${n} km`} active={radius === n} onPress={() => setRadius(radius === n ? null : n)} />)}</ScrollView>
      {!location ? <Text style={[s.small, { marginTop: 7 }]}>Saved places are available. Enable location in Settings for distances.</Text> : null}
    </View>
    {!visible.length && !list ? <View pointerEvents="none" style={{ position: 'absolute', top: '48%', left: 30, right: 30 }}><View style={s.card}><Text style={s.heading}>A map of what you save.</Text><Text style={s.text}>Entries linked to places will appear here. Your other saves stay in Recent.</Text></View></View> : null}
    {focused && !list ? <View style={{ position: 'absolute', bottom: 18, left: 18, right: 18 }}><View style={s.card}><View style={s.between}><Icon name={registry[focused.content_type]?.icon || 'map-pin'} /><Pressable accessibilityLabel="Close place preview" onPress={() => setFocused(null)}><Icon name="close" /></Pressable></View><Text style={s.heading}>{focused.title}</Text><Text style={s.small}>{focused.formatted_address}</Text><Button title="View entry" onPress={() => open(focused)} />{visible.filter(e => e.place_id === focused.place_id && e.id !== focused.id).map(e => <Pressable key={e.id} onPress={() => open(e)}><Text style={s.link}>{e.title}</Text></Pressable>)}</View></View> : null}
  </View>;
}
