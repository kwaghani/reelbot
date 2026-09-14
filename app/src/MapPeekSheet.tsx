import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Linking, StyleSheet, View } from 'react-native';
import BottomSheet, { BottomSheetScrollView, useBottomSheetSpringConfigs, type BottomSheetBackdropProps } from '@gorhom/bottom-sheet';
import Animated, { Extrapolation, interpolate, ReduceMotion, useAnimatedStyle } from 'react-native-reanimated';
import { Pressable, Text } from './controls';
import { useTheme, useReducedMotion } from './theme';
import { useUI } from './ui';
import { VenueThumbnail } from './VenueImagery';
import { VenueKindIcon, useVenueKinds } from './VenueKindIcon';
import { entryKind } from './venueModel';
import { ChangeFade } from './motion/Motion';
import { feedback } from './motion/haptics';
import { measureMotion } from './motion/Profiler';
import { useDistance } from './useDistance';
import type { Entry } from './libraryModel';
// Keep map markers touchable at every snap; background taps are handled by MapScreen.
function MapBackdrop({animatedIndex}:BottomSheetBackdropProps){
 const style=useAnimatedStyle(()=>({opacity:interpolate(animatedIndex.value,[0,1],[0,.18],Extrapolation.CLAMP)}));
 return <Animated.View pointerEvents="none" accessible={false} style={[StyleSheet.absoluteFill,{backgroundColor:'#000'},style]}/>;
}
export function MapPeekSheet({entry,height,distanceKm,collapseRequest,onClose,onSnap,renderDetail}:{entry:Entry;height:number;distanceKm?:number|null;collapseRequest:number;onClose:()=>void;onSnap:(index:number)=>void;renderDetail:(entry:Entry,close:()=>void)=>ReactNode}){
 const ref=useRef<BottomSheet>(null),c=useTheme(),s=useUI(),reduced=useReducedMotion(),kinds=useVenueKinds(),distance=useDistance();
 const [index,setIndex]=useState(0),[expanded,setExpanded]=useState(false),lastIndex=useRef(0);
 const snaps=useMemo(()=>[190,Math.max(210,height*.55),Math.max(240,height*.92)],[height]);
 const config=useBottomSheetSpringConfigs({damping:20,stiffness:240,mass:1,overshootClamping:true});
 const backdrop=useCallback((props:BottomSheetBackdropProps)=><MapBackdrop {...props}/>,[]);
 useEffect(()=>{if(collapseRequest>0)ref.current?.snapToIndex(0);},[collapseRequest]);
 const kind=entryKind(entry);
 return <BottomSheet accessible={false} ref={ref} index={0} snapPoints={snaps} containerHeight={height} handleComponent={()=><View onTouchStart={()=>measureMotion('map-peek-drag',2000)} accessible accessibilityRole="adjustable" accessibilityLabel="Map sheet handle" accessibilityValue={{text:['Peek','Medium','Full'][index] || 'Closed'}} accessibilityActions={[{name:'increment'},{name:'decrement'}]} onAccessibilityAction={event=>event.nativeEvent.actionName==='increment'?ref.current?.snapToIndex(Math.min(2,index+1)):index===0?ref.current?.close():ref.current?.snapToIndex(index-1)} style={{height:28,alignItems:'center',justifyContent:'center'}}><View style={{width:36,height:4,borderRadius:2,backgroundColor:c.border}}/></View>} enableDynamicSizing={false} enablePanDownToClose animateOnMount={!reduced} overrideReduceMotion={reduced?ReduceMotion.Always:ReduceMotion.Never} animationConfigs={config} backgroundStyle={{backgroundColor:c.card}} handleIndicatorStyle={{backgroundColor:c.border,width:36}} backdropComponent={backdrop} onAnimate={(_,to)=>{if(to>0)setExpanded(true);measureMotion('map-peek-snap');}} onChange={next=>{if(next!==lastIndex.current){feedback('selection');lastIndex.current=next;}setIndex(next);onSnap(next);if(next>0)setExpanded(true);if(next<0)onClose();}}>
  <BottomSheetScrollView scrollEnabled={index>0} onTouchStart={()=>measureMotion('map-peek-drag',2000)} contentContainerStyle={{paddingHorizontal:18,paddingBottom:30,gap:16}}>
   <ChangeFade changeKey={`${entry.id}:${index>0 ? 'details' : 'peek'}`} token="quick"><View style={{gap:14}}>
    <Pressable accessibilityRole="button" accessibilityLabel={'Expand '+entry.title} onPress={()=>ref.current?.snapToIndex(1)} style={{flexDirection:'row',gap:12,alignItems:'center',minHeight:68}}>
     <View style={{width:64,height:64,borderRadius:4,overflow:'hidden'}}><VenueThumbnail entry={entry} context="map"/></View>
     <View style={{flex:1,gap:5}}><Text numberOfLines={1} style={s.heading}>{entry.place_name || entry.title}</Text><View style={{flexDirection:'row',alignItems:'center',gap:5}}>{index===0 ? <VenueKindIcon kind={kind} size={18}/> : null}<Text numberOfLines={1} style={[s.small,{flex:1}]}>{[kinds[kind]?.label,entry.attributes.neighborhood,distanceKm!=null?distance.label(distanceKm):null].filter(Boolean).join(' · ')}</Text></View></View>
    </Pressable>
    <View style={{flexDirection:'row',gap:12}}><Pressable accessibilityRole="button" accessibilityLabel="Directions" onPress={()=>void Linking.openURL(`https://maps.apple.com/?daddr=${entry.lat},${entry.lng}&dirflg=w`)} style={[s.card,{flex:1,padding:10,alignItems:'center'}]}><Text style={s.strong}>Directions</Text></Pressable><Pressable accessibilityRole="button" accessibilityLabel="Open place" onPress={()=>ref.current?.snapToIndex(2)} style={[s.card,{flex:1,padding:10,alignItems:'center'}]}><Text style={s.strong}>Open</Text></Pressable></View>
    {expanded && index>0 ? renderDetail(entry,onClose) : null}
   </View></ChangeFade>
  </BottomSheetScrollView>
 </BottomSheet>;
}
