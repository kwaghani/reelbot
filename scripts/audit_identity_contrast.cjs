const fs=require('node:fs'),vm=require('node:vm'),ts=require('../app/node_modules/typescript'),yaml=require('../app/node_modules/js-yaml');
const exportsTheme={};vm.runInNewContext(ts.transpileModule(fs.readFileSync('app/src/theme.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{exports:exportsTheme,require:()=>({})});
const luminance=color=>color.slice(1).match(/../g).map(h=>parseInt(h,16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((v,x,i)=>v+x*[.2126,.7152,.0722][i],0);
const contrast=(a,b)=>{const l=[luminance(a),luminance(b)].sort((a,b)=>a-b);return +( (l[1]+.05)/(l[0]+.05)).toFixed(3)};
const report={text:[],markers:[]};
for(const [name,c] of [['light',exportsTheme.lightColors],['dark',exportsTheme.darkColors]]){
 for(const surface of ['background','card','cardMuted','accentSoft','dangerSoft','successSoft'])for(const foreground of ['textPrimary','textSecondary']) report.text.push({theme:name,foreground,background:surface,ratio:contrast(c[foreground],c[surface])});
 for(const [a,b] of [['accent','background'],['accent','card'],['inkText','accent'],['danger','dangerSoft'],['warningText','warningBackground'],['card','ink']])report.text.push({theme:name,foreground:a,background:b,ratio:contrast(c[a],c[b])});
 for(const [a,b] of [['success','successSoft'],['red','background']])report.text.push({theme:name,foreground:a,background:b,ratio:contrast(c[a],c[b])});
 const tints=new Map(); for(let i=0;i<20;i++){const tint=exportsTheme.folderTint(String(i),c);tints.set(tint.background,tint.foreground)}
 for(const [background,foreground] of tints)report.text.push({theme:name,foreground,background,ratio:contrast(foreground,background)});
}
for(const [kind,spec] of Object.entries(yaml.load(fs.readFileSync('config/content_types.yaml','utf8')).venue_kinds))report.markers.push({kind,color:spec.color,whiteGlyph:contrast(spec.color,'#FFFFFF'),paper:contrast(spec.color,'#E9EDE7'),water:contrast(spec.color,'#9FBFC9')});
report.textAA=report.text.every(p=>p.ratio>=4.5);report.glyphAA=report.markers.every(p=>p.whiteGlyph>=4.5);report.fillAgainstMap=report.markers.every(p=>p.paper>=3&&p.water>=3);
fs.writeFileSync('identity-evidence/contrast.json',JSON.stringify(report,null,2));console.log({textAA:report.textAA,glyphAA:report.glyphAA,fillAgainstMap:report.fillAgainstMap});
