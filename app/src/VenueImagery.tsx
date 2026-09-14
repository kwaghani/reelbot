import * as FileSystem from 'expo-file-system/legacy';
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import NetInfo from '@react-native-community/netinfo';
import { AppState, NativeModules, Image, Linking, ScrollView, StyleSheet, View, useWindowDimensions } from 'react-native';
import { Pressable, Text } from './controls';
import { request } from './api';
import { visualFixture } from './config';
import { VenueKindIcon, useVenueKinds } from './VenueKindIcon';
import { entryKind } from './venueModel';
import { fonts, useTheme } from './theme';
import Animated, { useSharedValue, useAnimatedStyle, withTiming, runOnJS } from 'react-native-reanimated';
import { timing } from './theme/motion';
import type { Entry } from './libraryModel';
import { mergeImageryRecords, allowedImages, chosenImage, nextImageChoice, type ImageryResult, type VenueImage } from './imageryModel';

type Context = { entries: Entry[]; records: Record<string, ImageryResult>; load: (entry: Entry, context: string, gallery?: boolean) => void; retry: (entry: Entry) => Promise<void>; failed: (entry: Entry, uri: string, fallback?: VenueImage | null) => void };
const Images = createContext<Context>({ entries: [], records: {}, load: () => {}, retry: async () => {}, failed: () => {} });
const viewportListeners = new Set<() => void>(); let lastViewport = 0;
export function imageryViewportChanged() { if (Date.now() - lastViewport < 120) return; lastViewport = Date.now(); viewportListeners.forEach(fn => fn()); }
const entryVersion = (entry: Entry) => [entry.place_id, entry.save_id, entry.save_entry_count, entry.thumbnail, entry.venue_kind, entry.image_acquired_at].join('|');
const cacheKey = (entry: Entry, context: string) => context + ':' + entry.id;
export function VenueImageryProvider({ entries, children }: { entries: Entry[]; children: ReactNode }) {
  const [records, setRecords] = useState<Record<string, ImageryResult>>({});
  const entriesRef = useRef(entries); entriesRef.current = entries;
  const recordRef = useRef(records); recordRef.current = records;
  const queue = useRef(new Map<string, { entry: Entry; context: string; gallery: boolean }>());
  const pending = useRef(new Set<string>()), attempted = useRef(new Set<string>()), generation = useRef(0), running = useRef(false);
  const cooldown = useRef(new Map<string,number>()), failures = useRef(new Map<string,number>()), retryTimers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  function load(entry: Entry, context: string, gallery = false) {
    const current = entriesRef.current.find(row => row.id === entry.id);
    if (!current) return;
    entry = current;
    if (visualFixture || entry.content_type !== 'place' || Date.now() < (cooldown.current.get(cacheKey(entry,context)) || 0)) return;
    const cached = recordRef.current[cacheKey(entry, context)];
    if (cached && cached.place_id === (entry.place_id || null) && cached.save_id === entry.save_id && (!gallery || cached.gallery.length >= cached.choices.length) && (entry.image_choice === 'auto' || !entry.image_choice || cached.gallery.some(image => image.key === entry.image_choice))) return;
    const key = cacheKey(entry, context), attempt = key + ':' + entryVersion(entry) + ':' + (entry.image_choice || 'auto') + ':' + gallery;
    if (attempted.current.has(attempt) || pending.current.has(attempt)) return;
    const previous = queue.current.get(key); queue.current.set(key, { entry, context, gallery: gallery || !!previous?.gallery });
    if (!running.current && !timer.current) timer.current = setTimeout(() => { timer.current = null; void drain(); }, 60);
  }
  async function drain() {
    if (running.current) return; running.current = true;
    try {
      while (queue.current.size) {
        for (const [key,row] of queue.current) {
          const current=entriesRef.current.find(entry=>entry.id===row.entry.id);
          if (!current) queue.current.delete(key); else row.entry=current;
        }
        if (!queue.current.size) break;
        const first = queue.current.values().next().value!;
        const group = [...queue.current.entries()].filter(([, row]) => row.context === first.context && row.gallery === first.gallery).sort((a,b) => (a[1].entry.place_id || a[1].entry.id).localeCompare(b[1].entry.place_id || b[1].entry.id)).slice(0,8);
        const epoch = generation.current;
        const attempts = group.map(([key,row]) => key + ':' + entryVersion(row.entry) + ':' + (row.entry.image_choice || 'auto') + ':' + row.gallery);
        group.forEach(([key]) => queue.current.delete(key)); attempts.forEach(a => { pending.current.add(a); attempted.current.add(a); });
        try {
          const response = await request<{ items: Record<string, ImageryResult> }>('/imagery/resolve', 'POST', { entry_ids: group.map(([,row]) => row.entry.id), gallery: first.gallery, context: first.context }, 65000);
          if (generation.current === epoch) setRecords(old => {
            const incoming = Object.fromEntries(Object.entries(response.items).map(([id,result]) => [first.context + ':' + id, result]));
            const next = mergeImageryRecords(old, incoming, entriesRef.current);
            for (const key of Object.keys(old)) if (!next[key]) for (const attempt of attempted.current) if (attempt.startsWith(key + ':')) attempted.current.delete(attempt);
            return next;
          });
        } catch (error) {
          console.warn('Venue image request failed', error instanceof Error ? error.message : String(error));
          attempts.forEach(a => attempted.current.delete(a));
          for (const [,row] of group) {
            const key=cacheKey(row.entry,row.context), count=(failures.current.get(key)||0)+1; failures.current.set(key,count); cooldown.current.set(key,count>3?Infinity:Date.now()+[2000,8000,30000][count-1]-10);
            if(count<=3){const retryTimer=setTimeout(()=>{retryTimers.current.delete(retryTimer);if(generation.current===epoch)load(row.entry,row.context,row.gallery);},[2000,8000,30000][count-1]);retryTimers.current.add(retryTimer);}
          }
        }
        finally { attempts.forEach(a => pending.current.delete(a)); }
      }
    } finally { running.current = false; }
  }
  async function retry(entry: Entry) {
    await request('/items/'+entry.id+'/image/retry','POST');
    for(const attempt of attempted.current)if(attempt.includes(':'+entry.id+':'))attempted.current.delete(attempt);
    setRecords(old=>Object.fromEntries(Object.entries(old).filter(([key])=>!key.endsWith(':'+entry.id))));
    recordRef.current=Object.fromEntries(Object.entries(recordRef.current).filter(([key])=>!key.endsWith(':'+entry.id)));
    failures.current.clear();cooldown.current.clear();load(entry,'library',true);imageryViewportChanged();
  }
  function failed(entry: Entry, uri: string, fallback?: VenueImage | null) {
    console.warn('Venue image decoder rejected image',entry.id);
    setRecords(old=>Object.fromEntries(Object.entries(old).map(([key,value])=>[key,{...value,gallery:[...value.gallery.filter(image=>image.uri!==uri),...(fallback && fallback.uri!==uri && key.endsWith(':'+entry.id) && value.place_id===(entry.place_id || null) ? [fallback] : [])]}])));
  }
  useEffect(() => {
    let wasConnected=true;
    const network=NetInfo.addEventListener(state=>{const connected=!!state.isConnected;if(connected&&!wasConnected){attempted.current.clear();failures.current.clear();cooldown.current.clear();imageryViewportChanged();}wasConnected=connected;});
    const listener = AppState.addEventListener('change', state => {
      if (state !== 'active') { generation.current++; queue.current.clear(); recordRef.current = {}; setRecords({}); attempted.current.clear(); }
      else imageryViewportChanged();
    });
    return () => { network();retryTimers.current.forEach(clearTimeout);retryTimers.current.clear();listener.remove(); generation.current++; if (timer.current) clearTimeout(timer.current); timer.current = null; queue.current.clear(); };
  }, []);
  useEffect(() => {
    if(!entries.length)mapImages.clear();
    const ids = new Set(entries.map(e => e.id));
    setRecords(old => Object.fromEntries(Object.entries(old).filter(([key]) => ids.has(key.slice(key.indexOf(':') + 1)))));
  }, [entries.map(e => e.id).join(',')]);
  return <Images.Provider value={{ entries, records, load, retry, failed }}>{children}</Images.Provider>;
}

async function mapPreview(entry:Entry,color:string):Promise<string|null>{
 if(!visualFixture)try{const cached=await request<{uri:string|null}>('/items/'+entry.id+'/map-thumbnail','GET',undefined,4000);if(cached.uri)return cached.uri;}catch{/* The native cache can still work offline. */}
 try{
  const uri=await NativeModules.ReelBotQueue.mapThumbnail(entry.place_id,entry.lat,entry.lng,color);
  if(!visualFixture)void FileSystem.readAsStringAsync(uri,{encoding:FileSystem.EncodingType.Base64}).then(jpeg=>request('/items/'+entry.id+'/map-thumbnail','POST',{jpeg})).catch(error=>console.warn('Map preview cache upload failed',entry.id,error instanceof Error?error.message:String(error)));
  return uri;
 }catch{return null;}
}
const mapImages = new Map<string, Promise<string | null>>();
function Placeholder({ entry }: { entry: Entry }) {
  const kinds = useVenueKinds(), kind = entryKind(entry), [uri,setUri]=useState<string|null>(null);
  const color=(kinds[kind] || kinds.other)?.color || '#536059';
  useEffect(()=>{let active=true;setUri(null);
    if((!visualFixture || entry.id==='fixture1') && entry.place_id && entry.lat!=null && entry.lng!=null && NativeModules.ReelBotQueue?.mapThumbnail){
      const key=[entry.place_id,entry.lat,entry.lng,color].join(':');
      if(!mapImages.has(key)){if(mapImages.size>=128)mapImages.delete(mapImages.keys().next().value!);mapImages.set(key,mapPreview(entry,color));}
      void mapImages.get(key)!.then(value=>{if(active)setUri(value);});
    }return()=>{active=false;};
  },[entry.place_id,entry.lat,entry.lng,color]);
  return <View style={[StyleSheet.absoluteFill, { backgroundColor: color, alignItems: 'center', justifyContent: 'center', padding:10 }]}>{uri?<Image accessibilityLabel={'Map of '+entry.title} source={{uri}} resizeMode="cover" style={StyleSheet.absoluteFill}/>:<Text numberOfLines={3} adjustsFontSizeToFit minimumFontScale={.65} style={{fontFamily:fonts.display,fontSize:19,color:'#FFFFFF',textAlign:'center'}}>{entry.place_name || entry.title}</Text>}</View>;
}
export function RetryVenueImage({entry}:{entry:Entry}){
 const {retry}=useContext(Images),c=useTheme(),[pending,setPending]=useState(false),[message,setMessage]=useState('');
 return <View style={{gap:8}}><Pressable accessibilityRole="button" disabled={pending} onPress={()=>{setPending(true);setMessage('');void retry(entry).then(()=>setMessage('Image refresh requested.')).catch(()=>setMessage('Could not reach the server. Try again when connected.')).finally(()=>setPending(false));}}><Text style={{color:c.accent,fontSize:15,paddingVertical:12}}>{pending?'Retrying image…':'Retry image'}</Text></Pressable>{message?<Text style={{color:c.textSecondary}}>{message}</Text>:null}{__DEV__?<Text style={{color:c.textSecondary,fontSize:12}}>{entry.image_source || 'No photo'} · {entry.image_acquired_at || 'Not acquired'}{entry.image_failure_reason?' · '+entry.image_failure_reason:''}</Text>:null}</View>;
}

function Photo({ entry, image, onDisplayed }: { entry: Entry; image: VenueImage | null; onDisplayed?: (image: VenueImage | null) => void }) {
  const {failed}=useContext(Images);
  const [displayed, setDisplayed] = useState<VenueImage | null>(null), progress = useSharedValue(0), previous = useRef<VenueImage | null>(null), currentUri = useRef(image?.uri); currentUri.current = image?.uri;
  // Keep the last successful image beneath its replacement until decoding finishes.
  // A failed replacement leaves both the old photo and its attribution in place.
  const animated = useAnimatedStyle(() => ({ opacity: progress.value }));
  useEffect(() => { progress.value = 0; if (!image) { setDisplayed(null); previous.current = null; onDisplayed?.(null); } }, [image?.uri]);
  return <><Placeholder entry={entry} />{displayed ? <Image source={{ uri: displayed.uri, cache: displayed.source === 'google' ? 'reload' : 'default' }} resizeMode="cover" style={StyleSheet.absoluteFill} /> : null}{image ? <Animated.Image key={image.uri} accessible accessibilityRole="image" accessibilityValue={{text:displayed?.uri===image.uri ? 'Loaded' : 'Loading'}} accessibilityLabel={'Photo of ' + entry.title} source={{ uri: image.uri, cache: image.source === 'google' ? 'reload' : 'default' }} resizeMode="cover" onError={() => failed(entry,image.uri,previous.current)} onLoad={() => {
    if (currentUri.current !== image.uri) return;
    progress.value = withTiming(1, timing(previous.current ? 'base' : 'quick'), done => { if (done) runOnJS(setDisplayed)(image); });
    onDisplayed?.(image); previous.current = image;
    // Delay replacing the underlay so it remains visible through the crossfade.
  }} style={[StyleSheet.absoluteFill, animated]} /> : null}</>;
}
export function VenueThumbnail({ entry, context = 'library' }: { entry: Entry; context?: string }) {
  const { entries, records, load } = useContext(Images), ref = useRef<View>(null), { height } = useWindowDimensions();
  const current = useRef(() => {});
  current.current = () => { ref.current?.measureInWindow((_x,y,_width,h) => { if (h > 0 && y < height && y+h > 0) load(entry, context); }); };
  useEffect(() => { const check = () => current.current(); viewportListeners.add(check); check(); return () => { viewportListeners.delete(check); }; }, [entry.id, entry.image_choice, entryVersion(entry)]);
  return <View ref={ref} collapsable={false} onLayout={() => current.current()} style={StyleSheet.absoluteFill}><Photo entry={entry} image={chosenImage(records[cacheKey(entry,context)],entry,entries,context)} /></View>;
}
function link(url?: string) { if (url && /^https?:\/\//i.test(url)) void Linking.openURL(url); }
function Attribution({ image }: { image: VenueImage }) {
  const c = useTheme(), a = image.attribution;
  return <View style={{ gap: 4 }}><Pressable accessibilityRole="link" onPress={() => link(a.url)}><Text style={{ color: c.textSecondary, fontSize: 12 }}>{a.label}</Text></Pressable>{a.authors?.map((author,index) => <Pressable key={index} accessibilityRole="link" onPress={() => link(author.uri?.startsWith('//') ? 'https:' + author.uri : author.uri)}><View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>{author.photoUri ? <Image source={{ uri: author.photoUri, cache: 'reload' }} style={{ width: 20, height: 20, borderRadius: 10 }} /> : null}<Text style={{ color: c.textSecondary, fontSize: 12 }}>{author.displayName}</Text></View></Pressable>)}{a.source_url ? <Pressable accessibilityRole="link" onPress={() => link(a.source_url)}><Text style={{ color: c.textSecondary, fontSize: 12 }}>View source photo</Text></Pressable> : null}{a.license_url ? <Pressable accessibilityRole="link" onPress={() => link(a.license_url)}><Text style={{ color: c.textSecondary, fontSize: 12 }}>{image.license}</Text></Pressable> : null}</View>;
}
export function VenueGallery({ entry, context, choose }: { entry: Entry; context: string; choose: (key: string) => void }) {
  const { entries, records, load } = useContext(Images), c = useTheme();
  const [displayedImage, setDisplayedImage] = useState<VenueImage | null>(null);
  const result = records[cacheKey(entry,context)], image = chosenImage(result,entry,entries,context), images = allowedImages(result,entry,entries,context);
  useEffect(() => { const refresh = () => load(entry,context,true); refresh(); viewportListeners.add(refresh); return () => { viewportListeners.delete(refresh); }; }, [entry.id, entry.image_choice, context, entryVersion(entry)]);
  const choices = result ? { ...result, choices: result.choices.filter(choice => (choice.source !== 'cover' || entries.filter(row => row.save_id === entry.save_id).length <= 1 && (entry.save_entry_count || 1) <= 1) && (context !== 'map' || choice.source !== 'google')) } : undefined;
  return <View style={{ gap: 8 }}><View style={{ height: 190, borderRadius: 3, overflow: 'hidden' }}><Photo entry={entry} image={image} onDisplayed={setDisplayedImage} /></View>{displayedImage ? <Attribution image={displayedImage} /> : null}{choices && choices.choices.length > 1 ? <Pressable accessibilityRole="button" accessibilityLabel="Change photo" onPress={() => choose(nextImageChoice(choices,image?.key || ''))} style={{ minHeight: 44, justifyContent: 'center' }}><Text style={{ color: c.accent, fontSize: 13, fontWeight: '600' }}>Change photo</Text></Pressable> : null}{images.length > 1 ? <ScrollView horizontal snapToInterval={120} decelerationRate="fast" disableIntervalMomentum showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }}>{images.map(photo => <Pressable key={photo.key} accessibilityRole="button" accessibilityLabel={'Use ' + (photo.source === 'cover' ? 'reel cover' : photo.source + ' photo')} onPress={() => choose(photo.key)} style={{ width: 112 }}><View style={{ height: 84, overflow: 'hidden' }}><Photo entry={entry} image={photo} /></View></Pressable>)}</ScrollView> : null}{entry.image_choice && entry.image_choice !== 'auto' ? <Pressable accessibilityRole="button" onPress={() => choose('auto')}><Text style={{ color: c.textSecondary, fontSize: 12 }}>Choose automatically</Text></Pressable> : null}</View>;
}
