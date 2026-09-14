import { useEffect, useLayoutEffect, useRef, useState, cloneElement, isValidElement, type ReactNode } from 'react';
import { Image, StyleSheet, type ImageProps, type StyleProp, type ViewStyle, View } from 'react-native';
import Animated, { cancelAnimation, runOnJS, withSequence, useAnimatedStyle, useSharedValue, withDelay, withSpring, withTiming } from 'react-native-reanimated';
import { useReducedMotion, useTheme } from '../theme';
import { motion, timing, reflow, type MotionDuration } from '../theme/motion';

export function Reveal({ children, delay = 0, rise = motion.distance.card, token = 'quick', style, animate = true }: { children: ReactNode; delay?: number; rise?: number; token?: MotionDuration; style?: StyleProp<ViewStyle>; animate?: boolean }) {
  const reduced = useReducedMotion(), progress = useSharedValue(animate ? 0 : 1);
  useEffect(() => { progress.value = withDelay(reduced ? 0 : delay, withTiming(1, timing(reduced ? 'quick' : token))); return () => cancelAnimation(progress); }, [reduced]);
  const animated = useAnimatedStyle(() => ({ opacity: progress.value, transform: [{ translateY: reduced ? 0 : rise * (1 - progress.value) }] }));
  return <Animated.View style={[style, animated]}>{children}</Animated.View>;
}
export function ChangeFade({ children, changeKey, style, token = 'quick', outgoingProps }: { children: ReactNode; changeKey: string; style?: StyleProp<ViewStyle>; token?: MotionDuration; outgoingProps?: Record<string, unknown> }) {
  const progress = useSharedValue(1), last = useRef({ key: changeKey, children }), [previous, setPrevious] = useState<ReactNode>(null);
  useLayoutEffect(() => {
    if (last.current.key !== changeKey) {
      cancelAnimation(progress); setPrevious(isValidElement(last.current.children) ? cloneElement(last.current.children as any, { ref: null, ...outgoingProps }) : last.current.children); progress.value = 0;
      progress.value = withTiming(1, timing(token), done => { if (done) runOnJS(setPrevious)(null); });
    }
    last.current = { key: changeKey, children };
  }, [changeKey, children]);
  const incoming = useAnimatedStyle(() => ({ opacity: progress.value })), outgoing = useAnimatedStyle(() => ({ opacity: 1 - progress.value }));
  return <View style={style}><Animated.View style={[StyleSheet.flatten(style)?.flex ? { flex: 1 } : undefined, incoming]}>{children}</Animated.View>{previous ? <Animated.View pointerEvents="none" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={[{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }, outgoing]}>{previous}</Animated.View> : null}</View>;
}
export function Flow({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  const reduced = useReducedMotion(); return <Animated.View layout={reflow(reduced)} style={style}>{children}</Animated.View>;
}
const AnimatedImage = Animated.createAnimatedComponent(Image);
export function FadeImage({ onLoad, onError, style, ...props }: ImageProps) {
  const opacity = useSharedValue(0);
  const uri = JSON.stringify(props.source);
  useEffect(() => { opacity.value = 0; }, [uri]);
  const animated = useAnimatedStyle(() => ({ opacity: opacity.value }));
  return <AnimatedImage {...props} style={[style, animated]} onLoad={event => { opacity.value = withTiming(1, timing('quick')); onLoad?.(event); }} onError={event => { opacity.value = 0; onError?.(event); }} />;
}
export function SelectionIcon({ active, children }: { active: boolean; children: ReactNode }) {
  const reduced = useReducedMotion(), scale = useSharedValue(1), previous = useRef(active);
  useEffect(() => { if (active && !previous.current && !reduced) { scale.value = withSequence(withTiming(motion.scale.tab, timing('instant')), withSpring(1, motion.spring)); } previous.current = active; }, [active, reduced]);
  const style = useAnimatedStyle(() => ({ transform: [{ scale: reduced ? 1 : scale.value }] }));
  return <Animated.View style={style}>{children}</Animated.View>;
}

export function Skeleton() {
  const c = useTheme();
  return <Reveal rise={0}><View accessibilityLabel="Loading saved entries" style={{ flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', gap: 14 }}>{Array.from({ length: 4 }, (_, index) => <View key={index} style={{ width: '48.3%', backgroundColor: c.card, borderRadius: 3, overflow: 'hidden' }}><View style={{ aspectRatio: 1.18, backgroundColor: c.cardMuted }} /><View style={{ padding: 11, gap: 7 }}><View style={{ height: 21, backgroundColor: c.cardMuted }} /><View style={{ height: 17, width: '60%', backgroundColor: c.cardMuted }} /></View></View>)}</View></Reveal>;
}
