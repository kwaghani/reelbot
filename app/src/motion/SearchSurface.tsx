import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Keyboard, View, type StyleProp, type ViewStyle } from 'react-native';
import Animated, { cancelAnimation, runOnJS, useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated';
import { useReducedMotion } from '../theme';
import { reflow, timing, type OriginFrame } from '../theme/motion';
export function SearchSurface({ visible, origin, children, style }: { visible: boolean; origin?: OriginFrame | null; children: ReactNode; style?: StyleProp<ViewStyle> }) {
  const reduced = useReducedMotion(), [mounted, setMounted] = useState(visible), [frame, setFrame] = useState<OriginFrame | null>(null), view = useRef<View>(null), progress = useSharedValue(0);
  useEffect(() => { cancelAnimation(progress); if (visible) { setMounted(true); progress.value = withTiming(1, timing(reduced ? 'quick' : 'base')); }
    else { Keyboard.dismiss(); progress.value = withTiming(0, timing(reduced ? 'quick' : 'base', 'exit'), done => { if (done) runOnJS(setMounted)(false); }); }
  }, [visible, reduced]);
  const dx = frame && origin ? origin.x + origin.width / 2 - frame.x - frame.width / 2 : 0, dy = frame && origin ? origin.y + origin.height / 2 - frame.y - frame.height / 2 : 0;
  const animated = useAnimatedStyle(() => ({ opacity: progress.value, transform: [{ translateX: reduced ? 0 : dx * (1 - progress.value) }, { translateY: reduced ? 0 : dy * (1 - progress.value) }, { scaleX: reduced ? 1 : .12 + .88 * progress.value }, { scaleY: reduced ? 1 : .7 + .3 * progress.value }] }));
  return mounted ? <Animated.View layout={reflow(reduced)} ref={view} onLayout={() => view.current?.measureInWindow((x,y,width,height) => setFrame(old => old?.x === x && old?.y === y && old?.width === width ? old : { x,y,width,height }))} pointerEvents={visible ? 'auto' : 'none'} style={[style, animated]}>{children}</Animated.View> : null;
}
