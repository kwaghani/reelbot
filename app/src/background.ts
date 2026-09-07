import * as TaskManager from 'expo-task-manager';
import * as BackgroundTask from 'expo-background-task';
import { syncLibrary } from './library';
const TASK = 'reelbot.personal.refresh';
TaskManager.defineTask(TASK, async () => {
  try { await syncLibrary(); return BackgroundTask.BackgroundTaskResult.Success; }
  catch { return BackgroundTask.BackgroundTaskResult.Failed; }
});
export async function registerRefresh() {
  if (await BackgroundTask.getStatusAsync() === BackgroundTask.BackgroundTaskStatus.Available && !await TaskManager.isTaskRegisteredAsync(TASK)) {
    await BackgroundTask.registerTaskAsync(TASK, { minimumInterval: 15 });
  }
}
