const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript'),crypto=require('node:crypto');
const {DatabaseSync}=require('node:sqlite');
function load(name,mocks,globals={}) {
 const exports={};const source=ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src',name+'.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 vm.runInNewContext(source,{exports,require:id=>{if(!(id in mocks))throw Error('Unexpected import '+id);return mocks[id]},URL,URLSearchParams,AbortController,Error,TypeError,setTimeout,clearTimeout,console,...globals});return exports;
}
const urls=load('reelUrls',{}),model=load('libraryModel',{});
function environment(){
 const connection=new DatabaseSync(':memory:');let chain=Promise.resolve(),queue=[],failAck=false;
 const sqlite={execAsync:async sql=>connection.exec(sql),runAsync:async(sql,...args)=>connection.prepare(sql).run(...args),getFirstAsync:async(sql,...args)=>connection.prepare(sql).get(...args),withExclusiveTransactionAsync:fn=>{const run=chain.then(async()=>{connection.exec('BEGIN IMMEDIATE');try{await fn(sqlite);connection.exec('COMMIT')}catch(e){connection.exec('ROLLBACK');throw e}});chain=run.catch(()=>{});return run}};
 class ApiError extends Error{}
 const mocks={'./config':{visualFixture:false},'expo-sqlite':{openDatabaseAsync:async()=>sqlite},'expo-crypto':{randomUUID:crypto.randomUUID},'./reelUrls':urls,'./libraryModel':model,'./api':{ApiError,connectDevice:async()=>{throw Error('offline')},request:async()=>{throw Error('offline')}},'./identity':{resetIdentity:async()=>{}},'./sharedGroup':{recordDrain:async()=>{},quarantineSharedEntry:async id=>{queue=queue.filter(e=>e.id!==id)},readSharedQueue:async()=>queue,acknowledgeSharedEntry:async id=>{if(failAck)throw Error('interrupted');queue=queue.filter(e=>e.id!==id)}}};
 return {load:()=>load('library',mocks),mocks,setQueue:value=>{queue=value},getQueue:()=>queue,failAck:value=>{failAck=value},close:()=>connection.close()};
}
test('offline first launch persists a reel without identity or network and survives runtime restart',async()=>{
 const env=environment(),lib=env.load();await lib.saveUrl('https://instagram.com/reel/ABC/?igsh=tracking');await lib.syncLibrary();const state=await env.load().loadLibrary();assert.equal(state.saves.length,1);assert.equal(state.saves[0].status,'queued');assert.equal(state.saves[0].local,true);assert.equal(state.sync_error,'offline');env.close();
});
test('50 concurrent local shares and URL variants are lossless and idempotent',async()=>{
 const env=environment(),lib=env.load();await Promise.all(Array.from({length:50},(_,i)=>lib.saveUrl('https://instagram.com/reel/'+i+'/?igsh='+i)));await lib.saveUrl('https://www.instagram.com/reel/12/');assert.equal((await lib.loadLibrary()).saves.length,50);env.close();
});
test('interruption after durable write and before queue acknowledgement neither loses nor duplicates a share',async()=>{
 const env=environment();env.setQueue([{id:'q1',url:'https://youtu.be/abcdefghijk',timestamp:1}]);env.failAck(true);let lib=env.load();await assert.rejects(lib.drainContainer());assert.equal((await lib.loadLibrary()).saves.length,1);assert.equal(env.getQueue().length,1);env.failAck(false);lib=env.load();await lib.drainContainer();assert.equal((await lib.loadLibrary()).saves.length,1);assert.equal(env.getQueue().length,0);env.close();
});
test('offline folders survive sync failure, and local search matches name, city, note, folder',async()=>{
 const env=environment(),lib=env.load();await lib.queueOperation({kind:'folder_create',method:'POST',path:'/folders',body:{id:'f',name:'Weekend'}});await lib.syncLibrary();const state=await env.load().loadLibrary();assert.equal(state.folders[0].name,'Weekend');assert.equal(state.outbox.length,1);env.close();
 const rows=[{name:'Tartine',city:'San Francisco',note:'morning bun',folders:[{name:'Weekend'}]}];for(const q of ['Tartine','Francisco','morning bun','Weekend'])assert.equal(model.searchLocal(rows,q).length,1);
});
test('invalid URLs cannot enter the durable queue',async()=>{
 const env=environment();for(const url of ['https://evil.test/video/123','https://instagram.com/username/','https://instagram.com.evil.test/reel/A/'])await assert.rejects(env.load().saveUrl(url));assert.equal((await env.load().loadLibrary()).saves.length,0);env.close();
});
test('API preserves authorization errors and rejects malformed success responses',async()=>{
 const mocks={'./identity':{getIdentity:async()=>({device_id:'x',token:'secret'})},'./config':{appConfig:{apiUrl:'https://example.invalid'}}};const api=load('api',mocks,{fetch:async()=>({ok:false,status:401,json:async()=>({detail:'Unauthorized'})})});await assert.rejects(api.request('/items'),e=>e.status===401);
 const bad=load('api',mocks,{fetch:async()=>({ok:true,status:200,json:async()=>{throw Error('bad JSON')}})});await assert.rejects(bad.request('/items'),/unreadable/);
});
test('Release cannot expose optional Debug views even if native flag is wrong',()=>{
 const cfg=load('config',{'expo-constants':{default:{expoConfig:{extra:{}}}},'react-native':{NativeModules:{ReelBotQueue:{debugFeaturesEnabled:true}}}},{__DEV__:false});assert.equal(cfg.FeatureFlags.groupsPlaceholder,false);assert.equal(load('debugViews',{'./config':cfg}).optionalDebugViews().length,0);
});
test('reels saved after an offline reset reach the new account after deletion',async()=>{
 const env=environment(),lib=env.load();await lib.deleteAllData();await lib.saveUrl('https://youtu.be/newreel1234');
 const calls=[];env.mocks['./api'].connectDevice=async()=>{};env.mocks['./identity'].resetIdentity=async()=>{calls.push('identity')};
 env.mocks['./api'].request=async(path)=>{calls.push(path);if(path==='/share')return{id:'accepted',status:'queued',source_url:'https://www.youtube.com/watch?v=newreel1234'};if(path==='/sync')return{items:[],folders:[],saves:[{id:'accepted',status:'queued'}],registry:{},apple_linked:false};return{ok:true}};
 await lib.syncLibrary();assert.deepEqual(calls,['/account','identity','/share','/sync']);assert.equal((await lib.loadLibrary()).saves[0].id,'accepted');env.close();
});
test('reset interrupted after server deletion resumes identity rotation without deleting a new account',async()=>{
 const env=environment(),lib=env.load();await lib.deleteAllData();let deletions=0;
 env.mocks['./api'].connectDevice=async()=>{};env.mocks['./api'].request=async(path)=>{if(path==='/account')deletions++;return{items:[],saves:[],folders:[],registry:{},apple_linked:false}};
 env.mocks['./identity'].resetIdentity=async()=>{throw Error('interrupted')};await lib.syncLibrary();
 assert.equal((await lib.loadLibrary()).outbox[0].body.serverDeleted,true);env.mocks['./identity'].resetIdentity=async()=>{};
 await env.load().syncLibrary();assert.equal(deletions,1);assert.equal((await lib.loadLibrary()).outbox.length,0);env.close();
});
test('offline review dismissal, edits and notes preserve their different review semantics',()=>{
 const state=model.emptyLibrary();state.registry={recipe:{geo:'never',attributes:{cuisine:{type:'string',required:true}}}};
 state.items=[{id:'one',content_type:'recipe',title:'Noodles',attributes:{cuisine:null},needs_review:true,review_reason:'missing_required:cuisine',folders:[]}];
 model.applyOperation(state,{kind:'note',target:'one',body:{note:'try Saturday'}});assert.equal(state.items[0].needs_review,true);
 model.applyOperation(state,{kind:'entry_edit',target:'one',body:{attributes:{cuisine:'Thai'}}});assert.equal(state.items[0].needs_review,false);assert.ok(state.items[0].verified_at);
 state.items[0].needs_review=true;model.applyOperation(state,{kind:'dismiss_review',target:'one'});assert.equal(state.items[0].needs_review,false);assert.ok(state.items[0].verified_at);
 const restored=model.upgradeLibrary(JSON.parse(JSON.stringify(state)));assert.equal(restored.items[0].needs_review,false);
});
test('map excludes unanchored/invalid entries and sorts distances across the date line',()=>{
 const map=load('mapModel',{}),entry=(id,lat,lng,place_id=id)=>({id,title:id,lat,lng,place_id});
 const rows=[entry('near',0,179.95),entry('far',0,-179.8),entry('none',0,179.9,null),entry('invalid',91,0)];
 assert.equal(map.anchored(rows).length,2);assert.ok(Math.abs(map.distanceKm({latitude:0,longitude:179.9},{latitude:0,longitude:-179.9})-22.239)<.01);
 assert.equal(map.nearby(rows,{latitude:0,longitude:179.9},10).length,1);assert.equal(map.nearby(rows,{latitude:0,longitude:179.9})[0].entry.id,'near');
 assert.ok(map.bounds(rows).longitudeDelta<1);assert.equal(map.clusters([entry('a',1,1),entry('b',1.001,1.001)],1).length,1);assert.equal(map.clusters([entry('a',1,1),entry('b',1.001,1.001)],.01).length,2);
});
test('registry additions support local search and multi-facet values without category switches',()=>{
 const entry={title:'Paper crane',summary:'Fold a crane',content_type:'craft',attributes:{material:['paper','card']},folders:[{name:'Crafts'}],place_name:'Studio'};
 for(const q of ['crane','Fold','paper','Crafts','Studio'])assert.equal(model.searchLocal([entry],q).length,1);
 assert.equal(model.facetValues(entry,{craft:{primary_facet:'material'}}).join(','),'Paper,Card');
});
test('shared URL is preserved while local variants remain idempotent',async()=>{
 const env=environment(),lib=env.load(),original='https://instagram.com/p/ABC/?igsh=original';await lib.saveUrl(original);await lib.saveUrl('https://instagram.com/reel/ABC/');const state=await lib.loadLibrary();assert.equal(state.saves.length,1);assert.equal(state.saves[0].source_url,original);env.close();
 for(const link of ['https://instagram.com/share/reel/token','https://instagram.com/share/p/token','https://instagr.am/ABC/'])assert.ok(urls.canonicalReelUrl(link));
});
test('blocked and deleted saves never offer inappropriate retry or no-content copy',()=>{
 assert.equal(model.canRetrySave({status:'fetch_blocked',retry_at:'2099-01-01'}),false);assert.equal(model.canRetrySave({status:'fetch_not_found'}),false);
 assert.equal(model.canAddSourceInfo({status:'fetch_blocked',retry_at:'2099-01-01'}),false);assert.equal(model.canAddSourceInfo({status:'fetch_blocked',retry_at:null}),true);
 assert.equal(model.canAddSourceInfo({status:'needs_source_info'}),true);assert.equal(model.reviewQuestion({review_reason:'ambiguous_place'}),'Is this the right place?');
 assert.equal(model.canAddSourceInfo({status:'needs_source_info'}),true);assert.equal(model.reviewQuestion({review_reason:'low_confidence;ambiguous_place'}),'Is this the right place?');
});
test('manual source entry and later note survive offline restart and server ID reconciliation',async()=>{
 const env=environment(),lib=env.load();await lib.queueOperation({kind:'source_info',target:'saved',method:'POST',path:'/saves/saved/source-info',body:{entry_id:'local-entry',title:'Noodles',content_type:'recipe'}});
 await lib.queueOperation({kind:'note',target:'local-entry',method:'PATCH',path:'/items/local-entry',body:{note:'Try this weekend'}});await lib.syncLibrary();let state=await env.load().loadLibrary();assert.equal(state.items[0].note,'Try this weekend');assert.equal(state.outbox.length,2);
 const paths=[];env.mocks['./api'].connectDevice=async()=>{};env.mocks['./api'].request=async(path)=>{paths.push(path);if(path.endsWith('/source-info'))return{id:'server-entry'};if(path==='/sync')return{items:[],saves:[],folders:[],registry:{},apple_linked:false};return{ok:true}};
 await env.load().syncLibrary();assert.ok(paths.includes('/items/server-entry'));assert.equal((await lib.loadLibrary()).outbox.length,0);env.close();
});

const organization=load('organizationModel',{'./libraryModel':model,'./venueModel':load('venueModel',{})});
const demoRegistry={place:{label:'Place',plural_label:'Places',primary_facet:'city'},recipe:{label:'Recipe',plural_label:'Recipes',primary_facet:'cuisine'},workout:{label:'Workout',plural_label:'Workouts',primary_facet:'muscle_group'}};
const demoEntry=(i,type='place')=>({id:String(i),created_at:new Date(2026,0,30-i).toISOString(),content_type:type,title:'Entry '+i,attributes:{cuisine:'Thai',neighborhood:i%2?'Venice Beach':'Silver Lake'},city:'Venice Beach',organization_city:'Los Angeles',note:'',folders:[{id:'places'}],needs_review:false});
test('library tiers show no machinery at zero, flat four, and shortcuts at twenty-five',()=>{
 for(const [count,tier,chips] of [[0,'empty',false],[4,'small',false],[25,'large',true]]){const view=organization.libraryProjection(Array.from({length:count},(_,i)=>demoEntry(i)),[],demoRegistry);assert.equal(view.tier,tier);assert.equal(view.showTypeChips,chips);assert.equal(view.items.length,count)}
 assert.equal(organization.entryCount(0),'0 entries');assert.equal(organization.entryCount(1),'1 entry');assert.equal(organization.entryCount(2),'2 entries');
});
test('one projection deduplicates counts, omits empty types and clears stale filters',()=>{
 const entries=[demoEntry(0),demoEntry(1,'recipe'),demoEntry(2,'recipe')];
 const view=organization.libraryProjection([...entries,entries[0]],[],demoRegistry,{type:'workout'});
 assert.equal(view.all.length,3);assert.equal(view.folderCounts.places,3);assert.equal(view.type,null);assert.equal(view.typeCounts.workout,undefined);
 for(const type of Object.keys(view.typeCounts))assert.ok(organization.libraryProjection(entries,[],demoRegistry,{type}).items.length>0);
 assert.equal(organization.libraryProjection(entries,[],demoRegistry,{type:'place',query:'Entry 1'}).items.length,1);
});
test('folder context uses geographic facets and review clears when its queue empties',()=>{
 const entries=[demoEntry(0),{...demoEntry(1),needs_review:true}];const folders=[{id:'places',name:'Los Angeles',kind:'auto_facet',content_type:'place'}];
 let view=organization.libraryProjection(entries,folders,demoRegistry,{folderId:'places',type:'recipe'});
 assert.equal(view.showTypeChips,false);assert.equal(view.type,null);assert.equal(view.facetCounts['Venice Beach'],1);assert.equal(view.facetCounts['Silver Lake'],1);
 assert.equal(organization.libraryProjection(entries,folders,demoRegistry,{folderId:'places',facet:'Venice Beach'}).items.length,1);
 view=organization.libraryProjection(entries,folders,demoRegistry,{review:true});assert.equal(view.items.length,1);
 view=organization.libraryProjection(entries.map(e=>({...e,needs_review:false})),folders,demoRegistry,{review:true});assert.equal(view.review,false);assert.equal(view.reviewCount,0);assert.equal(view.items.length,2);
});
test('semantic search matches remain visible with counts from the same library snapshot',()=>{
 const entries=[demoEntry(0),demoEntry(1,'recipe')];
 const view=organization.libraryProjection(entries,[],demoRegistry,{query:'weekend inspiration',semanticIds:['0']});
 assert.equal(view.items.map(e=>e.id).join(','),'0');assert.equal(JSON.stringify(view.typeCounts),JSON.stringify({place:1}));assert.equal(view.all.length,2);
});
test('grouping and unit choices survive offline restart and remain queued for the owner',async()=>{
 const env=environment(),lib=env.load();await lib.setPreference('groupBy','type');await lib.setPreference('distanceUnits','imperial');await lib.syncLibrary();const state=await env.load().loadLibrary();assert.equal(state.preferences.groupBy,'type');assert.equal(state.preferences.distanceUnits,'imperial');assert.equal(state.outbox.filter(o=>o.kind==='preferences').length,2);env.close();
});
test('failed sync preserves last success and local changes instead of recording a new success',async()=>{
 const env=environment(),lib=env.load();env.mocks['./api'].connectDevice=async()=>{};env.mocks['./api'].request=async()=>({items:[],saves:[],folders:[],registry:{},apple_linked:false});
 await lib.syncLibrary();const last=(await lib.loadLibrary()).last_synced;assert.ok(last);
 await lib.queueOperation({kind:'folder_create',method:'POST',path:'/folders',body:{id:'pending-folder',name:'Offline review'}});
 env.mocks['./api'].connectDevice=async()=>{throw Error('Connection timed out')};await lib.syncLibrary();const failed=await lib.loadLibrary();assert.equal(failed.last_synced,last);assert.equal(failed.sync_error,'Connection timed out');assert.equal(failed.outbox.length,1);assert.equal(failed.folders[0].name,'Offline review');env.close();
});
test('request timeouts and network failures explain that saves remain local',async()=>{
 const mocks={'./config':{appConfig:{apiUrl:'http://example.test'},visualFixture:false},'./identity':{getIdentity:async()=>({token:'test'})}};
 let api=load('api',mocks,{fetch:(_,options)=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Error('Aborted'))))});
 await assert.rejects(api.request('/sync','GET',undefined,5),/processing service did not respond.*saves are still/);
 api=load('api',mocks,{fetch:async()=>{throw new TypeError('Network request failed')}});await assert.rejects(api.request('/sync'),/Could not connect.*saves are still/);
});

test('cold launch drains a force-quit share before any network access and survives a second launch',async()=>{
 const env=environment();let reports=[];env.mocks['./sharedGroup'].recordDrain=async report=>reports.push(report);
 env.setQueue([{id:'cold',url:'https://vm.tiktok.com/Ab123/',timestamp:123}]);
 const state=await env.load().coldStartLibrary();assert.equal(state.saves.length,1);assert.equal(state.saves[0].local,true);assert.equal(env.getQueue().length,0);assert.equal(reports[0].trigger,'cold_launch');assert.equal(reports[0].successes,1);
 const again=await env.load().coldStartLibrary();assert.equal(again.saves.length,1);env.close();
});
test('one failed acknowledgement does not block later queue items and replay deduplicates',async()=>{
 const env=environment();env.setQueue([{id:'a',url:'https://youtu.be/abcdefghijk',timestamp:1},{id:'b',url:'https://instagram.com/reel/ABC/',timestamp:2}]);let failures=1;const ack=env.mocks['./sharedGroup'].acknowledgeSharedEntry;
 env.mocks['./sharedGroup'].acknowledgeSharedEntry=async id=>{if(id==='a'&&failures-- >0)throw Error('interrupted');await ack(id)};
 const lib=env.load();await assert.rejects(lib.drainContainer());assert.equal((await lib.loadLibrary()).saves.length,2);assert.equal(env.getQueue().length,1);await lib.drainContainer();assert.equal((await lib.loadLibrary()).saves.length,2);assert.equal(env.getQueue().length,0);env.close();
});
test('invalid shared URL is quarantined while later valid items commit',async()=>{
 const env=environment();env.setQueue([{id:'bad',url:'file:///photo.png',timestamp:1},{id:'ok',url:'https://youtu.be/abcdefghijk',timestamp:2}]);let report;env.mocks['./sharedGroup'].recordDrain=async value=>{report=value};const lib=env.load();await assert.rejects(lib.drainContainer());assert.equal(report.quarantined,1);assert.equal(report.successes,1);assert.equal(env.getQueue().length,0);assert.equal((await lib.loadLibrary()).saves.length,1);env.close();
});
test('overlapping drains share one run and produce one durable save',async()=>{
 const env=environment();env.setQueue([{id:'q',url:'https://youtu.be/abcdefghijk',timestamp:1}]);const lib=env.load();const a=lib.drainContainer('manual'),b=lib.drainContainer('foreground');assert.equal(a,b);await Promise.all([a,b]);assert.equal((await lib.loadLibrary()).saves.length,1);env.close();
});
