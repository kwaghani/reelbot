import { useEffect, useState } from 'react';
import { NativeModules, Pressable, Text, View } from 'react-native';
import { close, type InitialProps } from 'expo-share-extension';
import { reelUrls } from './reelUrls';
// Development fallback only. The shipped extension is a small native queue writer.
export default function ShareExtension(props: InitialProps) {
  const [error, setError] = useState('');
  useEffect(() => {
    const url = reelUrls([props.url, props.text].filter(Boolean).join(' '))[0];
    if (!url) { setError('No supported video link found.'); return; }
    NativeModules.ReelBotQueue.enqueue(url, Date.now()).then(close).catch(() => setError('Could not save the link. Please try again.'));
  }, [props.url, props.text]);
  return <View style={{ padding: 24 }}><Text>{error || 'Saving link…'}</Text>{error ? <Pressable accessibilityRole="button" onPress={close}><Text>Close</Text></Pressable> : null}</View>;
}
