import { useEffect, useState } from 'react';
import { Alert, Linking, ScrollView, Switch, Text, View } from 'react-native';
import * as AppleAuthentication from 'expo-apple-authentication';
import * as Location from 'expo-location';
import * as Notifications from 'expo-notifications';
import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';
import Constants from 'expo-constants';
import { Button, s } from './ui';
import { request, connectDevice } from './api';
import { deleteAllData, discardOperation, setPreference, storageBytes, syncLibrary } from './library';
import { humanize, type Library, type Registry } from './libraryModel';
export function SettingsScreen({ library, registry, run }: { library: Library; registry: Registry; run: (action: () => Promise<any>) => void }) {
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
  return <ScrollView contentContainerStyle={[s.body, { gap: 18, paddingTop: 14 }]}><Text style={s.title}>Settings</Text>
    <View style={s.card}><Text style={s.heading}>Saved for you.</Text><Text style={s.text}>Your library works without an account. Sign in with Apple to bring it to another device.</Text>{library.apple_linked ? <Text style={s.strong}>Connected with Apple</Text> : appleAvailable ? <AppleAuthentication.AppleAuthenticationButton buttonType={AppleAuthentication.AppleAuthenticationButtonType.SIGN_IN} buttonStyle={AppleAuthentication.AppleAuthenticationButtonStyle.BLACK} cornerRadius={8} style={{ height: 48, width: '100%' }} onPress={() => run(signIn)} /> : <Text style={s.small}>Apple sign-in is unavailable on this device.</Text>}<Text style={s.small}>{library.last_synced ? 'Last synced ' + new Date(library.last_synced).toLocaleTimeString() : 'Stored on this device'}</Text><Button title="Sync now" secondary onPress={() => run(syncLibrary)} /></View>
    <View style={s.card}><Text style={s.strong}>Location</Text><Text style={s.text}>{location === 'granted' ? 'Enabled — distances are available.' : 'Optional — your saved map works without it.'}</Text><Text style={s.small}>Permission: {location}</Text><Button title="Open system settings" secondary onPress={() => void Linking.openSettings()} /></View>
    <View style={s.card}><View style={s.between}><View style={{ flex: 1 }}><Text style={s.strong}>Save-ready notifications</Text><Text style={s.small}>When a queued reel finishes during a refresh.</Text></View><Switch accessibilityLabel="Save-ready notifications" value={library.preferences.notifications} onValueChange={value => run(() => notifications(value))} /></View><Text style={s.small}>Permission: {notificationStatus}</Text></View>
    <View style={s.card}><Text style={s.strong}>Your data</Text><Text style={s.small}>Library storage: {(bytes / 1024 / 1024).toFixed(2)} MB</Text><Button title="Export all entries · JSON" secondary onPress={() => run(exportLibrary)} /><Button title="Delete all data" danger onPress={() => Alert.alert('Delete your library?', 'All entries, folders, queued saves and account links will be removed. Export a copy first if you want to keep them.', [{ text: 'Cancel', style: 'cancel' }, { text: 'Delete all data', style: 'destructive', onPress: () => run(deleteAllData) }])} /></View>
    {library.outbox.filter(o => o.error).map(o => <View key={o.id} style={s.card}><Text style={s.strong}>A change needs attention</Text><Text style={s.small}>{o.error}</Text><Button title="Discard this change" secondary onPress={() => run(() => discardOperation(o.id))} /></View>)}
    {__DEV__ ? <View style={s.card}><Text style={s.heading}>Processing & costs</Text><Button title="Refresh cost measurements" secondary onPress={() => run(async () => setCost(await request('/debug/cost')))} />{cost ? <><Text style={s.text}>{cost.saves} saves · ${cost.average_usd.toFixed(4)} per save</Text><Text style={s.small}>Place cache {(cost.cache_hit_rate * 100).toFixed(1)}% · prompt cache {(cost.prompt_cache_hit_rate * 100).toFixed(1)}%</Text>{Object.entries(cost.by_type || {}).map(([key, raw]) => { const value = raw as any; return <View key={key}><Text style={s.strong}>{registry[key]?.label || humanize(key)}</Text><Text style={s.small}>${value.estimated_usd.toFixed(4)}/save · LLM ${value.llm_usd.toFixed(4)} · uncached equivalent ${value.llm_uncached_equivalent_usd.toFixed(4)}</Text></View>; })}<Text style={s.small}>{cost.basis}</Text></> : <Text style={s.small}>Measured usage appears after processing saves.</Text>}</View> : null}
    <Text style={[s.small, { textAlign: 'center' }]}>ReelBot {Constants.expoConfig?.version || '1.0.0'} · build {Constants.expoConfig?.ios?.buildNumber || '20'}</Text>
  </ScrollView>;
}
