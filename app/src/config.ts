import { NativeModules } from 'react-native';
import Constants from 'expo-constants';
const extra = Constants.expoConfig?.extra ?? {};
export const appConfig = {
  apiUrl: String(extra.apiUrl || '').trim().replace(/\/+$/, ''),
  appGroupIdentifier: String(extra.appGroupIdentifier || 'group.com.krishwaghani.reelbot'),
};
export const FeatureFlags = { groupsPlaceholder: __DEV__ && NativeModules.ReelBotQueue?.debugFeaturesEnabled === true } as const;
