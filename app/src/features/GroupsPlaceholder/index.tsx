import { useEffect, useState } from 'react';
import { View } from 'react-native';
import { Pressable, Text } from '../../controls';
import { useUI } from '../../ui';
import AsyncStorage from '@react-native-async-storage/async-storage';
const KEY = 'reelbot.groupsPlaceholder.interestTapped';
export default function GroupsPlaceholder() {
  const s = useUI();
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => { AsyncStorage.getItem(KEY).then(value => setConfirmed(value === 'true')); }, []);
  async function tap() {
    await AsyncStorage.setItem(KEY, 'true');
    setConfirmed(true);
    console.info('groups_placeholder_interest_tapped');
  }
  return <View style={{ padding: 24, gap: 16 }}><Text style={{ fontSize: 36 }}>◌</Text><Text style={s.heading}>Groups</Text><Text style={s.text}>This preview only records interest on this device.</Text><Pressable accessibilityRole="button" disabled={confirmed} onPress={tap}><Text style={s.link}>{confirmed ? 'Interest saved' : 'Register interest'}</Text></Pressable></View>;
}
