const test=require('node:test'),assert=require('node:assert/strict');
const {environment,page}=require('./harness.cjs');
test('I1: kill mid-drain after commit before acknowledgment preserves one save on replay',async()=>{
 const env=environment();try{env.queue([{id:'q',url:'https://instagram.com/reel/A/',timestamp:1}]);env.ackFailure(true);await assert.rejects(env.load().drainContainer());assert.equal(env.raw().saves.length,1);env.ackFailure(false);await env.restart().drainContainer();assert.equal(env.raw().saves.length,1);assert.equal(env.pending().length,0);}finally{env.close();}
});
test('I1: lost upload acknowledgment and backend outage keep a visible pending URL',async()=>{
 const env=environment();try{const lib=env.load();await lib.saveUrl('https://instagram.com/reel/A/');env.request(async()=>{throw Error('killed during upload')});await lib.syncLibrary();assert.equal(env.raw().saves[0].local,true);const restarted=env.restart();env.offline(true);await restarted.syncLibrary();assert.equal((await restarted.loadLibrary()).saves[0].status,'queued');}finally{env.close();}
});
test('I1: corrupt item is quarantined; subsequent 50 shares persist with backend down',async()=>{
 const env=environment();try{env.offline(true);env.queue([{id:'bad',url:'file:///bad',timestamp:0},...Array.from({length:50},(_,i)=>({id:'q'+i,url:'https://instagram.com/reel/R'+i+'/',timestamp:i}))]);await env.load().coldStartLibrary();assert.equal(env.raw().saves.length,50);assert.equal(env.quarantined().length,1);assert.ok(env.raw().sync_error);assert.equal((await env.restart().loadLibrary()).saves.length,50);}finally{env.close();}
});
test('I1: disk full never acknowledges a share that SQLite could not commit',async()=>{
 const env=environment();try{const lib=env.load();await lib.loadLibrary();env.queue([{id:'q',url:'https://instagram.com/reel/A/',timestamp:0}]);env.full(true);await assert.rejects(lib.drainContainer());assert.equal(env.pending().length,1);env.full(false);await env.restart().drainContainer();assert.equal(env.raw().saves.length,1);}finally{env.close();}
});
