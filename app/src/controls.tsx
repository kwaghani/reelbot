import { forwardRef, useEffect, useRef, useState } from 'react';
import { Text as NativeText, TextInput as NativeTextInput, Pressable as NativePressable, View, StyleSheet, type TextProps, type TextInputProps, type PressableProps } from 'react-native';
import { fonts, useTheme, useReducedMotion } from './theme';
import Animated, { cancelAnimation, useAnimatedStyle, useSharedValue, withSpring, withTiming } from 'react-native-reanimated';
import { motion, timing, type OriginFrame } from './theme/motion';
export const Text = forwardRef<NativeText, TextProps>((props, ref) => {
  const c = useTheme(), style = StyleSheet.flatten(props.style) || {};
  const family = style.fontFamily || (Number(style.fontWeight || 400) >= 500 ? fonts.semibold : fonts.regular);
  return <NativeText {...props} ref={ref} style={[{ color: c.textPrimary, fontFamily: family, fontVariant: ['tabular-nums'] }, props.style, { fontFamily: family, fontWeight: 'normal' }]} />;
});
export const TextInput = forwardRef<NativeTextInput, TextInputProps>((props, ref) => {
  const c = useTheme(); return <NativeTextInput placeholderTextColor={c.textSecondary} selectionColor={c.accent} {...props} ref={ref} style={[{ fontFamily: fonts.regular, color: c.textPrimary }, props.style]} />;
});
const AnimatedPressable = Animated.createAnimatedComponent(NativePressable);
type MotionPressableProps = PressableProps & { destructive?: boolean; pressScale?: number; onPressFrame?: (frame: OriginFrame) => void };
export function Pressable({ children, style, onFocus, onBlur, onPressIn, onPressOut, onPress, disabled, destructive, pressScale = motion.scale.press, onPressFrame, onLayout, ...props }: MotionPressableProps) {
  const [focused, setFocused] = useState(false), [touchInset, setTouchInset] = useState({ horizontal: 8, vertical: 8 }), c = useTheme(), reduced = useReducedMotion();
  const scale = useSharedValue(1), opacity = useSharedValue(disabled ? 0.4 : 1), native = useRef<View>(null);
  useEffect(() => { cancelAnimation(scale); cancelAnimation(opacity); scale.value = 1; opacity.value = disabled ? 0.4 : 1; }, [disabled, reduced]);
  const animated = useAnimatedStyle(() => ({ opacity: opacity.value, transform: [{ scale: reduced ? 1 : scale.value }] }));
  const pressIn: PressableProps['onPressIn'] = event => {
    if (disabled) return;
    scale.value = withTiming(reduced ? 1 : pressScale, timing('instant'));
    opacity.value = withTiming(0.9, timing(reduced ? 'quick' : 'instant')); onPressIn?.(event);
  };
  const pressOut: PressableProps['onPressOut'] = event => {
    if (disabled) return;
    scale.value = reduced ? 1 : withSpring(1, destructive ? motion.destructiveSpring : motion.spring);
    opacity.value = withTiming(1, timing(reduced ? 'quick' : 'instant')); onPressOut?.(event);
  };
  const resolvedStyle = typeof style === 'function' ? (state: any) => [style(state), animated] : [style, animated];
  return <AnimatedPressable {...props} ref={native as any} disabled={disabled} hitSlop={props.hitSlop ?? { left: touchInset.horizontal, right: touchInset.horizontal, top: touchInset.vertical, bottom: touchInset.vertical }} onLayout={event => { const {width,height} = event.nativeEvent.layout; const horizontal = Math.max(8, (44-width)/2), vertical = Math.max(8, (44-height)/2); setTouchInset(old => old.horizontal === horizontal && old.vertical === vertical ? old : { horizontal, vertical }); onLayout?.(event); }} onPressIn={pressIn} onPressOut={pressOut}
    onPress={event => { if (onPressFrame) native.current?.measureInWindow((x,y,width,height) => onPressFrame({x,y,width,height})); else onPress?.(event); }}
    onFocus={event => { setFocused(true); onFocus?.(event); }} onBlur={event => { setFocused(false); onBlur?.(event); }} style={resolvedStyle}>{state => <>{typeof children === 'function' ? children(state) : children}{focused ? <View pointerEvents="none" style={[StyleSheet.absoluteFill, { borderWidth: 2, borderColor: c.accent }]} /> : null}</>}</AnimatedPressable>;
}
