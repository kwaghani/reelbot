import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { View, type StyleProp, type ViewStyle } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withTiming, type SharedValue } from 'react-native-reanimated';
import { useReducedMotion } from '../theme';
import { motion, timing } from '../theme/motion';
import type { Entry } from '../libraryModel';
type Position = { x: number; y: number };
export type GridPositions = Map<string, Position>;
// Hold a deleted cell for its exit before committing the new row arrangement.
// Filtering uses the surrounding crossfade and does not masquerade as deletion.
export function useGridEntries(items: Entry[], all: Entry[]) {
  const [rendered, setRendered] = useState(items), previous = useRef(items);
  const signature = items.map(e => [e.id, e.needs_review, e.title, e.image_choice].join(':')).join('|');
  useEffect(() => {
    const ids = new Set(all.map(e => e.id)), deleted = previous.current.filter(e => !ids.has(e.id));
    if (deleted.length) {
      const staged = items.slice(); deleted.forEach(entry => staged.splice(Math.min(previous.current.indexOf(entry), staged.length), 0, entry)); setRendered(staged);
    } else setRendered(items);
    previous.current = items;
    const timer = setTimeout(() => setRendered(items), motion.duration.quick);
    return () => clearTimeout(timer);
  }, [signature, all]);
  return rendered;
}
export function GridCard({ id, generation, positions, scrollY, leaving, children, style }: { id: string; generation: string; positions: GridPositions; scrollY: SharedValue<number>; leaving: boolean; children: ReactNode; style?: StyleProp<ViewStyle> }) {
  const reduced = useReducedMotion(), ref = useRef<View>(null), x = useSharedValue(0), y = useSharedValue(0), opacity = useSharedValue(1), scale = useSharedValue(1);
  useLayoutEffect(() => {
    let alive = true;
    ref.current?.measureInWindow((left, top, width, height) => {
      if (!alive || width <= 0 || height <= 0) return;
      const target = { x: left, y: top + scrollY.value }, old = positions.get(id); positions.set(id, target);
      if (old && !reduced) { x.value = old.x - target.x; y.value = old.y - target.y; x.value = withTiming(0, timing('base', 'move')); y.value = withTiming(0, timing('base', 'move')); }
    });
    return () => { alive = false; };
  }, [id, generation, reduced]);
  useEffect(() => { opacity.value = withTiming(leaving ? 0 : 1, timing('quick', leaving ? 'exit' : 'enter')); scale.value = withTiming(leaving && !reduced ? motion.scale.deletion : 1, timing('quick', leaving ? 'exit' : 'enter')); }, [leaving, reduced]);
  const animated = useAnimatedStyle(() => ({ opacity: opacity.value, transform: [{ translateX: reduced ? 0 : x.value }, { translateY: reduced ? 0 : y.value }, { scale: scale.value }] }));
  return <Animated.View ref={ref} collapsable={false} pointerEvents={leaving ? 'none' : 'auto'} style={[style, animated]}>{children}</Animated.View>;
}
