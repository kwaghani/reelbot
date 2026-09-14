import { createContext, useContext, useEffect, useRef, type ReactNode } from 'react';
import { StyleSheet, View } from 'react-native';
import { NavigationContainer, NavigationIndependentTree, useFocusEffect } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import Animated, { useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated';
import { useReducedMotion, useTheme } from '../theme';
import { motion, timing } from '../theme/motion';
import { RecentScreen, type RecentProps } from '../RecentScreen';
import { SettingsScreen, type SettingsProps } from '../SettingsScreen';
import { ShareDiagnosticsScreen } from '../ShareDiagnosticsScreen';

const Stack = createNativeStackNavigator();
const RecentContext = createContext<RecentProps | null>(null), SettingsContext = createContext<SettingsProps | null>(null);
import { ScrollRequest as ScrollTopContext } from './scrollTop';
export function TabScene({ active, scrollRequest, children }: { active: boolean; scrollRequest: number; children: ReactNode }) {
  const visited = useRef(active), reduced = useReducedMotion(), progress = useSharedValue(active ? 1 : 0);
  if (active) visited.current = true;
  useEffect(() => { progress.value = withTiming(active ? 1 : 0, timing('quick')); }, [active]);
  const animated = useAnimatedStyle(() => ({ opacity: progress.value, transform: [{ translateY: reduced ? 0 : (1 - progress.value) * motion.distance.tab }] }));
  return <Animated.View pointerEvents={active ? 'auto' : 'none'} accessibilityElementsHidden={!active} importantForAccessibility={active ? 'auto' : 'no-hide-descendants'} style={[StyleSheet.absoluteFill, { zIndex: active ? 1 : 0 }, animated]}><ScrollTopContext.Provider value={scrollRequest}>{visited.current ? children : null}</ScrollTopContext.Provider></Animated.View>;
}
function RecentRoute({ route, navigation }: any) {
  const props = useContext(RecentContext)!;
  const folderId = route.params?.folderId || null, browsing = !!route.params?.browsing;
  useEffect(() => navigation.addListener('focus', () => { props.onFolder(folderId); props.onBrowse(browsing); }), [navigation, folderId, browsing]);
  return <RecentScreen {...props} folderId={folderId} browsing={browsing} focused={navigation.isFocused()} navigateFolder={id => { props.onFolder(id); props.onBrowse(!id); navigation.push('Folder', { folderId: id, browsing: !id }); }} goBack={() => navigation.goBack()} />;
}
export function RecentNavigator(props: RecentProps) {
  const c = useTheme(), reduced = useReducedMotion();
  return <RecentContext.Provider value={props}><NavigationIndependentTree><NavigationContainer><Stack.Navigator screenOptions={{ headerShown: false, contentStyle: { backgroundColor: c.background }, animation: reduced ? 'fade' : 'simple_push', animationDuration: reduced ? motion.duration.quick : motion.duration.page, gestureEnabled: true, fullScreenGestureEnabled: true, animationMatchesGesture: true }}><Stack.Screen name="Saved" component={RecentRoute} /><Stack.Screen name="Folder" component={RecentRoute} /></Stack.Navigator></NavigationContainer></NavigationIndependentTree></RecentContext.Provider>;
}
function SettingsRoute({ navigation }: any) {
  const props = useContext(SettingsContext)!;
  return <SettingsScreen {...props} openDiagnostics={() => navigation.navigate('Share diagnostics')} />;
}
function DiagnosticsRoute({ navigation }: any) { return <ShareDiagnosticsScreen close={() => navigation.goBack()} />; }
export function SettingsNavigator(props: SettingsProps) {
  const c = useTheme(), reduced = useReducedMotion();
  return <SettingsContext.Provider value={props}><NavigationIndependentTree><NavigationContainer><Stack.Navigator screenOptions={{ headerShown: false, contentStyle: { backgroundColor: c.background }, animation: reduced ? 'fade' : 'simple_push', animationDuration: reduced ? motion.duration.quick : motion.duration.page, gestureEnabled: true, fullScreenGestureEnabled: true }}><Stack.Screen name="Settings home" component={SettingsRoute} /><Stack.Screen name="Share diagnostics" component={DiagnosticsRoute} /></Stack.Navigator></NavigationContainer></NavigationIndependentTree></SettingsContext.Provider>;
}
