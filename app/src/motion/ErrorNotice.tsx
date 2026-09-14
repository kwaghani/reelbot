import { createContext, useEffect } from 'react';
import Animated, { useSharedValue, useAnimatedStyle, withSpring, withTiming } from 'react-native-reanimated';
import { useReducedMotion, useTheme } from '../theme';
import { motion, timing } from '../theme/motion';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Text, Pressable } from '../controls';
export const ActionFeedback = createContext({ message: '', dismiss: () => {} });
export function ErrorNotice({ message, dismiss }: { message: string; dismiss: () => void }) {
  const insets = useSafeAreaInsets(), c = useTheme(), reduced = useReducedMotion(), y = useSharedValue(-motion.distance.card), opacity = useSharedValue(0);
  useEffect(() => { y.value = reduced ? 0 : withSpring(0, motion.spring); opacity.value = withTiming(1, timing('quick')); }, [message, reduced]);
  const animated = useAnimatedStyle(() => ({ opacity: opacity.value, transform: [{ translateY: reduced ? 0 : y.value }] }));
  return <Animated.View accessibilityRole="alert" accessibilityLiveRegion="assertive" style={[{ position: 'absolute', top: insets.top, left: 20, right: 20, padding: 12, backgroundColor: c.dangerSoft, borderColor: c.danger, borderWidth: 1, zIndex: 20, flexDirection: 'row', alignItems: 'center', gap: 8 }, animated]}><Text style={{ color: c.danger, flex: 1 }}>{message}</Text><Pressable onPress={dismiss} accessibilityRole="button" accessibilityLabel="Dismiss error"><Text>Close</Text></Pressable></Animated.View>;
}
