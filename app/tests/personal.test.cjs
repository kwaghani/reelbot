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
 const mocks={'expo-sqlite':{openDatabaseAsync:async()=>sqlite},'expo-crypto':{randomUUID:crypto.randomUUID},'./reelUrls':urls,'./libraryModel':model,'./api':{ApiError,connectDevice:async()=>{throw Error('offline')},request:async()=>{throw Error('offline')}},'./identity':{resetIdentity:async()=>{}},'./sharedGroup':{readSharedQueue:async()=>queue,acknowledgeSharedEntry:async id=>{if(failAck)throw Error('interrupted');queue=queue.filter(e=>e.id!==id)}}};
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
