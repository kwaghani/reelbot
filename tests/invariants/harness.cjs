// Executes production TypeScript against real on-disk SQLite. Native queue,
// network and crash boundaries are injected; this is NOT an iPhone reboot.
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const {DatabaseSync}=require('node:sqlite'),ts=require('../../app/node_modules/typescript');
function environment() {
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),'reelbot-invariants-')),filename=path.join(directory,'library.db');
 let connection=new DatabaseSync(filename),chain=Promise.resolve(),queue=[],quarantine=[],failAck=false,diskFull=false,now=Date.now(),calls=[],timers=[];
 class Clock extends Date { constructor(...args){super(...(args.length?args:[now]));} static now(){return now;} }
 class ApiError extends Error { constructor(message,status){super(message);this.status=status;} }
 const sqlite={execAsync:async sql=>connection.exec(sql),runAsync:async(sql,...args)=>{if(diskFull)throw Error('SQLITE_FULL');return connection.prepare(sql).run(...args)},getFirstAsync:async(sql,...args)=>connection.prepare(sql).get(...args),withExclusiveTransactionAsync:fn=>{const run=chain.then(async()=>{connection.exec('BEGIN IMMEDIATE');try{await fn(sqlite);connection.exec('COMMIT')}catch(e){connection.exec('ROLLBACK');throw e}});chain=run.catch(()=>{});return run}};
 let handler=async()=>{throw Error('offline')},connected=true;
 let cache={};
 function load(name){if(cache[name])return cache[name];const out={};cache[name]=out;
 const mocks={'expo-sqlite':{openDatabaseAsync:async()=>sqlite},'expo-file-system/legacy':{},'expo-crypto':{randomUUID:crypto.randomUUID},'./config':{visualFixture:false},'./identity':{resetIdentity:async()=>{}},'./session':{getResetPending:async()=>null},'./api':{ApiError,connectDevice:async()=>{if(!connected)throw Error('offline')},request:async(p,m,b)=>{calls.push({path:p,method:m,body:b});return handler(p,m,b)}},'./sharedGroup':{readSharedQueue:async()=>queue,acknowledgeSharedEntry:async id=>{if(failAck)throw Error('killed before ack');queue=queue.filter(e=>e.id!==id)},quarantineSharedEntry:async id=>{quarantine.push(...queue.filter(e=>e.id===id));queue=queue.filter(e=>e.id!==id)},recordDrain:async r=>r}};
 const source=fs.readFileSync(path.join(__dirname,'../../app/src',name+'.ts'),'utf8');
 vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:out,require:id=>id in mocks?mocks[id]:load(id.replace('./','')),Date:Clock,URL,URLSearchParams,Error,TypeError,console,setTimeout:(fn,ms)=>{timers.push(fn);return timers.length},clearTimeout:()=>{}});return out;}
 const api={load:()=>load('library'),restart:()=>{connection.close();connection=new DatabaseSync(filename);cache={};timers=[];return load('library')},raw:()=>JSON.parse(connection.prepare('SELECT value FROM library WHERE id=1').get().value),seed:async state=>{await load('library').loadLibrary();connection.prepare('UPDATE library SET value=? WHERE id=1').run(JSON.stringify({...load('libraryModel').emptyLibrary(),retention_version:1,...state}))},request:fn=>handler=fn,offline:v=>connected=!v,queue:v=>queue=v,pending:()=>queue,quarantined:()=>quarantine,ackFailure:v=>failAck=v,full:v=>diskFull=v,advance:ms=>now+=ms,calls,ApiError,close:()=>{connection.close();for(const name of fs.readdirSync(directory))fs.unlinkSync(path.join(directory,name));fs.rmdirSync(directory)}};
 return api;
}
const entry=(i=0)=>({id:'e'+i,save_id:'s'+i,place_id:'p'+i,content_type:'place',title:'Venue '+i,note:'original',name:'Venue '+i,summary:'Summary',attributes:{venue_kind:'cafe'},venue_kind:'cafe',venue_kind_source:'user',city:'',folders:[],source_url:'https://instagram.com/reel/R'+i+'/',created_at:new Date().toISOString(),needs_review:false,confidence:1,lat:37+i/100,lng:-122,coords_fetched_at:new Date(Date.now()-1000).toISOString()});
const page=(changes=[])=>({changes,cursor:'unchanged',has_more:false,reset:false,registry:{},venue_kinds:{},preferences:{},apple_linked:false});
module.exports={environment,entry,page};
