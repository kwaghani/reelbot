import * as Crypto from 'expo-crypto';
import * as SecureStore from 'expo-secure-store';
const KEY = 'reelbot.personal.identity.v1';
export type Identity = { device_id: string; token: string };
let pending: Promise<Identity> | null = null;
export function getIdentity(): Promise<Identity> {
  if (!pending) pending = (async () => {
    const raw = await SecureStore.getItemAsync(KEY);
    if (raw) return JSON.parse(raw) as Identity;
    const bytes = await Crypto.getRandomBytesAsync(32);
    const identity = { device_id: Crypto.randomUUID(), token: Array.from(bytes, v => v.toString(16).padStart(2, '0')).join('') };
    await SecureStore.setItemAsync(KEY, JSON.stringify(identity), { keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY });
    return identity;
  })().catch(error => { pending = null; throw error; });
  return pending;
}
