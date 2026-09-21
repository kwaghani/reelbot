const test=require('node:test'),assert=require('node:assert/strict');
const {environment,entry,page}=require('./harness.cjs');
test('I4: ten pins persist across five consecutive closed-database cold launches, offline',async()=>{
 const env=environment();try{await env.seed({items:Array.from({length:10},(_,i)=>entry(i)),sync_cursor:'unchanged'});env.offline(true);for(let i=0;i<5;i++){const state=await env.restart().coldStartLibrary();assert.equal(state.items.filter(e=>e.lat!==null).length,10,'I4: pin lost on launch '+i);assert.equal(state.items[0].note,'original');}assert.equal(env.calls.length,0);}finally{env.close();}
});
test('I4: 31-day coordinates are nulled on disk before one cursor-independent batch restores ten pins',async()=>{
 const env=environment();try{const points=Array.from({length:10},(_,i)=>entry(i));await env.seed({items:points.map(e=>({...e,coords_fetched_at:new Date(Date.now()-31*86400000).toISOString()})),sync_cursor:'unchanged'});env.offline(true);let lib=env.restart();await lib.coldStartLibrary();assert.equal(env.raw().items.filter(e=>e.lat===null).length,10);env.offline(false);lib=env.restart();env.request(async(p,m,b)=>{assert.equal(p,'/coordinates/reconcile');assert.equal(b.entry_ids.length,10);return{coordinates:points}});await lib.reconcileCoordinates();assert.equal(env.raw().items.filter(e=>e.lat!==null).length,10);assert.equal(env.calls.length,1);assert.equal(env.raw().sync_cursor,'unchanged');}finally{env.close();}
});
test('I4: unknown and future leases are deleted locally while writing and pending mutations survive restart',async()=>{
 const env=environment();try{await env.seed({items:[{...entry(),coords_fetched_at:null},{...entry(1),coords_fetched_at:new Date(Date.now()+86400000).toISOString()}]});env.offline(true);let lib=env.load();await lib.queueOperation({kind:'note',target:'e0',path:'/items/e0',method:'PATCH',body:{note:'Unsynced writing'}});lib=env.restart();const state=await lib.coldStartLibrary();assert.equal(state.items[0].note,'Unsynced writing');assert.equal(state.items.filter(e=>e.lat===null).length,2);assert.equal(state.outbox[0].body.note,'Unsynced writing');}finally{env.close();}
});
