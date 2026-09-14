import * as SQLite from 'expo-sqlite';
import * as Crypto from 'expo-crypto';
import { canonicalReelUrl } from './reelUrls';
import { request, connectDevice, ApiError } from './api';
import { emptyLibrary, upgradeLibrary, applyOperation, type Library, type Operation, type Save } from './libraryModel';
import { resetIdentity } from './identity';
import { readSharedQueue, acknowledgeSharedEntry, quarantineSharedEntry, recordDrain } from './sharedGroup';
import { visualFixture } from './config';
let fixtureLibrary: Library | null = null;
const fixture = () => fixtureLibrary || (fixtureLibrary = require('./visualFixture').makeVisualFixture());
let database: Promise<SQLite.SQLiteDatabase> | null = null;
const listeners = new Set<() => void>();
export function subscribe(callback: () => void) { listeners.add(callback); return () => { listeners.delete(callback); }; }
async function db() {
  if (!database) database = (async () => {
    const connection = await SQLite.openDatabaseAsync('reelbot-personal.db');
    await connection.execAsync('PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; CREATE TABLE IF NOT EXISTS library (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);');
    await connection.runAsync('INSERT OR IGNORE INTO library(id,value) VALUES(1,?)', JSON.stringify(emptyLibrary()));
    return connection;
  })();
  return database;
}
export async function loadLibrary(): Promise<Library> {
  if (visualFixture) return JSON.parse(JSON.stringify(fixture()));
  const row = await (await db()).getFirstAsync<{ value: string }>('SELECT value FROM library WHERE id=1');
  return row ? upgradeLibrary(JSON.parse(row.value)) : emptyLibrary();
}
async function update(mutator: (state: Library) => void) {
  if (visualFixture) { mutator(fixture()); listeners.forEach(callback => callback()); return; }
  await (await db()).withExclusiveTransactionAsync(async txn => {
    const row = await txn.getFirstAsync<{ value: string }>('SELECT value FROM library WHERE id=1');
    const state = row ? upgradeLibrary(JSON.parse(row.value)) : emptyLibrary();
    mutator(state);
    await txn.runAsync('UPDATE library SET value=? WHERE id=1', JSON.stringify(state));
  });
  listeners.forEach(callback => callback());
}
export async function saveUrl(value: string, timestamp = Date.now()) {
  const url = canonicalReelUrl(value);
  if (!url) throw new Error('Paste an Instagram, TikTok, or YouTube video link.');
  await update(state => {
    if (!state.saves.some(s => canonicalReelUrl(s.source_url) === url)) state.saves.unshift({ id: Crypto.randomUUID(), source_url: value.trim(), status: 'queued', created_at: new Date(timestamp).toISOString(), local: true });
  });
}
export async function queueOperation(input: Omit<Operation, 'id'>) {
  const operation = { ...input, id: Crypto.randomUUID() };
  await update(state => { applyOperation(state, operation);
    if (visualFixture && operation.kind === 'choose_place') applyOperation(state, { ...operation, kind: 'dismiss_review' });
    state.outbox.push(operation); });
}
let draining: Promise<DrainReport> | null = null;
type DrainReport = { timestamp: number; trigger: string; count: number; successes: number; failures: number; quarantined: number; errors: string[]; duration_ms: number };
export function drainContainer(trigger = 'sync'): Promise<DrainReport> {
  if (!draining) draining = performDrain(trigger).finally(() => { draining = null; });
  return draining;
}
async function performDrain(trigger: string): Promise<DrainReport> {
  const report: DrainReport = { timestamp: Date.now(), trigger, count: 0, successes: 0, failures: 0, quarantined: 0, errors: [], duration_ms: 0 };
  if (visualFixture) return report;
  try {
    const entries = await readSharedQueue(); report.count = entries.length;
    for (const entry of entries) {
      try {
        if (!canonicalReelUrl(entry.url)) {
          await quarantineSharedEntry(entry.id, 'Unsupported or invalid video URL'); report.quarantined++; report.failures++;
          report.errors.push(`${entry.id}: unsupported or invalid video URL`); continue;
        }
        // Acknowledge only AFTER SQLite commits; a crash between these is safely idempotent.
        await saveUrl(entry.url, entry.timestamp);
        await acknowledgeSharedEntry(entry.id); report.successes++;
      } catch (error) {
        report.failures++; report.errors.push(`${entry.id}: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
  } catch (error) { report.failures++; report.errors.push(error instanceof Error ? error.message : String(error)); }
  report.duration_ms = Date.now() - report.timestamp;
  const finalReport: DrainReport = await recordDrain(report) || report;
  if (finalReport.failures) throw new Error(`Share drain: ${finalReport.successes} saved, ${finalReport.failures} failed. ${finalReport.errors.join('; ')}`);
  return finalReport;
}
// This path runs before network/identity work, including a force-quit cold start.
export async function coldStartLibrary() { await drainContainer('cold_launch'); return loadLibrary(); }
let syncing: Promise<void> | null = null;
export function syncLibrary(trigger = 'sync'): Promise<void> {
  if (visualFixture) return Promise.resolve();
  if (!syncing) syncing = performSync(trigger).finally(() => { syncing = null; });
  return syncing;
}
async function performSync(trigger: string) {
  try {
    await drainContainer(trigger);
    await connectDevice();
    // Complete a pending reset before sending reels saved into the new library.
    // Otherwise those new reels would be accepted by the account about to be erased.
    for (const operation of (await loadLibrary()).outbox.filter(o => o.kind === 'delete_all')) {
      if (!operation.body?.serverDeleted) {
        await request(operation.path, operation.method);
        await update(state => { const pending = state.outbox.find(o => o.id === operation.id); if (pending) pending.body = { serverDeleted: true }; });
      }
      await resetIdentity();
      await connectDevice();
      await update(state => { state.outbox = state.outbox.filter(o => o.id !== operation.id); });
    }
    for (const save of (await loadLibrary()).saves.filter(s => s.local)) {
      const accepted = await request<Save>('/share', 'POST', { url: save.source_url });
      if (!accepted.id || !accepted.status) throw new Error('The server did not confirm the save. It remains queued here.');
      await update(state => { state.saves = state.saves.filter(s => s.id !== save.id && s.id !== accepted.id); state.saves.unshift({ ...accepted, local: false }); });
    }
    for (const pending of (await loadLibrary()).outbox) {
      const operation = (await loadLibrary()).outbox.find(o => o.id === pending.id);
      if (!operation) continue;
      try {
        const result = await request<{ id?: string }>(operation.path, operation.method, operation.body);
        if (operation.kind === 'source_info' && result?.id && result.id !== operation.body.entry_id) {
          const previousId = operation.body.entry_id, serverId = result.id;
          await update(state => { state.outbox.forEach(pending => { if (pending.target === previousId) { pending.target = serverId; pending.path = pending.path.replace('/items/' + previousId, '/items/' + serverId); } }); });
        }
        await update(state => { state.outbox = state.outbox.filter(o => o.id !== operation.id); });
      } catch (error) {
        if (!(error instanceof ApiError) || error.status >= 500) throw error;
        await update(state => { const pending = state.outbox.find(o => o.id === operation.id); if (pending) pending.error = error.message; });
      }
    }
    const remote = await request<Pick<Library, 'saves' | 'items' | 'folders' | 'apple_linked' | 'registry' | 'venue_kinds' | 'preferences'>>('/sync');
    if (!Array.isArray(remote.saves) || !Array.isArray(remote.items) || !Array.isArray(remote.folders)) throw new Error('Sync returned incomplete data. Your local library is safe.');
    const previous = await loadLibrary();
    await update(state => {
      if (state.outbox.some(o => o.kind === 'delete_all')) return;
      if (remote.registry) state.registry = remote.registry;
      if (remote.venue_kinds) state.venue_kinds = remote.venue_kinds;
      if (remote.preferences) state.preferences = { ...state.preferences, ...remote.preferences };
      state.saves = [...state.saves.filter(s => s.local), ...remote.saves]; state.items = remote.items; state.folders = remote.folders; state.apple_linked = remote.apple_linked;
      state.outbox.forEach(operation => applyOperation(state, operation));
      state.sync_error = state.outbox.find(o => o.error)?.error || null; state.last_synced = new Date().toISOString();
    });
    const completed = remote.saves.filter(save => ['resolved', 'needs_review'].includes(save.status) && previous.saves.some(old => old.id === save.id && ['queued', 'processing'].includes(old.status)));
    if (previous.preferences.notifications && completed.length) {
      try {
        const notifications = await import('expo-notifications');
        await notifications.scheduleNotificationAsync({ content: { title: 'Your reel is saved', body: 'Open ReelBot to see what you found.' }, trigger: null });
      } catch { /* Notification delivery never blocks saving or syncing. */ }
    }
  } catch (error) {
    await update(state => { state.sync_error = error instanceof Error ? error.message : 'Waiting for a connection. Your saves are on this device.'; });
  }
}
export async function retrySave(save: Save) {
  if (!save.local) await request('/saves/' + save.id + '/retry' + (save.is_compilation || save.status === 'extraction_empty' || save.status === 'partial_extraction' ? '?deeper=true' : ''), 'POST');
  await syncLibrary();
}
export async function discardOperation(id: string) { await update(state => { state.outbox = state.outbox.filter(o => o.id !== id); }); await syncLibrary(); }

export async function setPreference<K extends keyof Library['preferences']>(key: K, value: Library['preferences'][K]) {
  if (key === 'groupBy' || key === 'distanceUnits') {
    await queueOperation({ kind: 'preferences', method: 'PATCH', path: '/preferences', body: { [key]: value } });
    void syncLibrary();
  } else await update(state => { state.preferences[key] = value; });
}
export async function deleteAllData() {
  if (visualFixture) { fixtureLibrary = emptyLibrary(); listeners.forEach(callback => callback()); return; }
  if (syncing) await syncing;
  await queueOperation({ kind: 'delete_all', method: 'DELETE', path: '/account' });
  for (const entry of await readSharedQueue()) await acknowledgeSharedEntry(entry.id);
  await syncLibrary();
}
export async function storageBytes() {
  if (visualFixture) return 0;
  const connection = await db();
  const pages = await connection.getFirstAsync<{ page_count: number }>('PRAGMA page_count');
  const size = await connection.getFirstAsync<{ page_size: number }>('PRAGMA page_size');
  return (pages?.page_count || 0) * (size?.page_size || 0);
}
