import { createContext, useContext } from 'react';
import { useLocales } from 'expo-localization';
import { distanceSystem, formatDistance } from './venueModel';
export const DistancePreference = createContext('auto');
export function useDistance() {
  const locales = useLocales(), override = useContext(DistancePreference);
  const system = distanceSystem(locales[0]?.regionCode, override);
  return { system, label: (km: number) => formatDistance(km, system), factor: system === 'imperial' ? 1.609344 : 1, unit: system === 'imperial' ? 'mi' : 'km' };
}
