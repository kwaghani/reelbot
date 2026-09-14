import { NativeModules } from 'react-native';
import Constants from 'expo-constants';
const extra = Constants.expoConfig?.extra ?? {};
export const appConfig = {
  apiUrl: String(extra.apiUrl || '').trim().replace(/\/+$/, ''),
  contentTypes: extra.contentTypes || {},
  venueKinds: extra.venueKinds || {},
  appGroupIdentifier: String(extra.appGroupIdentifier || 'group.com.krishwaghani.reelbot'),
};
export const FeatureFlags = { groupsPlaceholder: __DEV__ && NativeModules.ReelBotQueue?.debugFeaturesEnabled === true } as const;

export const visualFixture = (__DEV__ || NativeModules.ReelBotQueue?.motionProfile === true) && NativeModules.ReelBotQueue?.visualFixture === true;
export const fixtureCount = Number(NativeModules.ReelBotQueue?.fixtureCount || 50);
export const fixtureGrayscale = visualFixture && NativeModules.ReelBotQueue?.fixtureGrayscale === true;
