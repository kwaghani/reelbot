import { useEffect, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
const KEY = 'reelbot.groupsPlaceholder.interestTapped';
export default function GroupsPlaceholder() {
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => { AsyncStorage.getItem(KEY).then(value => setConfirmed(value === 'true')); }, []);
  async function tap() {
    await AsyncStorage.setItem(KEY, 'true');
    setConfirmed(true);
    console.info('groups_placeholder_interest_tapped');
  }
  return <View style={{ padding: 24, gap: 16 }}><Text style={{ fontSize: 36 }}>◌</Text><Text style={{ fontSize: 28, fontWeight: '700' }}>Groups</Text><Text>A future way to discover places together.</Text><Pressable accessibilityRole="button" disabled={confirmed} onPress={tap}><Text>{confirmed ? 'Interest noted — thank you' : 'Notify me when this ships'}</Text></Pressable></View>;
}
