const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),ts=require('typescript'),yaml=require('js-yaml');
const exportsForModel={};vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/attributeModel.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:exportsForModel});
const m=exportsForModel,document=yaml.load(fs.readFileSync(path.join(__dirname,'../../config/content_types.yaml'),'utf8'));
const {venue_kinds:kinds,venue_kind_signals:signals,...registry}=document;
const entry=(kind,attributes)=>({content_type:'place',venue_kind:kind,attributes:{venue_kind:kind,...attributes}});
test('restaurant trail and hotel use different registry schemas and hide empty attributes',()=>{
 const trail=entry('outdoors',{activity:'hike',distance_km:5.4,permit_required:false,cuisine:'Thai',parking:null});
 const shown=m.visibleAttributes(trail,registry,kinds).map(([k])=>k);
 assert.ok(shown.includes('activity'));assert.ok(shown.includes('distance_km'));assert.ok(shown.includes('permit_required'));assert.ok(!shown.includes('cuisine'));assert.ok(!shown.includes('parking'));
 assert.equal(m.attributeText(false),'No');assert.equal(m.attributeText(5.4),'5.4');
 assert.ok('cuisine' in m.attributeFields(entry('restaurant',{}),registry,kinds));assert.ok(!('distance_km' in m.attributeFields(entry('restaurant',{}),registry,kinds)));
 assert.ok('nightly_rate' in m.attributeFields(entry('hotel',{}),registry,kinds));assert.ok(!('cuisine' in m.attributeFields(entry('hotel',{}),registry,kinds)));
});
test('seventeenth kind and attributes added only in YAML flow to the existing detail renderer',()=>{
 const addition='\n  observatory:\n    label: Observatory\n    icon: telescope\n    color: "#315A89"\n    google_types: [observatory]\n    attributes:\n      telescope: {type: string}\n      night_access: {type: boolean}\n';
 const text=yaml.dump({venue_kinds:kinds})+addition;const extra=yaml.load(text).venue_kinds;
 assert.equal(Object.keys(extra).length,17);
 const rows=m.visibleAttributes(entry('observatory',{telescope:'Reflector',night_access:true}),registry,extra);
 assert.ok(rows.some(([k])=>k==='telescope'));assert.ok(rows.some(([k])=>k==='night_access'));
 assert.equal(Object.keys(kinds).length,16);
});
test('changing kind strips obsolete fields; all sixteen kinds have configured attributes and signals',()=>{
 for(const [key,spec] of Object.entries(kinds)){assert.ok(spec.attributes);assert.ok(signals[key]);}
 const fields=m.attributeFields(entry('outdoors',{}),registry,kinds);
 assert.deepEqual(JSON.parse(JSON.stringify(m.cleanAttributes({venue_kind:'outdoors',cuisine:'Thai',activity:'hike'},fields))),{venue_kind:'outdoors',activity:'hike'});
 const detail=fs.readFileSync(path.join(__dirname,'../src/EntryDetail.tsx'),'utf8');assert.ok(detail.includes('visibleAttributes(detail'));assert.ok(!detail.includes('Not specified'));
});
