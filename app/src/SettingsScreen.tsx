import { PhotoDiagnosticsScreen } from './PhotoDiagnosticsScreen';
import { useTheme } from './theme';
import { useEffect, useRef, useState } from 'react';
import { Alert, Linking, ScrollView, Switch, View } from 'react-native';
import { ShareDiagnosticsScreen } from './ShareDiagnosticsScreen';
import { Pressable, Text } from './controls';
import { useScrollTop } from './motion/scrollTop';
import * as AppleAuthentication from 'expo-apple-authentication';
import * as Location from 'expo-location';
import * as Notifications from 'expo-notifications';
import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';
import Constants from 'expo-constants';
import { Button, Chip, useUI } from './ui';
import { request, connectDevice } from './api';
import { deleteAllData, discardOperation, setPreference, storageBytes, syncLibrary, loadLibrary } from './library';
import { humanize, type Library, type Registry } from './libraryModel';
export type SettingsProps = { library: Library; registry: Registry; run: (action: () => Promise<any>) => void; openDiagnostics?: () => void };
export function SettingsScreen({ library, registry, run, openDiagnostics }: SettingsProps) {
  const scroll = useScrollTop();
  const c = useTheme();
  const [diagnosticsAvailable, setDiagnosticsAvailable] = useState(__DEV__), [showDiagnostics, setShowDiagnostics] = useState(false);
  const [showImageDiagnostics,setShowImageDiagnostics]=useState(false);
  const versionTaps = useRef({ count: 0, time: 0 });
  function tapVersion() {
    const now = Date.now(); versionTaps.current.count = now - versionTaps.current.time < 3000 ? versionTaps.current.count + 1 : 1; versionTaps.current.time = now;
    if (versionTaps.current.count >= 5) { versionTaps.current.count = 0; setDiagnosticsAvailable(true); openDiagnostics ? openDiagnostics() : setShowDiagnostics(true); }
  }
  const [syncing, setSyncing] = useState(false), [syncFeedback, setSyncFeedback] = useState('');
  async function syncNow() {
    setSyncing(true); setSyncFeedback('');
    try { await syncLibrary(); const latest = await loadLibrary(); setSyncFeedback(latest.sync_error ? '' : 'Synced just now'); }
    catch (error) { setSyncFeedback(error instanceof Error ? error.message : 'Sync could not finish'); }
    finally { setSyncing(false); }
  }
  const s = useUI();
  const [location, setLocation] = useState('Not requested'), [notificationStatus, setNotificationStatus] = useState('Not requested'), [bytes, setBytes] = useState(0), [cost, setCost] = useState<any>(null), [appleAvailable, setAppleAvailable] = useState(false);
  useEffect(() => { void Location.getForegroundPermissionsAsync().then(p => setLocation(p.status)); void Notifications.getPermissionsAsync().then(p => setNotificationStatus(p.status)); void storageBytes().then(setBytes); void AppleAuthentication.isAvailableAsync().then(setAppleAvailable); }, []);
  async function signIn() {
    await syncLibrary(); await connectDevice();
    const challenge = await request<{ nonce: string }>('/auth/apple/challenge');
    const credential = await AppleAuthentication.signInAsync({ requestedScopes: [], nonce: challenge.nonce });
    if (!credential.identityToken) throw Error('Apple did not return a sign-in token.');
    await request('/auth/apple', 'POST', { identity_token: credential.identityToken, nonce: challenge.nonce }); await syncLibrary();
  }
  async function exportLibrary() {
    const path = FileSystem.cacheDirectory + 'ReelBot-export.json';
    await FileSystem.writeAsStringAsync(path, JSON.stringify({ format: 'reelbot.entries.v1', exported_at: new Date().toISOString(), entries: library.items, saves: library.saves.map(({ cost, ...save }) => save), folders: library.folders, pending_changes: library.outbox }, null, 2));
    await Sharing.shareAsync(path, { mimeType: 'application/json', UTI: 'public.json', dialogTitle: 'Export your ReelBot library' });
  }
  async function notifications(enabled: boolean) {
    const permission = enabled ? await Notifications.requestPermissionsAsync() : await Notifications.getPermissionsAsync();
    setNotificationStatus(permission.status);
    await setPreference('notifications', enabled && permission.granted);
  }
  if (showImageDiagnostics) return <PhotoDiagnosticsScreen entries={library.items} close={()=>setShowImageDiagnostics(false)}/>;
  if (showDiagnostics) return <ShareDiagnosticsScreen close={() => setShowDiagnostics(false)} />;
  return <ScrollView ref={scroll} contentContainerStyle={[s.body, { gap: 18, paddingTop: 14 }]}><Text style={s.title}>Settings</Text>
    <View style={s.card}><Text style={s.heading}>Your library</Text><Text style={s.text}>Your library works without an account. Sign in with Apple to bring it to another device.</Text>{library.apple_linked ? <Text style={s.strong}>Connected with Apple</Text> : appleAvailable ? <AppleAuthentication.AppleAuthenticationButton buttonType={AppleAuthentication.AppleAuthenticationButtonType.SIGN_IN} buttonStyle={AppleAuthentication.AppleAuthenticationButtonStyle.BLACK} cornerRadius={8} style={{ height: 48, width: '100%' }} onPress={() => run(signIn)} /> : <Text style={s.small}>Apple sign-in is unavailable on this device.</Text>}<Text accessibilityLiveRegion="polite" style={s.strong}>{syncing ? 'Syncing…' : library.sync_error ? 'Sync could not finish' : syncFeedback || (library.last_synced ? 'Library synced' : 'Stored on this device')}</Text>{library.sync_error && !syncing ? <Text style={s.small}>{library.sync_error === 'Aborted' ? 'The processing service did not respond. Your saves are still on this device.' : library.sync_error}</Text> : null}{library.last_synced ? <Text style={s.small}>{'Last successful sync: ' + new Date(library.last_synced).toLocaleString()}</Text> : null}<Button title="Sync now" secondary loading={syncing} onPress={() => void syncNow()} /></View>
    <View style={s.card}><Text style={s.strong}>Location</Text><Text style={s.text}>{location === 'granted' ? 'Enabled — distances are available.' : 'Optional — your saved map works without it.'}</Text><Text style={s.small}>Permission: {location}</Text><Text style={s.strong}>Distance units</Text><View style={s.wrap}>{([{ key: 'auto', label: 'Device region' }, { key: 'imperial', label: 'Miles' }, { key: 'metric', label: 'Kilometres' }] as const).map(unit => <Chip key={unit.key} label={unit.label} active={library.preferences.distanceUnits === unit.key} onPress={() => run(() => setPreference('distanceUnits', unit.key))} />)}</View><Button title="Open system settings" secondary onPress={() => void Linking.openSettings()} /></View>
    <View style={s.card}><View style={s.between}><View style={{ flex: 1 }}><Text style={s.strong}>Save-ready notifications</Text><Text style={s.small}>When a queued reel finishes during a refresh.</Text></View><Switch accessibilityLabel="Save-ready notifications" value={library.preferences.notifications} onValueChange={value => run(() => notifications(value))} /></View><Text style={s.small}>Permission: {notificationStatus}</Text></View>
    <View style={s.card}><Text style={s.strong}>Your data</Text><Text style={s.small}>Library storage: {(bytes / 1024 / 1024).toFixed(2)} MB</Text><Button title="Export entries as JSON" secondary onPress={() => run(exportLibrary)} /><Button title="Delete all data" danger onPress={() => Alert.alert('Delete your library?', 'All entries, folders, queued saves and account links will be removed. Export a copy first if you want to keep them.', [{ text: 'Cancel', style: 'cancel' }, { text: 'Delete all data', style: 'destructive', onPress: () => run(deleteAllData) }])} /></View>
    {library.outbox.filter(o => o.error).map(o => <View key={o.id} style={s.card}><Text style={s.strong}>A change needs attention</Text><Text style={s.small}>{o.error}</Text><Button title="Discard this change" secondary onPress={() => run(() => discardOperation(o.id))} /></View>)}
    {__DEV__ ? <View style={s.card}><Text style={s.heading}>Processing & costs</Text><Button title="Refresh cost measurements" secondary onPress={() => run(async () => setCost(await request('/debug/cost')))} />{cost ? <><Text style={s.text}>{cost.saves} saves / ${cost.average_usd.toFixed(4)} per save</Text><Text style={s.small}>Place cache {(cost.cache_hit_rate * 100).toFixed(1)}% / prompt cache {(cost.prompt_cache_hit_rate * 100).toFixed(1)}%</Text>{Object.entries(cost.by_type || {}).map(([key, raw]) => { const value = raw as any; return <View key={key}><Text style={s.strong}>{registry[key]?.label || humanize(key)}</Text><Text style={s.small}>${value.estimated_usd.toFixed(4)}/save / LLM ${value.llm_usd.toFixed(4)} / uncached equivalent ${value.llm_uncached_equivalent_usd.toFixed(4)}</Text></View>; })}<Text style={s.small}>{cost.basis}</Text></> : <Text style={s.small}>Measured usage appears after processing saves.</Text>}</View> : null}
    {diagnosticsAvailable ? <Button title="Share diagnostics" secondary onPress={() => openDiagnostics ? openDiagnostics() : setShowDiagnostics(true)} /> : null}
    {diagnosticsAvailable ? <Button title="Image diagnostics" secondary onPress={()=>setShowImageDiagnostics(true)}/> : null}
    <Pressable accessibilityRole="button" accessibilityLabel="ReelBot version" onPress={tapVersion}><Text style={[s.small, { textAlign: 'center', padding: 12 }]}>ReelBot {Constants.expoConfig?.version || '1.0.0'} / build {Constants.expoConfig?.ios?.buildNumber || '?'}</Text></Pressable>
  </ScrollView>;
}
