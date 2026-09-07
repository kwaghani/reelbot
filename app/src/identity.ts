import AsyncStorage from "@react-native-async-storage/async-storage";
import { registerDevice, setApiContext } from "./api";
import { appConfig } from "./config";

// Server-issued identity: an old public device ID can never claim a session.
const SESSION_KEY = `reelbot.session.v1.${appConfig.apiUrl}`;
let pending: Promise<string> | null = null;

export function getDeviceId(): Promise<string> {
  if (!pending) {
    pending = (async () => {
      const stored = await AsyncStorage.getItem(SESSION_KEY);
      const session = stored ? JSON.parse(stored) as { device_id: string; token: string } : await registerDevice();
      if (!session.device_id || !session.token) throw new Error("Device setup is incomplete. Please try again.");
      if (!stored) await AsyncStorage.setItem(SESSION_KEY, JSON.stringify(session));
      setApiContext({ deviceId: session.device_id, token: session.token });
      return session.device_id;
    })().catch((error) => { pending = null; throw error; });
  }
  return pending;
}

export async function resetDeviceSession(): Promise<void> {
  await AsyncStorage.removeItem(SESSION_KEY);
  pending = null;
  setApiContext({ deviceId: "", token: "", groupId: "" });
}
