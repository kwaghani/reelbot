declare module "react-native-shared-group-preferences" {
  const SharedGroupPreferences: {
    setItem(
      key: string,
      value: unknown,
      appGroupIdentifier: string,
      options?: Record<string, unknown>
    ): Promise<void>;
    getItem<T = unknown>(
      key: string,
      appGroupIdentifier: string,
      options?: Record<string, unknown>
    ): Promise<T>;
  };

  export default SharedGroupPreferences;
}
