import { useScrollTop } from './motion/scrollTop';
import { useEffect, useState } from 'react';
import { ScrollView, View } from 'react-native';
import { Text } from './controls';
import { Button, useUI } from './ui';
import { appConfig, FeatureFlags } from './config';
import { readShareDiagnostics, runContainerCanary } from './sharedGroup';
import { drainContainer } from './library';

export function ShareDiagnosticsScreen({ close }: { close: () => void }) {
  const s = useUI(), scroll = useScrollTop();
  const [snapshot, setSnapshot] = useState<any>(null), [error, setError] = useState('');
  const [busy, setBusy] = useState(false), [action, setAction] = useState('');
  useEffect(() => {
    let active = true, reading = false;
    const refresh = async () => {
      if (reading) return; reading = true;
      try { const value = await readShareDiagnostics(); if (active) { setSnapshot(value); setError(''); } }
      catch (err) { if (active) { setSnapshot(null); setError(String(err)); } }
      finally { reading = false; }
    };
    void refresh(); const timer = setInterval(() => void refresh(), 2000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  async function perform(name: string, fn: () => Promise<unknown>) {
    setBusy(true); setAction(`${name}…`);
    try { const result = await fn(); setAction(`${name}: success\n${JSON.stringify(result, null, 2)}`); }
    catch (err) { setAction(`${name}: failed\n${err instanceof Error ? err.message : String(err)}`); }
    finally { setBusy(false); }
  }
  const raw = (value: unknown) => <Text selectable style={s.small}>{JSON.stringify(value, null, 2)}</Text>;
  return <ScrollView ref={scroll} contentContainerStyle={[s.body, { gap: 16 }]}>
    <Text style={s.title}>Share diagnostics</Text><Button title="Close diagnostics" secondary onPress={close} />
    <Text style={s.small}>Refreshes every two seconds. This screen reads local evidence; it does not upload it.</Text>
    {error ? <Text accessibilityLiveRegion="polite" style={s.strong}>Container unreadable: {error}</Text> : null}
    <View style={s.card}><Text style={s.heading}>App Group</Text>
      <Text selectable style={s.text}>Runtime native identifier: {snapshot?.container_id || 'Unavailable'}</Text>
      <Text selectable style={s.small}>App configuration: {appConfig.appGroupIdentifier}</Text>
      {snapshot?.container_id && snapshot.container_id !== appConfig.appGroupIdentifier ? <Text style={s.strong}>Mismatch: the native and app configuration identifiers differ.</Text> : null}
      <Text style={s.text}>Container reachable: {snapshot ? snapshot.reachable ? 'Yes' : 'No' : 'Unknown'}</Text>
      <Text selectable style={s.small}>{snapshot?.path || 'No resolved path'}</Text>
      {snapshot?.error ? <Text selectable style={s.strong}>{snapshot.error}</Text> : null}
      <Button title="Run canary test" secondary disabled={busy} onPress={() => void perform('Canary write/read', runContainerCanary)} />
    </View>
    {action ? <Text selectable accessibilityLiveRegion="polite" style={s.text}>{action}</Text> : null}
    <View style={s.card}><Text style={s.heading}>Pending queue ({snapshot?.pending?.length ?? '?'})</Text>
      {snapshot?.pending_error ? <Text style={s.strong}>{snapshot.pending_error}</Text> : raw(snapshot?.pending || [])}
      <Button title="Drain queue now" disabled={busy} onPress={() => void perform('Manual drain', () => drainContainer('manual'))} />
      <Text style={s.small}>Draining commits links to this device before acknowledging them. Sync handles processing separately.</Text>
    </View>
    <View style={s.card}><Text style={s.heading}>Last drain</Text>{raw(snapshot?.last_drain || snapshot?.last_drain_error || 'No drain recorded')}
      <Text style={s.strong}>Last drain with items or errors</Text>{raw(snapshot?.last_meaningful_drain || snapshot?.last_meaningful_drain_error || 'No nonempty drain recorded')}
      <Text style={s.strong}>Native queue scan</Text>{raw(snapshot?.last_queue_read || snapshot?.last_queue_read_error || 'No scan recorded')}
    </View>
    <View style={s.card}><Text style={s.heading}>Quarantined files</Text>{raw(snapshot?.quarantine_error || snapshot?.quarantine || [])}</View>
    <View style={s.card}><Text style={s.heading}>Extension attempt log</Text>
      {!snapshot?.reachable ? <Text style={s.strong}>Container unreadable. Attempt history cannot be inspected. A failed container write cannot leave a trace here.</Text>
        : snapshot.attempts_error ? <Text style={s.strong}>Attempt log unreadable: {snapshot.attempts_error}</Text>
        : !snapshot.attempts?.length ? <Text style={s.text}>No attempts logged. The extension may not have launched, may be writing to another App Group, or may have failed before it could write. An empty log does not prove success. Run the canary, then share one link.</Text>
        : [...snapshot.attempts].reverse().map((entry: any, index: number) => <View key={entry.attempt_id || index} style={{ gap: 4 }}><Text style={s.strong}>{new Date(entry.timestamp).toLocaleString()} — {entry.outcome}</Text>{raw(entry)}</View>)}
      <Text style={s.small}>“started” without a final outcome can mean an interrupted or killed extension. source_app is null because iOS does not expose it through a supported public API. Times in raw records are Unix milliseconds.</Text>
    </View>
    <View style={s.card}><Text style={s.heading}>Build info</Text>
      <Text style={s.text}>Version {snapshot?.version || '?'} / build {snapshot?.build || '?'} / {snapshot?.configuration || 'Unknown configuration'}</Text>
      <Text style={s.small}>FeatureFlags.groupsPlaceholder: {String(FeatureFlags.groupsPlaceholder)}</Text>
    </View>
  </ScrollView>;
}
