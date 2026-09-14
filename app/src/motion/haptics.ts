import * as Haptics from 'expo-haptics';
// The OS decides whether to deliver feedback (including disabled system haptics).
export function feedback(event: 'selection' | 'snap' | 'success' | 'error') {
  const task = event === 'selection' ? Haptics.selectionAsync() : event === 'snap' ? Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium) : Haptics.notificationAsync(event === 'success' ? Haptics.NotificationFeedbackType.Success : Haptics.NotificationFeedbackType.Error);
  void task.catch(() => {});
}
