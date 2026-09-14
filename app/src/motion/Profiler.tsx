import { NativeModules } from 'react-native';
import { motion } from '../theme/motion';
// Only the dedicated hardware profile build enables the native display-link sampler.
const enabled = NativeModules.ReelBotQueue?.motionProfile === true;
export function measureMotion(label: string, duration: number = motion.duration.page + motion.duration.base) {
  if (enabled) NativeModules.ReelBotQueue.beginMotionProfile(label, duration);
}
export function MotionProfiler() { return null; }
