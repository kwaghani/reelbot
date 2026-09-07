import { NativeModules, Platform } from 'react-native';
import SharedGroupPreferences from 'react-native-shared-group-preferences';
import { appConfig } from './config';
export type SharedEntry = { id: string; url: string; timestamp: number };
const bridge = NativeModules.ReelBotQueue;
// Preserve the App Group preference bridge for container metadata. The queue uses
// atomic individual files so concurrent extension launches cannot overwrite saves.
export async function writeContainerPreference(key: string, value: unknown) {
  await SharedGroupPreferences.setItem(key, value, appConfig.appGroupIdentifier);
}
export async function readContainerPreference<T>(key: string): Promise<T | null> {
  try { return await SharedGroupPreferences.getItem<T>(key, appConfig.appGroupIdentifier); } catch { return null; }
}
export async function enqueueSharedUrl(url: string) {
  if (!bridge) throw new Error('The save container is unavailable. Rebuild the iOS app.');
  return bridge.enqueue(url, Date.now());
}
export async function readSharedQueue(): Promise<SharedEntry[]> {
  if (Platform.OS !== 'ios') return [];
  if (!bridge) throw new Error('The save container is unavailable in this build.');
  return bridge.read();
}
export async function acknowledgeSharedEntry(id: string) { await bridge.acknowledge(id); }
