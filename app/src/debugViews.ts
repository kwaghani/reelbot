import { FeatureFlags } from './config';
import type { ComponentType } from 'react';
declare const require: { context(path: string, recursive: boolean, filter: RegExp): { keys(): string[]; (key: string): { default: ComponentType } } };
// Optional Debug views are discovered, so deleting an entire view directory is safe.
export function optionalDebugViews(): ComponentType[] {
  if (!FeatureFlags.groupsPlaceholder) return [];
  const views = require.context('./features', true, /index\.tsx$/);
  return views.keys().map(key => views(key).default);
}
