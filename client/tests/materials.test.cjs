const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const React=require('react');
const {act,create}=require('react-test-renderer');
function load(name,rpc){const filename=path.resolve(__dirname,'../src/client/'+name+'.tsx');const text=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;const mod=new Module(filename,module);mod.filename=filename;mod.paths=Module._nodeModulePaths(path.dirname(filename));const original=mod.require.bind(mod);mod.require=id=>id==='./api'?{rpc}:original(id);mod._compile(text,filename);return mod.exports;}
const info={port:1,token:'isolated-test'};
const row={id:'a'.repeat(32),doc_id:'source:a',title:'source',locator:'selected.txt',kind:'local',revision:'revision',project_id:'',freshness:'current',indexed:true,index_version:'b'.repeat(64)};
const page={ok:true,sources:[row],projects:[{id:'p',name:'Project'}]};
const button=(view,label)=>view.root.findAllByType('button').find(b=>b.children.join('')===label);

test('failed source refresh keeps status queryable and does not replay or remove the source',async()=>{
 const calls=[];const rpc=async(_i,method,args)=>{calls.push([method,args]);if(args.payload.action==='list')return page;return{ok:false,error:'embedding failed',change_state:'partial'};};
 const {MaterialsPane}=load('materials',rpc);let view;await act(async()=>{view=create(React.createElement(MaterialsPane,{info,onIndexed(){}}));});
 await act(async()=>button(view,'刷新此索引').props.onClick());
 assert.match(JSON.stringify(view.toJSON()),/已部分变更/);assert.ok(button(view,'仅移除索引'));
 const writes=calls.filter(c=>c[1].payload.action!=='list');assert.equal(writes.length,1);assert.equal(writes[0][1].payload.source_id,row.id);
 act(()=>view.unmount());
});

test('current-source pagination stays on the first captured version',async()=>{
 const calls=[];const version='c'.repeat(64);const rpc=async(_i,method,args)=>{calls.push([method,args]);const a=args.payload;
  if(a.action==='list')return page;if(a.action==='read_current')return{ok:true,source:{source_id:row.id,version,text:'first',offset:0,next_offset:5,chars:9}};
  if(a.action==='read_version'){assert.equal(a.version,version);assert.equal(a.offset,5);return{ok:true,source:{source_id:row.id,version,text:'last',offset:5,next_offset:null,chars:9}};}
 };const {MaterialsPane}=load('materials',rpc);let view;await act(async()=>{view=create(React.createElement(MaterialsPane,{info,onIndexed(){}}));});
 await act(async()=>button(view,'查看当前原文').props.onClick());await act(async()=>button(view,'继续读取此版本').props.onClick());
 assert.match(JSON.stringify(view.toJSON()),/firstlast/);assert.equal(calls.filter(c=>c[1].payload.action==='read_current').length,1);act(()=>view.unmount());
});

test('cancelling batch registration admits no remaining files after the current receipt',async()=>{
 let resolve;const registered=[];const rpc=async(_i,method,args)=>{
  if(method==='pick_local_sources')return{ok:true,sources:[{id:'one',kind:'file',name:'one'},{id:'two',kind:'file',name:'two'}]};
  if(method==='cancel_local_source_request')return{ok:true};
  if(args.payload.action==='list')return page;
  if(args.payload.action==='register'){registered.push(args.payload.snapshot_id);return new Promise(r=>{resolve=r;});}
 };const {MaterialsPane}=load('materials',rpc);let view;await act(async()=>{view=create(React.createElement(MaterialsPane,{info,onIndexed(){}}));});
 act(()=>button(view,'登记本地文件').props.onClick());await act(async()=>new Promise(r=>setImmediate(r)));
 await act(async()=>button(view,'取消本次操作').props.onClick());await act(async()=>resolve(page));
 assert.deepEqual(registered,['one']);act(()=>view.unmount());
});

test('citation lookup retains the saved version and renders current version separately',async()=>{
 const quote={n:1,doc:'saved',title:'section',doc_id:'source:a',text:'saved quote',source_id:row.id,source_version:'b'.repeat(64)};
 const calls=[];const rpc=async(_i,method,args)=>{calls.push(args.payload);return{ok:true,source:{text:'current source',version:'c'.repeat(64),next_offset:null}};};
 const {MaterialCitation}=load('material-citation',rpc);let view;await act(async()=>{view=create(React.createElement(MaterialCitation,{info,quote}));});
 await act(async()=>button(view,'核对当前来源').props.onClick());assert.equal(quote.text,'saved quote');assert.equal(quote.source_version,'b'.repeat(64));
 assert.match(JSON.stringify(view.toJSON()),/当前核对版本与引用不同/);assert.equal(calls[0].project_id,'');act(()=>view.unmount());
});
