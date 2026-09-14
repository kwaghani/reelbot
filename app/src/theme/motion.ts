import { Easing, ReduceMotion, withTiming, type LayoutAnimationFunction } from 'react-native-reanimated';

export const motion = {
  reducedPosition: 0,
  duration: { instant: 100, quick: 180, base: 260, page: 320, slow: 450 },
  delay: { grid: 25, attribute: 20, folder: 60, search: 350 },
  cap: { grid: 8, attribute: 6 },
  distance: { tab: 4, card: 8 },
  scale: { press: 0.97, chip: 0.96, tab: 1.08, marker: 1.15, deletion: 0.9 },
  spring: { damping: 18, stiffness: 220, mass: 1 },
  destructiveSpring: { damping: 22, stiffness: 280, mass: 1 },
  easing: { enter: Easing.bezier(0.16, 1, 0.3, 1), exit: Easing.bezier(0.7, 0, 0.84, 0), move: Easing.bezier(0.65, 0, 0.35, 1) },
} as const;
export type MotionDuration = keyof typeof motion.duration;
export function timing(token: MotionDuration = 'quick', direction: 'enter' | 'exit' | 'move' = 'enter') {
  'worklet';
  return { duration: Math.round(motion.duration[token] * (direction === 'exit' ? 0.75 : 1)), easing: motion.easing[direction], reduceMotion: ReduceMotion.Never };
}
// FLIP: commit the final layout once, then move pixels back from the previous frame.
// No animated width/height/origin/margin/padding values are returned.
export function reflow(reduced: boolean): LayoutAnimationFunction {
  return values => {
    'worklet';
    return {
      initialValues: { opacity: reduced ? 0 : 1, transform: [{ translateX: reduced ? 0 : values.currentOriginX - values.targetOriginX }, { translateY: reduced ? 0 : values.currentOriginY - values.targetOriginY }] },
      animations: { opacity: withTiming(1, timing('quick')), transform: [{ translateX: withTiming(0, timing('base', 'move')) }, { translateY: withTiming(0, timing('base', 'move')) }] },
    };
  };
}
export type OriginFrame = { x: number; y: number; width: number; height: number };
export function disappear(reduced: boolean) {
  return () => {
    'worklet';
    return { initialValues: { opacity: 1, transform: [{ scale: 1 }, { translateY: 0 }] }, animations: { opacity: withTiming(0, timing('quick', 'exit')), transform: [{ scale: withTiming(reduced ? 1 : motion.scale.deletion, timing('quick', 'exit')) }, { translateY: withTiming(reduced ? 0 : -motion.distance.tab, timing('quick', 'exit')) }] } };
  };
}
