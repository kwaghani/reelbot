const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function load(name, mocks, globals = {}) {
  const exports = {};
  const source = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src', name + '.ts'), 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
  const context = {exports, require:(id)=>{if (!(id in mocks)) throw new Error('Unexpected dependency '+id); return mocks[id];}, Error, TypeError, SyntaxError, URL, URLSearchParams, AbortController, setTimeout, clearTimeout, console, ...globals};
  vm.runInNewContext(source, context, {filename:name+'.js'});
  return exports;
}
const config={appConfig:{apiUrl:'https://audit.invalid',apiKey:'test',testGroupId:'default'},getApiConfigIssue:()=>null,apiHostLabel:()=>'audit.invalid'};
const response=(body,status=200)=>({ok:status<400,status,text:async()=>typeof body==='string'?body:JSON.stringify(body)});

test('share carries authenticated identity and selected destination, even across later context changes', async()=>{
  const calls=[];
  const api=load('api',{'./config':config},{fetch:async(url,init)=>{calls.push({url,...init});return response({status:'queued',job_id:'job-1'});}});
  api.setApiContext({deviceId:'alice',groupId:'private-a',token:'alice-secret'});
  const pending=api.shareReel('https://instagram.com/reel/ABC/','Same Name');
  api.setApiContext({groupId:'private-b'});
  const result=await pending;
  assert.equal(result.job_id,'job-1');
  assert.equal(JSON.parse(calls[0].body).group_id,'private-a');
  assert.equal(JSON.parse(calls[0].body).device_id,'alice');
  assert.equal(calls[0].headers.Authorization,'Bearer alice-secret');
});

test('queued questions poll their original job and return its actual answer', async()=>{
  const calls=[];
  const api=load('api',{'./config':config},{setTimeout:(fn,ms)=>setTimeout(fn,ms===1000?0:ms),fetch:async(url,init)=>{calls.push(url); return response(url.endsWith('/query')?{status:'processing',job_id:'question-1',answer:''}:{id:'question-1',status:'done',answer:'Pasta: ten minutes.',sources:[{title:'Pasta',url:'https://instagram.com/reel/ABC/'}]});}});
  api.setApiContext({deviceId:'alice',groupId:'group-a',token:'alice-token'});
  const answer=await api.askQuestion('How long?','Alice');
  assert.equal(answer.answer,'Pasta: ten minutes.');
  assert.equal(answer.sources.length,1);
  assert.ok(calls[1].endsWith('/jobs/question-1'));
});

test('server failures and malformed success responses are errors, not success', async()=>{
  for(const [body,status] of [['<html>offline</html>',503],['not json',200],[{detail:'Not a member'},403]]) {
    const api=load('api',{'./config':config},{fetch:async()=>response(body,status)});
    await assert.rejects(api.getItems());
  }
});

test('pre-aborted share never becomes a successful submission',async()=>{
  const api=load('api',{'./config':config},{fetch:async(_url,init)=>{if(init.signal.aborted)throw new Error('aborted');return response({status:'queued'});}});
  const controller=new AbortController();controller.abort();
  await assert.rejects(api.shareReel('https://instagram.com/reel/ABC/','Alice',controller.signal),/cancelled/);
});

test('incomplete submission and answer payloads never report success',async()=>{
  const api=load('api',{'./config':config},{fetch:async()=>response({status:'processing'})});
  await assert.rejects(api.shareReel('https://instagram.com/reel/ABC/','Alice'),/not confirmed/);
  await assert.rejects(api.askQuestion('hello','Alice'),/not confirmed/);
});

test('query retries reuse uncertain requests but replace confirmed failed jobs',async()=>{
  const requestIds=[];let attempt=0;
  const api=load('api',{'./config':config},{fetch:async(_url,init)=>{
    requestIds.push(JSON.parse(init.body).request_id);
    attempt++;
    if(attempt===1)throw new Error('connection interrupted');
    if(attempt===2)return {...response({detail:'Provider unavailable'},503),headers:{get:()=> 'true'}};
    return response({answer:'Hello',sources:[]});
  }});
  await assert.rejects(api.askQuestion('hello','Alice'),/interrupted/);
  await assert.rejects(api.askQuestion('hello','Alice'),/Provider unavailable/);
  assert.equal((await api.askQuestion('hello','Alice')).answer,'Hello');
  assert.equal(requestIds[0],requestIds[1]);
  assert.notEqual(requestIds[1],requestIds[2]);
});

test('concurrent identity initialization registers once and restores the saved session',async()=>{
  const store=new Map();let registered=0;const contexts=[];
  const storage={getItem:async(key)=>store.get(key)||null,setItem:async(key,value)=>{store.set(key,value);}};
  const mocks={'@react-native-async-storage/async-storage':{default:storage},'./config':config,'./api':{registerDevice:async()=>{registered++;return {device_id:'server-issued-id',token:'secret'};},setApiContext:(value)=>contexts.push(value)}};
  const first=load('identity',mocks);
  assert.deepEqual(await Promise.all([first.getDeviceId(),first.getDeviceId(),first.getDeviceId()]),Array(3).fill('server-issued-id'));
  assert.equal(registered,1);
  const restarted=load('identity',mocks);
  assert.equal(await restarted.getDeviceId(),'server-issued-id');
  assert.equal(registered,1);
  assert.equal(contexts.at(-1).token,'secret');
});

test('storage failure cannot report completed identity setup',async()=>{
  const identity=load('identity',{'@react-native-async-storage/async-storage':{default:{getItem:async()=>null,setItem:async()=>{throw new Error('disk full');}}},'./config':config,'./api':{registerDevice:async()=>({device_id:'id',token:'secret'}),setApiContext:()=>{throw new Error('must not authorize before persistence');}}});
  await assert.rejects(identity.getDeviceId(),/disk full/);
});

test('native shared preferences decode JSON strings as well as objects for settings and receipts',async()=>{
  const settings={apiUrl:'https://audit.invalid',apiKey:'test',userName:'Audit',testGroupId:'default',deviceId:'device',deviceToken:'token',activeGroupId:'private',activeGroupName:'Private'};
  const receipt={url:'https://instagram.com/reel/ABC/',createdAt:Date.now(),status:'queued',jobId:'job',groupId:'private'};
  for(const encoded of [true,false]) {
    const shared=load('sharedGroup',{'react-native-shared-group-preferences':{default:{getItem:async(key)=>{const value=key.endsWith('shareSettings')?settings:receipt;return encoded?JSON.stringify(value):value;}}},'./api':{getDeviceCredentials:()=>({deviceId:'device',token:'token'})},'./config':{appConfig:{...config.appConfig,appGroupIdentifier:'group.audit'}}});
    assert.equal((await shared.readShareSettings()).deviceToken,'token');
    assert.equal((await shared.readShareSettings()).activeGroupId,'private');
    assert.equal((await shared.readLastShareReceipt()).jobId,'job');
  }
});

test('permission failures are distinguishable from temporary outages so private cached views can be cleared',async()=>{
  for(const status of [401,403,503]) {
    const api=load('api',{'./config':config},{fetch:async()=>response({detail:'Unavailable'},status)});
    try {await api.getItems();assert.fail('request must reject');}
    catch(error) {assert.equal(api.isAuthorizationError(error),status!==503);}
  }
});
