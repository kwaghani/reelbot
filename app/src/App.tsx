import { EntryDetail } from './EntryDetail';
import { visibleAttributes, attributeText } from './attributeModel';
import { useFonts } from 'expo-font';
import { useEffect, useState } from 'react';
import { ActivityIndicator, Alert, useWindowDimensions, AppState, Image, Linking, ScrollView, View } from 'react-native';
import { Pressable, Text, TextInput } from './controls';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import * as Crypto from 'expo-crypto';
import NetInfo from '@react-native-community/netinfo';
import { useTheme, fonts, useReducedMotion } from './theme';
import { appConfig, FeatureFlags, visualFixture } from './config';
import { request } from './api';
import { loadLibrary, subscribe, saveUrl, queueOperation, syncLibrary, coldStartLibrary, retrySave, setPreference } from './library';
import { emptyLibrary, humanize, fieldLabel, type Save, type Entry, type Folder, type Registry } from './libraryModel';
import { registerRefresh } from './background';
import { optionalDebugViews } from './debugViews';
import { MapScreen } from './MapScreen';
import { RecentNavigator, SettingsNavigator, TabScene } from './motion/Navigation';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { Reveal, SelectionIcon, ChangeFade, FadeImage } from './motion/Motion';
import { motion, type OriginFrame } from './theme/motion';
import { ErrorNotice, ActionFeedback } from './motion/ErrorNotice';
import { MotionProfiler, measureMotion } from './motion/Profiler';
import { feedback } from './motion/haptics';
import { SettingsScreen } from './SettingsScreen';
import { EntryEditor } from './EntryEditor';
import { Button, Icon, Sheet, useUI, TypeFilters } from './ui';
import { VenueKindsContext, VenueKindIcon } from './VenueKindIcon';
import { entryKind } from './venueModel';
import { VenueImageryProvider, VenueGallery } from './VenueImagery';
import { DistancePreference } from './useDistance';
import { type Coordinate } from './mapModel';
type Tab = 'Recent' | 'Map' | 'Groups' | 'Settings';
function AppContent() {
  const { width: screenWidth } = useWindowDimensions();
  const c = useTheme();
  const s = useUI();
  const [library, setLibrary] = useState(emptyLibrary()), [loaded, setLoaded] = useState(false), [tab, setTab] = useState<Tab>('Recent');
  const [query, setQuery] = useState(''), [semantic, setSemantic] = useState<Entry[]>([]);
  const [folderBrowse, setFolderBrowse] = useState(false), [activeFolder, setActiveFolder] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]), [assignMode, setAssignMode] = useState<'copy' | 'move' | null>(null);
  const [addOpen, setAddOpen] = useState(false), [url, setUrl] = useState(''), [detailId, setDetailId] = useState<string | null>(null), [editing, setEditing] = useState(false), [note, setNote] = useState('');
  const [folderEditor, setFolderEditor] = useState<Folder | 'new' | null>(null), [folderName, setFolderName] = useState(''), [busy, setBusy] = useState(false), [location, setLocation] = useState<Coordinate | null>(null), [extraDetails, setExtraDetails] = useState<any>(null);
  const [sourceSave, setSourceSave] = useState<Save | null>(null), [sourceTitle, setSourceTitle] = useState(''), [sourceCity, setSourceCity] = useState(''), [sourceType, setSourceType] = useState('place'), [diagnostics, setDiagnostics] = useState<any>(null);
  const registry: Registry = Object.keys(library.registry).length ? library.registry : appConfig.contentTypes;
  const views = FeatureFlags.groupsPlaceholder ? optionalDebugViews() : [];
  const tabs: { name: Tab; icon: string }[] = [{ name: 'Recent', icon: 'bookmark-outline' }, { name: 'Map', icon: 'map-outline' }, ...(FeatureFlags.groupsPlaceholder && views.length ? [{ name: 'Groups' as Tab, icon: 'account-multiple-outline' }] : []), { name: 'Settings', icon: 'cog-outline' }];
  const [actionError, setActionError] = useState('');
  const [recentReset, setRecentReset] = useState(0);
  const [origin, setOrigin] = useState<OriginFrame | null>(null), [scrollRequests, setScrollRequests] = useState<Record<string, number>>({});
  function selectTab(next: Tab) { measureMotion('tab:' + next); if (next === tab) setScrollRequests(value => ({ ...value, [next]: (value[next] || 0) + 1 })); else { feedback('selection'); setTab(next); } }
  const detail = library.items.find(e => e.id === detailId) || null;
  useEffect(() => {
    let active = true;
    const refresh = () => loadLibrary().then(state => { if (active) { setLibrary(state); setLoaded(true); } });
    const unsubscribe = subscribe(() => { void refresh(); });
    void refresh();
    void coldStartLibrary().then(refresh).catch(error => Alert.alert('Pending saves', String(error))).finally(() => { void syncLibrary(); });
    const appState = AppState.addEventListener('change', value => { if (value === 'active') void syncLibrary('foreground'); });
    const network = NetInfo.addEventListener(state => { if (state.isConnected) void syncLibrary(); });
    const timer = setInterval(() => { if (AppState.currentState === 'active') void syncLibrary(); }, 4000);
    void registerRefresh().catch(() => {});
    return () => { active = false; unsubscribe(); appState.remove(); network(); clearInterval(timer); };
  }, []);
  useEffect(() => {
    let active = true; setSemantic([]);
    if (!query.trim() || visualFixture) return;
    const timer = setTimeout(() => { request<{ items: Entry[] }>('/items?q=' + encodeURIComponent(query)).then(result => { if (active) setSemantic(result.items); }).catch(() => {}); }, motion.delay.search);
    return () => { active = false; clearTimeout(timer); };
  }, [query]);
  function run(action: () => Promise<any>) { setBusy(true); return action().catch(error => { feedback('error'); setActionError(error instanceof Error ? error.message : String(error)); }).finally(() => setBusy(false)); }
  async function mutate(operation: Parameters<typeof queueOperation>[0]) { await queueOperation(operation); if (['choose_place', 'entry_edit', 'note', 'source_info'].includes(operation.kind)) feedback('success'); void syncLibrary(); }
  function open(entry: Entry, edit = false, frame?: OriginFrame) { measureMotion(tab === 'Map' ? 'marker-sheet' : 'card-detail'); setOrigin(frame || null); setDetailId(entry.id); setEditing(edit); setNote(entry.note); setExtraDetails(null); }
  async function dismiss(entry: Entry) { await mutate({ kind: 'dismiss_review', target: entry.id, method: 'POST', path: `/items/${entry.id}/dismiss-review` }); }
  async function saveFolder() {
    if (!folderName.trim()) throw Error('Enter a folder name.');
    if (folderEditor === 'new') await mutate({ kind: 'folder_create', method: 'POST', path: '/folders', body: { id: Crypto.randomUUID(), name: folderName.trim() } });
    else if (folderEditor) await mutate({ kind: 'folder_edit', target: folderEditor.id, method: 'PATCH', path: '/folders/' + folderEditor.id, body: { name: folderName.trim() } });
    setFolderEditor(null);
  }
  function inspect(saveId: string) { if (__DEV__) run(async () => setDiagnostics(await request('/debug/saves/' + saveId))); }
  function assist(save: Save) { setSourceSave(save); setSourceTitle(save.source_info_hint || ''); setSourceCity(''); setSourceType('place'); }
  function newFolder() { setFolderName(''); setFolderEditor('new'); }
  return <ActionFeedback.Provider value={{ message: actionError, dismiss: () => setActionError('') }}><VenueImageryProvider entries={library.items}><VenueKindsContext.Provider value={Object.keys(library.venue_kinds).length ? library.venue_kinds : appConfig.venueKinds}><DistancePreference.Provider value={library.preferences.distanceUnits}><SafeAreaView style={s.safe} edges={['top', 'bottom']}><StatusBar style="auto" />{actionError ? <ErrorNotice message={actionError} dismiss={() => setActionError('')} /> : null}
    {busy ? <Reveal rise={0} style={[s.row, { justifyContent: 'center', padding: 7, position: 'absolute', top: 0, left: 0, right: 0, zIndex: 10, backgroundColor: c.background }]}><ActivityIndicator size="small" color={c.accent} /><Text style={s.small}>Saving your change…</Text></Reveal> : null}
    <View style={{ flex: 1 }}>
      <TabScene active={tab === 'Recent'} scrollRequest={scrollRequests.Recent || 0}><RecentNavigator key={recentReset} library={library} registry={registry} loaded={loaded} query={query} onQuery={setQuery} semantic={semantic}
      folderId={activeFolder} onFolder={setActiveFolder} browsing={folderBrowse} onBrowse={setFolderBrowse} location={location} onLocation={setLocation}
      open={open} add={() => setAddOpen(true)} createFolder={newFolder} editFolder={folder => { setFolderName(folder.name); setFolderEditor(folder); }}
      retry={save => run(() => retrySave(save))} assist={assist} inspect={inspect} dismiss={entry => run(() => dismiss(entry))}
      mutate={operation => run(() => mutate(operation))} assign={(ids, mode) => { setSelected(ids); setAssignMode(mode); }} sync={() => run(syncLibrary)} /></TabScene>
      <TabScene active={tab === 'Map'} scrollRequest={scrollRequests.Map || 0}><MapScreen entries={library.items} registry={registry} location={location} onLocation={setLocation} rationaleSeen={library.preferences.locationRationaleSeen} rememberRationale={() => void setPreference('locationRationaleSeen', true)} open={open} renderDetail={(entry, close) => <EntryDetail key={entry.id} detail={entry} registry={registry} entries={library.items} context="map" mutate={mutate} onClose={close} onError={setActionError} />} /></TabScene>
      <TabScene active={tab === 'Settings'} scrollRequest={scrollRequests.Settings || 0}><View style={[s.between, { paddingHorizontal: 22, paddingVertical: 12 }]}><View style={[s.row, { flex: 1, minWidth: 0, marginRight: 12 }]}><Image source={require('../assets/icon.png')} style={{ width: 30, height: 30, borderRadius: 7 }} /><Text style={{ fontSize: 23, fontWeight: '800', color: c.ink, letterSpacing: -.8, flexShrink: 1 }}>ReelBot</Text></View><Pressable accessibilityRole="button" accessibilityLabel="Save a reel" onPress={() => setAddOpen(true)} style={{ backgroundColor: c.accent, width: 40, height: 40, borderRadius: 3, alignItems: 'center', justifyContent: 'center' }}><Icon name="plus" color={c.inkText} /></Pressable></View><SettingsNavigator library={library} registry={registry} run={run} /></TabScene>
      {FeatureFlags.groupsPlaceholder ? <TabScene active={tab === 'Groups'} scrollRequest={scrollRequests.Groups || 0}><ScrollView>{views.map((ViewComponent, i) => <ViewComponent key={i} />)}</ScrollView></TabScene> : null}
    </View>
    <View style={{ flexDirection: 'row', backgroundColor: c.card, borderTopWidth: 1, borderColor: c.border, paddingTop: 12, paddingBottom: 5 }}>{tabs.map(item => <Pressable key={item.name} accessibilityRole="tab" accessibilityLabel={item.name} accessibilityState={{ selected: tab === item.name }} onPress={() => selectTab(item.name)} style={{ flex: 1, alignItems: 'center', paddingHorizontal: 4, gap: 4 }}><SelectionIcon active={tab === item.name}><ChangeFade token="instant" changeKey={String(tab === item.name)}><Icon name={item.icon} color={tab === item.name ? c.accent : c.textSecondary} /></ChangeFade></SelectionIcon><Text style={{ width: Math.max(40, screenWidth / tabs.length - 20), textAlign: 'center', flexShrink: 1, fontSize: 11, color: tab === item.name ? c.accent : c.textSecondary, fontWeight: '600' }}>{item.name}</Text></Pressable>)}</View>
    {__DEV__ ? <Sheet visible={!!diagnostics} title="Save diagnostics" onClose={() => setDiagnostics(null)}>{diagnostics ? <Text selectable style={[s.small, { fontFamily: 'Courier', fontSize: 11 }]}>{JSON.stringify(diagnostics, null, 2)}</Text> : null}</Sheet> : null}
    <Sheet visible={!!sourceSave} title="Save what caught your eye" onClose={() => setSourceSave(null)}><Text style={s.text}>Open the reel, then add the venue or topic you want to keep.</Text><Button secondary title="Open in app" onPress={() => sourceSave && void Linking.openURL(sourceSave.canonical_url || sourceSave.source_url)} /><TextInput accessibilityLabel="Venue or topic name" placeholder="Venue or topic name" value={sourceTitle} onChangeText={setSourceTitle} maxLength={200} style={s.input} /><TypeFilters registry={registry} value={sourceType} onChange={value => setSourceType(value || 'place')} />{sourceType === 'place' ? <TextInput accessibilityLabel="City" placeholder="City, if known" value={sourceCity} onChangeText={setSourceCity} maxLength={200} style={s.input} /> : null}<Button title="Save" loading={busy} onPress={() => run(async () => { if (!sourceTitle.trim() || !sourceSave) throw Error('Enter a venue or topic name.'); await mutate({ kind: 'source_info', target: sourceSave.id, method: 'POST', path: `/saves/${sourceSave.id}/source-info`, body: { entry_id: Crypto.randomUUID(), title: sourceTitle.trim(), content_type: sourceType, city: sourceCity || null } }); setSourceSave(null); })} /><Text style={s.small}>You can fill in more details afterward. This change is kept on your device while offline.</Text></Sheet>
    <Sheet visible={addOpen} title="Save a reel" onClose={() => setAddOpen(false)}><Text style={s.text}>Paste an Instagram, TikTok, or YouTube video link.</Text><TextInput accessibilityLabel="Reel URL" value={url} onChangeText={setUrl} autoCapitalize="none" autoCorrect={false} placeholder="Paste a video link" style={s.input} /><Button title="Save" loading={busy} onPress={() => run(async () => { await saveUrl(url); feedback('success'); setUrl(''); setAddOpen(false); setTab('Recent'); setRecentReset(value => value + 1); setActiveFolder(null); setFolderBrowse(false); void syncLibrary(); })} /><Text style={s.small}>Or share from Instagram, TikTok, or YouTube and choose ReelBot. The link saves immediately, even offline.</Text></Sheet>
    <Sheet visible={!!folderEditor} title={folderEditor === 'new' ? 'New folder' : 'Edit folder'} onClose={() => setFolderEditor(null)}><TextInput accessibilityLabel="Folder name" value={folderName} onChangeText={setFolderName} placeholder="e.g. Things to try" maxLength={100} style={s.input} /><Button title="Save" loading={busy} onPress={() => run(saveFolder)} />{folderEditor && folderEditor !== 'new' ? <Button danger title="Delete folder, keep entries" loading={busy} onPress={() => run(async () => { await mutate({ kind: 'folder_delete', target: folderEditor.id, method: 'DELETE', path: '/folders/' + folderEditor.id }); setFolderEditor(null); })} /> : null}</Sheet>
    <Sheet visible={!!assignMode} title={assignMode === 'move' ? 'Move entries' : 'Copy entries'} onClose={() => setAssignMode(null)}><Text style={s.text}>Choose a custom folder. Entries stay in their automatic folders.</Text>{library.folders.filter(f => f.kind === 'custom').map(folder => <Button key={folder.id} secondary title={folder.name} loading={busy} onPress={() => run(async () => { await mutate({ kind: 'assign', method: 'POST', path: '/folders/assign', body: { item_ids: selected, destination_id: folder.id, source_id: activeFolder, move: assignMode === 'move' } }); setAssignMode(null); setSelected([]); })} />)}{!library.folders.some(f => f.kind === 'custom') ? <Button title="Create a folder" secondary onPress={() => { setAssignMode(null); newFolder(); }} /> : null}</Sheet>
    <Sheet origin={origin} visible={!!detail} title={editing ? 'Edit entry' : detail?.title || 'Entry'} onClose={() => { setDetailId(null); setEditing(false); }}>
      {detail ? <EntryDetail key={detail.id} detail={detail} registry={registry} entries={library.items} initialEditing={editing} mutate={mutate} onClose={() => setDetailId(null)} onError={setActionError} /> : null}
    </Sheet>
  </SafeAreaView></DistancePreference.Provider></VenueKindsContext.Provider></VenueImageryProvider></ActionFeedback.Provider>;
}
export default function App() {
  const [ready, error] = useFonts({ 'CabinetGrotesk-Bold': require('../assets/fonts/CabinetGrotesk-Bold.otf'), 'Switzer-Regular': require('../assets/fonts/Switzer-Regular.otf'), 'Switzer-Semibold': require('../assets/fonts/Switzer-Semibold.otf') });
  if (!ready && !error) return null;
  return <GestureHandlerRootView style={{ flex: 1 }}><SafeAreaProvider><MotionProfiler /><AppContent /></SafeAreaProvider></GestureHandlerRootView>;
}
