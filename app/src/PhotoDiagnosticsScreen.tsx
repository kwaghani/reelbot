import { useState } from 'react';
import { ScrollView, View } from 'react-native';
import { Text } from './controls';
import { Button, useUI } from './ui';
import { request } from './api';
import type { Entry } from './libraryModel';
export function PhotoDiagnosticsScreen({entries,close}:{entries:Entry[];close:()=>void}){
 const s=useUI(),[details,setDetails]=useState<Record<string,any>>({}),[error,setError]=useState('');
 return <ScrollView contentContainerStyle={[s.body,{gap:16}]}><Button title="Back" secondary onPress={close}/><Text style={s.title}>Image diagnostics</Text>{error?<Text style={s.text}>{error}</Text>:null}{entries.filter(e=>e.content_type==='place').map(entry=>{const d=details[entry.id];return <View key={entry.id} style={s.card}><Text style={s.heading}>{entry.title}</Text><Text style={s.text}>Source: {d?.source || entry.image_source || 'No venue photo'}</Text><Text style={s.small}>Acquired: {d?.acquired_at || entry.image_acquired_at || 'Not acquired'}</Text><Text style={s.small}>Failure: {d?.failure_reason || entry.image_failure_reason || 'None recorded'}</Text><Button title="Inspect photo steps" secondary onPress={()=>void request('/items/'+entry.id+'/image/diagnostics').then(value=>setDetails(old=>({...old,[entry.id]:value}))).catch(()=>setError('Could not reach the server. Your saved entries are still here.'))}/>{d?.steps?.map((step:any,index:number)=><Text key={index} style={s.small}>{[step.source,step.outcome,step.reason].filter(Boolean).join(' · ')}</Text>)}</View>;})}</ScrollView>;
}
