import { useEffect, useRef, useState, type ReactNode } from 'react';
import Animated, { useAnimatedStyle, useSharedValue, withSpring, withTiming } from 'react-native-reanimated';
import type { collisionClusters, Coordinate } from '../mapModel';
import { wrapLongitude } from '../mapModel';
import { useReducedMotion } from '../theme';
import { motion, timing } from '../theme/motion';
type Cluster = ReturnType<typeof collisionClusters>[number];
type Transition = Cluster & { leaving?: boolean; origin?: Coordinate };
export function useMarkerTransitions(markers: Cluster[]) {
  const [rows, setRows] = useState<Transition[]>(markers), last = useRef(markers);
  useEffect(() => {
    const old = last.current, ids = new Set(markers.map(marker => marker.id));
    const added = markers.map(marker => ({ ...marker, origin: old.find(parent => parent.id !== marker.id && marker.members.some(member => parent.members.some(row => row.id === member.id)))?.coordinate }));
    setRows([...added, ...old.filter(marker => !ids.has(marker.id)).map(marker => ({ ...marker, leaving: true }))]); last.current = markers;
    const timer = setTimeout(() => setRows(added), motion.duration.quick);
    return () => clearTimeout(timer);
  }, [markers]);
  return rows;
}
export function MovingMarker({ marker, region, width, height, children }: { marker: Transition; region: { latitudeDelta: number; longitudeDelta: number }; width: number; height: number; children: ReactNode }) {
  const reduced = useReducedMotion(), progress = useSharedValue(marker.origin ? 0 : 1), opacity = useSharedValue(0);
  useEffect(() => { progress.value = reduced ? 1 : withSpring(1, motion.spring); opacity.value = withTiming(marker.leaving ? 0 : 1, timing('quick', marker.leaving ? 'exit' : 'enter')); }, [marker.leaving]);
  const dx = marker.origin ? wrapLongitude(marker.origin.longitude - marker.coordinate.longitude) / region.longitudeDelta * width : 0;
  const dy = marker.origin ? (marker.coordinate.latitude - marker.origin.latitude) / region.latitudeDelta * height : 0;
  const animated = useAnimatedStyle(() => ({ opacity: opacity.value, transform: [{ scale: reduced ? 1 : marker.leaving ? motion.scale.deletion + (1 - motion.scale.deletion) * opacity.value : .9 + .1 * opacity.value }, { translateX: reduced ? 0 : dx * (1 - progress.value) }, { translateY: reduced ? 0 : dy * (1 - progress.value) }] }));
  return <Animated.View pointerEvents={marker.leaving ? 'none' : 'auto'} style={animated}>{children}</Animated.View>;
}
