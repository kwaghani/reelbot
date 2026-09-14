import { Linking } from 'react-native';
import { useContext, useEffect, useRef, useState } from 'react';
import { ActionSheetIOS, FlatList, RefreshControl, ScrollView, StyleSheet, useWindowDimensions, View } from 'react-native';
import Animated, { interpolate, Extrapolation, runOnJS, useAnimatedScrollHandler, useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import { GridCard, useGridEntries, type GridPositions } from './motion/GridMotion';
import { SearchSurface } from './motion/SearchSurface';
import type { OriginFrame } from './theme/motion';
import { measureMotion } from './motion/Profiler';
import { motion, reflow, disappear } from './theme/motion';
import { Reveal, FadeImage, ChangeFade, Flow, Skeleton } from './motion/Motion';
import { ScrollRequest } from './motion/scrollTop';
import { feedback } from './motion/haptics';
import { Pressable, Text, TextInput } from './controls';
import * as Location from 'expo-location';
import { useTheme, fonts, folderTint, useReducedMotion } from './theme';
import { Chip, Icon, useUI } from './ui';
import { distanceKm, distanceLabel, type Coordinate } from './mapModel';
import { canAddSourceInfo, canRetrySave, humanize, reviewQuestion, statusLabels, type Entry, type Folder, type Library, type Operation, type Registry, type Save } from './libraryModel';
import { setPreference } from './library';
import { VenueKindIcon, useVenueKinds } from './VenueKindIcon';
import { entryKind, typeFolders } from './venueModel';
import { VenueThumbnail, imageryViewportChanged } from './VenueImagery';
import { useDistance } from './useDistance';
import { entryCount, libraryProjection, primaryFacet } from './organizationModel';

function menu(title: string, actions: { label: string; run: () => void }[]) {
  ActionSheetIOS.showActionSheetWithOptions({ title, options: [...actions.map(a => a.label), 'Cancel'], cancelButtonIndex: actions.length }, index => actions[index]?.run());
}

function Cover({ entry, registry }: { entry: Entry; registry: Registry }) {
  const c = useTheme();
  const s = useUI();
  const styles = useStyles();
  const [failed, setFailed] = useState(false), tint = folderTint(registry[entry.content_type]?.plural_label || entry.content_type, c);
  useEffect(() => setFailed(false), [entry.thumbnail]);
  if (entry.content_type === 'place') return <View style={styles.cover}><VenueThumbnail entry={entry} /></View>;
  return <View style={[styles.cover, { backgroundColor: tint.background }]}>
    {entry.thumbnail && !failed ? <FadeImage accessibilityLabel={`Cover for ${entry.title}`} source={{ uri: entry.thumbnail }} resizeMode="cover" onError={() => setFailed(true)} style={StyleSheet.absoluteFill} />
      : <><Icon name={registry[entry.content_type]?.icon || 'bookmark-outline'} size={40} color={tint.foreground} /><Text style={[styles.placeholderType, { color: tint.foreground }]}>{registry[entry.content_type]?.label || humanize(entry.content_type)}</Text></>}
  </View>;
}

function FolderRow({ folder, count, open, edit, mutate }: { folder: Folder; count: number; open: () => void; edit: () => void; mutate: (op: Omit<Operation, 'id'>) => void }) {
  const c = useTheme();
  const s = useUI();
  const styles = useStyles();
  const reduced = useReducedMotion(), x = useSharedValue(0), start = useSharedValue(0), [swiped, setSwiped] = useState(false);
  const hide = () => { mutate({ kind: 'folder_edit', target: folder.id, method: 'PATCH', path: `/folders/${folder.id}`, body: { hidden: !folder.hidden } }); setSwiped(false); x.value = withSpring(0, motion.spring); };
  const action = folder.hidden ? 'Show' : 'Hide';
  const committed = (opened: boolean) => { setSwiped(opened); };
  const pan = Gesture.Pan().enabled(!folder.id.startsWith('venue:')).activeOffsetX([-12, 12]).failOffsetY([-10, 10])
    .onStart(() => { start.value = x.value; })
    .onUpdate(event => { x.value = Math.max(-84, Math.min(0, start.value + event.translationX)); })
    .onEnd(event => { const opened = x.value + event.velocityX * .08 < -40; x.value = reduced ? (opened ? -84 : 0) : withSpring(opened ? -84 : 0, motion.spring); runOnJS(committed)(opened); })
    .onFinalize((_event, success) => { if (!success) x.value = reduced ? 0 : withSpring(0, motion.spring); });
  const swipeStyle = useAnimatedStyle(() => ({ transform: [{ translateX: x.value }] }));
  return <View style={styles.folderSwipe}>
    <Pressable accessibilityElementsHidden={!swiped} accessibilityRole="button" accessibilityLabel={`${action} ${folder.name}`} onPress={hide} style={styles.swipeAction}><Text style={{ color: c.inkText, fontWeight: '600' }}>{action}</Text></Pressable>
    <GestureDetector gesture={pan}><Animated.View style={swipeStyle}>
      <Pressable accessibilityRole="button" accessibilityLabel={`${folder.name}, ${entryCount(count)}${folder.hidden ? ', hidden' : ''}`} onPress={open}
        onLongPress={folder.id.startsWith('venue:') ? undefined : () => menu(folder.name, [{ label: action, run: hide }, ...(folder.kind === 'custom' ? [{ label: 'Edit folder', run: edit }] : [])])} style={styles.folderRow}>
        <View style={[styles.folderIcon, { backgroundColor: folderTint(folder.name, c).background }]}>{folder.facet_key === 'venue_kind' ? <VenueKindIcon kind={folder.facet_value!} size={26} /> : <Icon name={folder.icon || 'folder-outline'} size={21} />}</View>
        <View style={{ flex: 1, gap: 3 }}><Text style={s.strong}>{folder.name}{folder.hidden ? ' (hidden)' : ''}</Text><Text style={s.small}>{entryCount(count)}</Text></View><Icon name="chevron-right" size={19} color={c.textSecondary} />
      </Pressable>
    </Animated.View></GestureDetector>
  </View>;
}

export type RecentProps = {
  navigateFolder?: (id: string | null) => void; goBack?: () => void; focused?: boolean;
  library: Library; registry: Registry; loaded: boolean; query: string; onQuery: (value: string) => void; semantic: Entry[];
  folderId: string | null; onFolder: (id: string | null) => void; browsing: boolean; onBrowse: (value: boolean) => void;
  location: Coordinate | null; onLocation: (point: Coordinate) => void; open: (entry: Entry, edit?: boolean, origin?: import('./theme/motion').OriginFrame) => void;
  add: () => void; createFolder: () => void; editFolder: (folder: Folder) => void; retry: (save: Save) => void; assist: (save: Save) => void;
  inspect: (id: string) => void; dismiss: (entry: Entry) => void; mutate: (op: Omit<Operation, 'id'>) => void;
  assign: (ids: string[], mode: 'copy' | 'move') => void; sync: () => Promise<unknown>;
};

export function RecentScreen(props: RecentProps) {
  const c = useTheme();
  const s = useUI();
  const styles = useStyles(), reduced = useReducedMotion();
  const { library, registry, folderId, browsing, query, onQuery } = props;
  const { fontScale } = useWindowDimensions(), kinds = useVenueKinds(), distanceFormatter = useDistance();
  const [venueKind, setVenueKind] = useState<string | null>(null);
  const placeRoot = library.folders.find(f => f.kind === 'auto_type' && f.content_type === 'place');
  const derivedFolders = typeFolders(library.items, kinds, placeRoot);
  const allFolders = [...library.folders, ...derivedFolders];
  const [searchOrigin, setSearchOrigin] = useState<OriginFrame | null>(null);
  const [searchOpen, setSearchOpen] = useState(false), [type, setType] = useState<string | null>(null), [review, setReview] = useState(false), [facet, setFacet] = useState<string | null>(null);
  const [hidden, setHidden] = useState(false), [selecting, setSelecting] = useState(false), [selected, setSelected] = useState<string[]>([]), [verify, setVerify] = useState<string | null>(null);
  const y = useSharedValue(0), scroll = useRef<FlatList<Entry>>(null), dragging = useSharedValue(false);
  const request = useContext(ScrollRequest), lastRequest = useRef(request), firstIds = useRef<string[] | null>(null), seen = useRef(new Set<string>());
  const [refreshing, setRefreshing] = useState(false);
  const [debouncedQuery, setDebouncedQuery] = useState(query);
  useEffect(() => { const timer = setTimeout(() => (query !== debouncedQuery && measureMotion('search-results'), setDebouncedQuery(query)), motion.delay.search); return () => clearTimeout(timer); }, [query]);
  useEffect(() => { if (request !== lastRequest.current && props.focused !== false) scroll.current?.scrollToOffset({ offset: 0, animated: !reduced }); lastRequest.current = request; }, [request, props.focused, reduced]);
  const view = libraryProjection([...library.items, ...props.semantic.filter(e => !library.items.some(local => local.id === e.id))], allFolders, registry, { kinds, folderId, query: debouncedQuery, type, review, facet, venueKind, semanticIds: props.semantic.map(e => e.id) });
  const renderedItems = useGridEntries(view.items, library.items), gridPositions = useRef<GridPositions>(new Map());
  const generation = renderedItems.map(e => e.id + e.needs_review).join() + '|' + verify;
  const folder = view.folder, contextual = !!folder || browsing;
  const [headerHeight, setHeaderHeight] = useState(0), [contentsReady, setContentsReady] = useState(!contextual);
  useEffect(() => { if (!contextual) { setContentsReady(true); return; } setContentsReady(false); const timer = setTimeout(() => setContentsReady(true), motion.duration.page + motion.delay.folder); return () => clearTimeout(timer); }, [folderId, browsing]);
  useEffect(() => { if (view.tier === 'empty' && contextual) { props.onFolder(null); props.onBrowse(false); } }, [view.tier, contextual]);
  useEffect(() => { setType(null); setFacet(null); setVenueKind(null); setReview(false); setSelecting(false); setSelected([]); setVerify(null); setSearchOpen(false); onQuery(''); scroll.current?.scrollToOffset({ offset: 0, animated: false }); y.value = 0; }, [folderId, browsing]);
  useEffect(() => {
    let active = true;
    void (async () => {
      if (!(await Location.getForegroundPermissionsAsync()).granted) return;
      const cached = await Location.getLastKnownPositionAsync();
      if (cached && active) props.onLocation(cached.coords);
      const point = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
      if (active) props.onLocation(point.coords);
    })().catch(() => {});
    return () => { active = false; };
  }, []);
  useEffect(() => { if (type !== view.type) setType(view.type); if (review !== view.review) setReview(view.review); if (facet !== view.facet) setFacet(view.facet); }, [view.type, view.review, view.facet]);
  const roots = library.folders.filter(f => !f.parent_folder_id && (hidden || !f.hidden) && (view.folderCounts[f.id] || f.kind === 'custom')).sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name));
  const childFolders = folder?.id === placeRoot?.id && library.preferences.groupBy === 'type' ? derivedFolders : library.folders.filter(f => f.parent_folder_id === folderId && view.filteredFolderCounts[f.id]);
  const children = childFolders.filter(f => hidden || !f.hidden);
  const hiddenLink = (available: boolean) => available ? <Pressable accessibilityRole="button" onPress={() => setHidden(!hidden)} style={{ alignSelf: 'center', minHeight: 44, justifyContent: 'center' }}><Text style={s.link}>{hidden ? 'Show visible folders only' : 'Show hidden folders'}</Text></Pressable> : null;
  const browse = (id: string | null) => { measureMotion('folder-push'); if (props.navigateFolder) props.navigateFolder(id); else { props.onFolder(id); props.onBrowse(!id); } };
  const more = () => menu(folder?.name || (browsing ? 'Folders' : 'Saved'), [
    ...(view.items.length && !browsing ? [{ label: 'Select entries', run: () => { setSelecting(true); setSelected([]); } }] : []),
    ...(view.tier !== 'empty' ? [{ label: 'New folder', run: props.createFolder }] : []),
    ...(folder && !folder.id.startsWith('venue:') ? [{ label: folder.hidden ? 'Show folder' : 'Hide folder', run: () => props.mutate({ kind: 'folder_edit', target: folder.id, method: 'PATCH', path: `/folders/${folder.id}`, body: { hidden: !folder.hidden } }) }] : []),
    ...(folder?.kind === 'custom' ? [{ label: 'Edit folder', run: () => props.editFolder(folder) }] : []),
  ]);
  const iconButton = (label: string, icon: string, action: () => void) => <Pressable accessibilityRole="button" accessibilityLabel={label} onPress={action} onPressFrame={label === 'Search saved entries' ? frame => { setSearchOrigin(frame); action(); } : undefined} style={styles.navButton}><Icon name={icon} size={23} color={c.accent} /></Pressable>;
  const revealSearch = () => { measureMotion('search-open'); setSearchOpen(true); scroll.current?.scrollToOffset({ offset: 0, animated: !reduced }); };
  const pending = library.saves.filter(save => !['resolved', 'needs_review'].includes(save.status) || !library.items.some(e => e.save_id === save.id));

  if (props.loaded && !firstIds.current) firstIds.current = view.items.slice(0, motion.cap.grid).map(entry => entry.id);
  const titleStyle = useAnimatedStyle(() => ({ opacity: interpolate(y.value, [28, 58], [0, 1], Extrapolation.CLAMP) }));
  const viewportTick = useSharedValue(0), searchTriggered = useSharedValue(false);
  const scrollHandler = useAnimatedScrollHandler({
    onBeginDrag: () => { runOnJS(measureMotion)('grid-scroll', 2000); dragging.value = true; searchTriggered.value = false; },
    onEndDrag: () => { dragging.value = false; },
    onScroll: event => { y.value = event.contentOffset.y; const now = Date.now(); if (now - viewportTick.value > 120) { viewportTick.value = now; runOnJS(imageryViewportChanged)(); }
      if (dragging.value && !searchTriggered.value && event.contentOffset.y < -48 && view.tier !== 'empty' && !contextual && !searchOpen) { searchTriggered.value = true; runOnJS(revealSearch)(); }
    },
  });
  const renderCard = ({ item: entry, index }: { item: Entry; index: number }) => {
        const isSelected = selected.includes(entry.id), facetLabel = primaryFacet(entry, registry), neighborhood = entry.attributes.neighborhood;
        const distance = props.location && entry.lat != null && entry.lng != null ? distanceKm(props.location, { latitude: entry.lat, longitude: entry.lng }) : null;
        const initialIndex = firstIds.current?.indexOf(entry.id) ?? -1, animate = initialIndex >= 0 && !seen.current.has(entry.id);
        seen.current.add(entry.id);
        return <GridCard id={entry.id} generation={generation} positions={gridPositions.current} scrollY={y} leaving={!library.items.some(row => row.id === entry.id) && !props.semantic.some(row => row.id === entry.id)} style={[styles.card, isSelected && { borderColor: c.accent, borderWidth: 2 }]}>
          <Reveal animate={animate} delay={Math.max(0, initialIndex) * motion.delay.grid}>
          <Pressable accessibilityRole="button" accessibilityLabel={entry.title} onPressFrame={frame => selecting ? setSelected(ids => isSelected ? ids.filter(id => id !== entry.id) : [...ids, entry.id]) : props.open(entry, false, frame)}
            onLongPress={() => menu(entry.title, [{ label: 'Select entry', run: () => { setSelecting(true); setSelected([entry.id]); } }, { label: 'Edit entry', run: () => props.open(entry, true) }, ...(__DEV__ ? [{ label: 'Save diagnostics', run: () => props.inspect(entry.save_id) }] : [])])}>
            <Cover entry={entry} registry={registry} />
            <View style={styles.cardText}><View style={[s.row, { alignItems: 'flex-start', gap: 6 }]}>{!selecting && entry.content_type === 'place' ? <VenueKindIcon kind={entryKind(entry)} size={18} /> : <Icon name={selecting ? isSelected ? 'checkbox-marked-circle' : 'checkbox-blank-circle-outline' : registry[entry.content_type]?.icon || 'bookmark'} size={15} />}<Text  style={styles.cardTitle}>{entry.title}</Text></View>
              {facetLabel ? <Text  style={styles.facet}>{facetLabel}</Text> : null}
              {neighborhood && humanize(neighborhood) !== facetLabel ? <Text style={s.small}>{humanize(neighborhood)}</Text> : null}
              {distance != null ? <Text style={s.small}>{distanceFormatter.label(distance)} away</Text> : null}
            </View>
          </Pressable>
          {entry.needs_review ? <Animated.View layout={reflow(reduced)} exiting={disappear(reduced)} style={styles.review}><View style={[s.row, { alignItems: 'flex-start', gap: 4 }]}><Pressable accessibilityRole="button" onPress={() => setVerify(verify === entry.id ? null : entry.id)} style={{ flex: 1 }}><Text style={[s.link, { paddingVertical: 4 }]}>{reviewQuestion(entry)}</Text></Pressable><Pressable accessibilityRole="button" accessibilityLabel={`Dismiss review for ${entry.title}`} onPress={() => props.dismiss(entry)} hitSlop={8} style={{ paddingVertical: 4 }}><Icon name="close" size={17} /></Pressable></View>
            {verify === entry.id ? <Reveal style={{ gap: 6 }}>{entry.candidate?.place_candidates?.slice(0, 3).map(option => <Pressable key={option.place.id} accessibilityRole="button" onPress={() => { measureMotion('verification-accept'); props.mutate({ kind: 'choose_place', target: entry.id, method: 'POST', path: `/items/${entry.id}/choose-place`, body: { place_id: option.place.id } }); }} style={{ paddingVertical: 8, borderTopWidth: 1, borderColor: c.border }}><Text style={s.link}>{option.place.displayName.text}</Text><Text style={s.small}>{option.place.formattedAddress}</Text>{typeof option.distance_m === 'number' && Number.isFinite(option.distance_m) && option.distance_m >= 0 ? <Text style={s.small}>{distanceFormatter.label(option.distance_m / 1000)} from search center</Text> : null}</Pressable>)}<Pressable accessibilityRole="button" onPress={() => props.open(entry, true)}><Text style={s.link}>Edit details</Text></Pressable></Reveal> : null}
          </Animated.View> : null}
          </Reveal>
        </GridCard>;
      };
  return <View style={{ flex: 1 }}>
    {!contextual ? <View style={styles.nav}>
      <Animated.Text accessibilityRole="header" style={[styles.inlineTitle, titleStyle]}>Saved</Animated.Text>
      <View style={{ flex: 1 }} />
      {view.tier !== 'empty' ? <>{iconButton('Search saved entries', 'magnify', revealSearch)}{iconButton('Saved options', 'dots-horizontal', more)}</> : null}
      {iconButton('Save a reel', 'plus', props.add)}
    </View> : null}
    <ChangeFade outgoingProps={{ ListHeaderComponent: <View style={{ height: headerHeight }} />, refreshControl: undefined, onScroll: undefined, scrollEnabled: false, contentOffset: { x: 0, y: y.value } }} changeKey={[props.loaded, view.tier === 'empty', debouncedQuery, type, review, facet, venueKind].join('|')} style={{ flex: 1 }}>
    <Animated.FlatList key={fontScale > 1.6 ? 'one-column' : 'two-columns'} ref={scroll} data={browsing || !contentsReady ? [] : renderedItems} numColumns={fontScale > 1.6 ? 1 : 2}
      keyExtractor={entry => entry.id} renderItem={renderCard} initialNumToRender={8} maxToRenderPerBatch={4} windowSize={3} removeClippedSubviews={false}
      columnWrapperStyle={fontScale > 1.6 ? undefined : { justifyContent: 'space-between', alignItems: 'flex-start' }}
      ItemSeparatorComponent={() => <View style={{ height: 14 }} />} keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag" alwaysBounceVertical scrollEventThrottle={16}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); void props.sync().finally(() => setRefreshing(false)); }} tintColor={c.accent} />}
      onScroll={scrollHandler} contentContainerStyle={styles.content} ListHeaderComponent={<View onLayout={event => setHeaderHeight(event.nativeEvent.layout.height)}>
      {contextual ? <View style={[styles.contextHeader, fontScale > 1.6 && { flexWrap: 'wrap' }]}>
        {iconButton('Back', 'chevron-left', () => { if (props.goBack) { props.goBack(); return; } if (folder?.parent_folder_id) browse(folder.parent_folder_id); else if (folder) browse(null); else { props.onBrowse(false); props.onFolder(null); } })}
        {fontScale > 1.6 ? <View style={{ marginLeft: 'auto' }}>{iconButton('Folder options', 'dots-horizontal', more)}</View> : null}
        <View style={[{ flex: 1, minWidth: 0, gap: 4 }, fontScale > 1.6 && { flexBasis: '100%', paddingHorizontal: 12 }]}><Text accessibilityRole="header" style={styles.folderTitle}>{folder?.name || 'Folders'}</Text><Text style={s.small}>{entryCount(folder ? view.folderCounts[folder.id] || 0 : view.all.length)}</Text></View>
        {fontScale <= 1.6 ? iconButton('Folder options', 'dots-horizontal', more) : null}
      </View> : <Text accessibilityRole="header" style={styles.largeTitle}>Saved</Text>}
      {view.tier !== 'empty' && !contextual ? <SearchSurface visible={searchOpen} origin={searchOrigin} style={[s.row, { marginBottom: 12 }]}><TextInput autoFocus accessibilityLabel="Search your saves" placeholder="Search your saves" value={query} onChangeText={onQuery} clearButtonMode="while-editing" style={[s.input, { flex: 1 }]} /><Pressable accessibilityRole="button" onPress={() => { onQuery(''); setSearchOpen(false); }}><Text style={s.link}>Cancel</Text></Pressable></SearchSurface> : null}
      {view.tier === 'empty' && !pending.length && props.loaded ? <Reveal style={styles.onboarding}>
        <View accessibilityLabel="Share a reel, then save it in ReelBot" style={styles.shareVisual}><View style={styles.visualPhone}><Icon name="play-circle-outline" size={42} /><Icon name="export-variant" size={25} color={c.accent} /></View><Icon name="arrow-right" size={26} color={c.textSecondary} /><View style={styles.visualSaved}><Icon name="bookmark-check-outline" size={44} color={c.accent} /></View></View>
        <Text style={styles.onboardingTitle}>Save your first reel</Text><Text style={[s.text, { textAlign: 'center' }]}>Open a reel, tap Share, then choose ReelBot.</Text>
      </Reveal> : null}
      {view.showTypeChips && !browsing ? <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
        {view.reviewCount ? <Chip label={`Needs review ${view.reviewCount}`} active={view.review} onPress={() => { setReview(!view.review); setType(null); }} /> : null}
        {Object.entries(view.typeCounts).map(([key, count]) => <Chip key={key} label={`${registry[key]?.plural_label || humanize(key)} ${count}`} icon={registry[key]?.icon} active={view.type === key} onPress={() => setType(view.type === key ? null : key)} />)}
      </ScrollView> : null}
      {folder?.id === placeRoot?.id ? <View style={[s.row, { marginBottom: 16, flexWrap: 'wrap' }]}><Text style={s.strong}>Group by</Text>{(['city', 'type'] as const).map(group => <Chip key={group} label={humanize(group)} active={library.preferences.groupBy === group} onPress={() => void setPreference('groupBy', group)} />)}</View> : null}
      {(folder?.content_type === 'place' && folder.facet_key !== 'venue_kind' || type === 'place') && Object.keys(view.kindCounts).length ? <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>{Object.entries(view.kindCounts).map(([key, count]) => <Chip key={key} label={`${kinds[key]?.plural_label || kinds[key]?.label || key} ${count}`} iconElement={<VenueKindIcon kind={key} size={20} />} active={view.venueKind === key} onPress={() => setVenueKind(view.venueKind === key ? null : key)} />)}</ScrollView> : null}
      {folder && Object.keys(view.facetCounts).length ? <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
        {Object.entries(view.facetCounts).map(([value, count]) => <Chip key={value} label={`${value} ${count}`} active={view.facet === value} onPress={() => setFacet(view.facet === value ? null : value)} />)}
      </ScrollView> : null}
      {!contextual && view.tier === 'large' && !query && !type && !review ? <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.shortcuts}>
        {roots.filter(f => !f.hidden).slice(0, 6).map(f => <Pressable accessibilityRole="button" accessibilityLabel={`Open ${f.name}`} key={f.id} onPress={() => browse(f.id)} style={styles.shortcut}><Icon name={f.icon || 'folder-outline'} size={18} /><Text style={s.strong}>{f.name}</Text><Text style={s.small}>{view.folderCounts[f.id] || 0}</Text></Pressable>)}
        <Pressable accessibilityRole="button" onPress={() => browse(null)} style={styles.shortcut}><Text style={s.link}>All folders</Text><Icon name="chevron-right" size={18} /></Pressable>
      </ScrollView> : null}
      {selecting && !browsing ? <View style={[s.between, { marginBottom: 12 }]}><Text style={s.strong}>{selected.length} selected</Text><View style={s.row}>{selected.length ? <><Pressable accessibilityRole="button" onPress={() => { props.assign(selected, 'copy'); setSelecting(false); setSelected([]); }}><Text style={s.link}>Copy</Text></Pressable><Pressable accessibilityRole="button" onPress={() => { props.assign(selected, 'move'); setSelecting(false); setSelected([]); }}><Text style={s.link}>Move</Text></Pressable></> : null}<Pressable accessibilityRole="button" onPress={() => { setSelecting(false); setSelected([]); }}><Text style={s.link}>Done</Text></Pressable></View></View> : null}
      {contentsReady && browsing && view.tier !== 'empty' ? <View style={{ gap: 8 }}>{roots.map(f => <FolderRow key={f.id} folder={f} count={view.folderCounts[f.id] || 0} open={() => browse(f.id)} edit={() => props.editFolder(f)} mutate={props.mutate} />)}
        {hiddenLink(library.folders.some(f => !f.parent_folder_id && f.hidden))}
      </View> : null}
    </View>} ListFooterComponent={<>
      {contentsReady && folder?.kind === 'auto_type' && childFolders.length ? <View style={{ gap: 8, marginTop: 20 }}><Text style={styles.sectionTitle}>Browse {folder.content_type === 'place' ? library.preferences.groupBy === 'type' ? 'types' : 'cities' : 'folders'}</Text>{children.map(f => <FolderRow key={f.id} folder={f} count={view.filteredFolderCounts[f.id] || 0} open={() => browse(f.id)} edit={() => props.editFolder(f)} mutate={props.mutate} />)}{hiddenLink(childFolders.some(f => f.hidden))}</View> : null}
      {!contextual && view.tier === 'small' ? <Pressable accessibilityRole="button" onPress={() => browse(null)} style={styles.browseLink}><Text style={s.link}>Browse folders</Text><Icon name="chevron-right" size={17} color={c.accent} /></Pressable> : null}
      {!contextual && pending.length ? <View style={{ marginTop: 16, gap: 10 }}>{pending.map(save => <View key={save.id} style={s.card}><View style={s.row}><Icon name="bookmark-outline" /><Text style={[s.strong, { flex: 1 }]}>{save.status === 'partial_extraction' ? `Found ${save.extracted_venue_count || 0} of ${save.expected_venue_count || '?'} places` : statusLabels[save.status]}</Text>{canRetrySave(save) ? <Pressable accessibilityRole="button" onPress={() => props.retry(save)}><Text style={s.link}>{save.is_compilation || save.status === 'extraction_empty' ? 'Retry with deeper scan' : 'Retry'}</Text></Pressable> : null}</View>{canAddSourceInfo(save) ? <Pressable accessibilityRole="button" onPress={() => props.assist(save)}><Text style={s.link}>Add venue or topic</Text></Pressable> : null}{save.is_compilation || save.status === 'extraction_empty' ? <Pressable accessibilityRole="link" onPress={() => void Linking.openURL(save.canonical_url || save.source_url)}><Text style={s.link}>Watch reel</Text></Pressable> : null}{save.error_reason && save.status !== 'partial_extraction' ? <Text style={s.small}>{save.error_reason}</Text> : null}</View>)}</View> : null}
      {!browsing && view.tier !== 'empty' && !view.items.length ? <Reveal rise={0}><Text style={[s.text, { paddingVertical: 24 }]}>Try another name, city, or note.</Text></Reveal> : null}
      {library.sync_error ? <Pressable accessibilityRole="button" onPress={props.sync} style={styles.browseLink}><Icon name="cloud-off-outline" size={18} /><Text style={s.small}>Saved on this device / Retry sync</Text></Pressable> : null}
      {!props.loaded ? <Skeleton /> : null}
    </>} />
    </ChangeFade>
  </View>;
}

function useStyles() { const c = useTheme(), { fontScale } = useWindowDimensions(); return StyleSheet.create({
  nav: { minHeight: Math.max(44, 24 * fontScale), flexDirection: 'row', alignItems: 'center', paddingHorizontal: 12 }, navButton: { width: 44, minHeight: 44, alignItems: 'center', justifyContent: 'center' }, inlineTitle: { fontSize: 17, fontWeight: '600', color: c.textPrimary, position: 'absolute', left: 20 },
  content: { paddingHorizontal: 20, paddingBottom: 28 }, largeTitle: { fontFamily: fonts.display, fontSize: 34, lineHeight: 39, fontWeight: '700', letterSpacing: -.6, color: c.textPrimary, marginBottom: 18, marginTop: 4 },
  contextHeader: { flexDirection: 'row', alignItems: 'flex-start', marginLeft: -12, marginRight: -8, paddingTop: 6, paddingBottom: 18 }, folderTitle: { fontFamily: fonts.display, fontSize: 23, fontWeight: '700', color: c.textPrimary, flexShrink: 1 },
  chips: { gap: 8, paddingBottom: 16 }, shortcuts: { gap: 8, paddingBottom: 16 }, shortcut: { backgroundColor: c.card, borderRadius: 3, paddingHorizontal: 12, minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 8 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', rowGap: 14 }, card: { width: fontScale > 1.6 ? '100%' : '48.3%', backgroundColor: c.card, borderRadius: 3, overflow: 'hidden', borderWidth: 1, borderColor: c.border },
  cover: { width: '100%', aspectRatio: fontScale > 1.6 ? 2.4 : 1.18, alignItems: 'center', justifyContent: 'center', gap: 8, overflow: 'hidden' }, placeholderType: { fontSize: 11, fontWeight: '500' },
  cardText: { padding: 11, gap: 7 }, cardTitle: { flex: 1, fontSize: 15, fontWeight: '600', lineHeight: 21, color: c.textPrimary }, facet: { alignSelf: 'flex-start', maxWidth: '100%', fontSize: 10, color: c.textSecondary, backgroundColor: c.cardMuted, paddingHorizontal: 7, paddingVertical: 3, borderRadius: 5 },
  review: { paddingHorizontal: 10, paddingBottom: 8, borderTopWidth: 1, borderColor: c.border }, browseLink: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 5, marginTop: 14, minHeight: 44 }, sectionTitle: { fontSize: 13, fontWeight: '600', color: c.textSecondary, marginBottom: 4 },
  folderSwipe: { borderRadius: 3, overflow: 'hidden', backgroundColor: c.accent }, folderRow: { minHeight: 64, paddingVertical: 10, paddingHorizontal: 12, backgroundColor: c.card, flexDirection: 'row', alignItems: 'center', gap: 12 }, folderIcon: { width: 38, height: 38, borderRadius: 2, alignItems: 'center', justifyContent: 'center' }, swipeAction: { position: 'absolute', right: 0, top: 0, bottom: 0, width: 84, alignItems: 'center', justifyContent: 'center' },
  onboarding: { alignItems: 'center', paddingTop: 66, paddingBottom: 40, gap: 14 }, onboardingTitle: { fontFamily: fonts.display, fontSize: 22, fontWeight: '600', textAlign: 'center', color: c.textPrimary }, shareVisual: { flexDirection: 'row', alignItems: 'center', gap: 20, marginBottom: 30 }, visualPhone: { width: 85, height: 125, borderWidth: 2, borderColor: c.ink, borderRadius: 3, alignItems: 'center', justifyContent: 'center', gap: 18, backgroundColor: c.card }, visualSaved: { width: 80, height: 90, backgroundColor: c.accentSoft, borderRadius: 3, alignItems: 'center', justifyContent: 'center' },
}); }
