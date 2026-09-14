import { useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { Modal, StyleSheet, View, useWindowDimensions } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import BottomSheet, { BottomSheetScrollView } from '@gorhom/bottom-sheet';
import Animated, { cancelAnimation, ReduceMotion, runOnJS, useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Text, Pressable } from '../controls';
import { useReducedMotion, useTheme, fonts } from '../theme';
import { motion, timing, type OriginFrame } from '../theme/motion';
import { ActionFeedback, ErrorNotice } from './ErrorNotice';
import { measureMotion } from './Profiler';
import { feedback } from './haptics';

export function MotionSheet({ visible, title, onClose, children, origin }: { visible: boolean; title: string; onClose: () => void; children: ReactNode; origin?: OriginFrame | null }) {
  const errors = useContext(ActionFeedback), c = useTheme(), reduced = useReducedMotion(), insets = useSafeAreaInsets(), { width, height } = useWindowDimensions();
  const [presented, setPresented] = useState(false), progress = useSharedValue(0), sheet = useRef<BottomSheet>(null);
  const retained = useRef({ children, title, origin }); if (visible) retained.current = { children, title, origin };
  const callback = useRef(onClose); callback.current = onClose;
  const finish = useCallback(() => { setPresented(false); callback.current(); }, []);
  const dismiss = useCallback(() => { measureMotion('sheet-dismiss'); cancelAnimation(progress); progress.value = withTiming(0, timing(reduced ? 'quick' : origin ? 'page' : 'base', 'exit'), done => { if (done) runOnJS(finish)(); }); }, [reduced, origin, finish]);
  useEffect(() => {
    if (visible) { cancelAnimation(progress); setPresented(true); progress.value = withTiming(1, timing(reduced ? 'quick' : origin ? 'page' : 'base')); }
    else if (presented) dismiss();
  }, [visible, reduced]);
  const frame = retained.current.origin, originX = frame ? frame.x + frame.width / 2 - width / 2 : 0, originY = frame ? frame.y + frame.height / 2 - height / 2 : 0;
  const minimumScale = frame ? Math.min(0.85, Math.max(0.1, frame.width / width)) : 1;
  const surface = useAnimatedStyle(() => ({ opacity: progress.value, transform: [{ translateX: reduced ? 0 : originX * (1 - progress.value) }, { translateY: reduced ? 0 : frame ? originY * (1 - progress.value) : height * 0.12 * (1 - progress.value) }, { scale: reduced ? 1 : minimumScale + (1 - minimumScale) * progress.value }] }));
  const backdrop = useAnimatedStyle(() => ({ opacity: progress.value * 0.28 }));
  return <Modal visible={presented} transparent animationType="none" onRequestClose={dismiss} statusBarTranslucent>
    <GestureHandlerRootView style={{ flex: 1 }}>
      <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: '#000' }, backdrop]} />
      <Animated.View style={[{ flex: 1 }, surface]}>
        <BottomSheet accessible={false} ref={sheet} index={1} snapPoints={[height * 0.52, height - insets.top - 10]} animateOnMount={false} enableDynamicSizing={false} enablePanDownToClose
          overrideReduceMotion={reduced ? ReduceMotion.Always : ReduceMotion.Never} animationConfigs={reduced ? timing('quick') : motion.spring}
          onAnimate={(_from, to) => { measureMotion('sheet-snap'); if (to === -1) feedback('snap'); }} onChange={index => { if (index === -1) finish(); else if (index === 0) feedback('snap'); }}
          backgroundStyle={{ backgroundColor: c.background, borderRadius: 18 }} handleIndicatorStyle={{ backgroundColor: c.borderStrong }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, padding: 22 }}><Text style={{ fontFamily: fonts.display, fontSize: 23, color: c.textPrimary, flex: 1 }}>{retained.current.title}</Text><Pressable accessibilityRole="button" accessibilityLabel="Close" onPress={dismiss} style={{ width: 44, height: 44, justifyContent: 'center', alignItems: 'center' }}><Text style={{ fontSize: 30 }}>×</Text></Pressable></View>
          <BottomSheetScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingHorizontal: 22, paddingBottom: 32 + insets.bottom, gap: 18 }}>{visible ? children : retained.current.children}</BottomSheetScrollView>
        </BottomSheet>
      </Animated.View>
      {errors.message ? <ErrorNotice message={errors.message} dismiss={errors.dismiss} /> : null}
    </GestureHandlerRootView>
  </Modal>;
}
