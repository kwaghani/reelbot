import * as SQLite from 'expo-sqlite';
import * as Crypto from 'expo-crypto';
import { canonicalReelUrl } from './reelUrls';
import { request, connectDevice, ApiError } from './api';
import { emptyLibrary, upgradeLibrary, applyOperation, type Library, type Operation, type Save } from './libraryModel';
import { resetIdentity } from './identity';
import { readSharedQueue, acknowledgeSharedEntry } from './sharedGroup';
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
  const row = await (await db()).getFirstAsync<{ value: string }>('SELECT value FROM library WHERE id=1');
  return row ? upgradeLibrary(JSON.parse(row.value)) : emptyLibrary();
}
async function update(mutator: (state: Library) => void) {
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
  await update(state => { applyOperation(state, operation); state.outbox.push(operation); });
}
export async function drainContainer() {
  for (const entry of await readSharedQueue()) {
    // Acknowledge only AFTER SQLite commits; a crash between these is safely idempotent.
    await saveUrl(entry.url, entry.timestamp);
    await acknowledgeSharedEntry(entry.id);
  }
}
let syncing: Promise<void> | null = null;
export function syncLibrary(): Promise<void> {
  if (!syncing) syncing = performSync().finally(() => { syncing = null; });
  return syncing;
}
async function performSync() {
  try {
    await drainContainer();
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
    const remote = await request<Pick<Library, 'saves' | 'items' | 'folders' | 'apple_linked' | 'registry'>>('/sync');
    if (!Array.isArray(remote.saves) || !Array.isArray(remote.items) || !Array.isArray(remote.folders)) throw new Error('Sync returned incomplete data. Your local library is safe.');
    const previous = await loadLibrary();
    await update(state => {
      if (state.outbox.some(o => o.kind === 'delete_all')) return;
      if (remote.registry) state.registry = remote.registry;
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
  if (!save.local) await request('/saves/' + save.id + '/retry', 'POST');
  await syncLibrary();
}
export async function discardOperation(id: string) { await update(state => { state.outbox = state.outbox.filter(o => o.id !== id); }); await syncLibrary(); }

export async function setPreference(key: keyof Library['preferences'], value: boolean) {
  await update(state => { state.preferences[key] = value; });
}
export async function deleteAllData() {
  if (syncing) await syncing;
  await queueOperation({ kind: 'delete_all', method: 'DELETE', path: '/account' });
  for (const entry of await readSharedQueue()) await acknowledgeSharedEntry(entry.id);
  await syncLibrary();
}
export async function storageBytes() {
  const connection = await db();
  const pages = await connection.getFirstAsync<{ page_count: number }>('PRAGMA page_count');
  const size = await connection.getFirstAsync<{ page_size: number }>('PRAGMA page_size');
  return (pages?.page_count || 0) * (size?.page_size || 0);
}
