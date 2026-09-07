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
 const mocks={'expo-sqlite':{openDatabaseAsync:async()=>sqlite},'expo-crypto':{randomUUID:crypto.randomUUID},'./reelUrls':urls,'./libraryModel':model,'./api':{ApiError,connectDevice:async()=>{throw Error('offline')},request:async()=>{throw Error('offline')}},'./sharedGroup':{readSharedQueue:async()=>queue,acknowledgeSharedEntry:async id=>{if(failAck)throw Error('interrupted');queue=queue.filter(e=>e.id!==id)}}};
 return {load:()=>load('library',mocks),setQueue:value=>{queue=value},getQueue:()=>queue,failAck:value=>{failAck=value},close:()=>connection.close()};
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
