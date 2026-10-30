const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),React=require('react');
const {act,create}=require('react-test-renderer');
function compile(file,imports={}){const filename=path.resolve(__dirname,'../src/client/'+file),mod=new Module(filename,module);mod.filename=filename;mod.paths=Module._nodeModulePaths(path.dirname(filename));const original=mod.require.bind(mod);mod.require=id=>id in imports?imports[id]:id.endsWith('.css')?{}:original(id);mod._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText,filename);return mod.exports;}
const state=compile('workflow-state.ts');
test('workflow input preview preserves unknown values and never evaluates commands',()=>{
  const receipts={a:{output:{text:'$(do-not-run)',urls:['https://example.org']}}};
  assert.equal(state.resolvePreview({text:'{{a.text}}'},receipts).text,'$(do-not-run)');
  assert.equal(state.resolvePreview({text:'{{missing.text}}'},receipts).text,'（上游尚未取得）');
  assert.equal(state.describeTarget({kind:'base',config:{base_token:'b',table_id:'t'}}),'b / t');
  assert.equal(state.editableRun({state:'unknown'}),false);
  assert.equal(state.editableRun({state:'failed'}),true);
});

test('workflow publishing previews the fixed target before sending, and a lost response retains the submission key',async()=>{
  const calls=[];let failures=1;
  const graph={nodes:[{id:'trigger',kind:'trigger',config:{text:''},position:{x:0,y:0}},{id:'send',kind:'message',config:{chat_id:'oc_target',text:'已确认的正文',profile:''},position:{x:0,y:100}}],edges:[{source:'trigger',target:'send',branch:''}]};
  const definition={id:'workflow',name:'日报',revision:1,version:'a'.repeat(64),graph};
  const catalog=[{kind:'trigger',label:'触发',defaults:{text:''},outputs:['text'],writes:false},{kind:'message',label:'发送群消息',defaults:{chat_id:'',text:'',profile:''},outputs:['message_id'],writes:true}];
  const run={id:'run',workflow:'workflow',version:definition.version,name:'日报',mode:'full',state:'running',receipts:{},approved:['send'],graph};
  const rpc=async(_info,method,{operation,payload}={})=>{calls.push([method,operation,payload]);if(operation==='catalog')return{ok:true,nodes:catalog};if(operation==='list')return{ok:true,definitions:[definition],runs:[]};if(operation==='load')return{ok:true,definition};if(operation==='check_targets')return{ok:true,targets:[{node:'send',target:'oc_target',name:'目标群'}]};if(operation==='start'){if(failures-- >0)throw Error('WebSocket disconnected');return{ok:true,run};}return{ok:true};};
  const flow={ReactFlow:props=>React.createElement('div',{'data-canvas':true},props.children),ReactFlowProvider:({children})=>children,Background:()=>null,Controls:()=>null,Handle:()=>null,Position:{Top:'top',Bottom:'bottom'},addEdge:(edge,edges)=>[...edges,edge],useNodesState:initial=>{const[v,set]=React.useState(initial);return[v,set,()=>{}];},useEdgesState:initial=>{const[v,set]=React.useState(initial);return[v,set,()=>{}];},useNodesInitialized:()=>true,useReactFlow:()=>({screenToFlowPosition:value=>value,fitView:()=>{}})};
  const editor=compile('workflow-editor.tsx',{'./api':{rpc},'./page-stack':{usePageActive:()=>true},'./workflow-state':state,'@xyflow/react':flow});
  const previous=global.window;global.window={setInterval:()=>1,clearInterval(){},confirm:()=>true};
  let renderer;
  try{
    await act(async()=>{renderer=create(React.createElement(editor.WorkflowPane,{info:{port:1,token:'test'}}));});
    await act(async()=>renderer.root.findByProps({'aria-label':'已保存工作流'}).props.onChange({target:{value:'workflow'}}));
    const button=text=>renderer.root.findAllByType('button').find(node=>node.children.join('')===text);
    await act(async()=>button('运行工作流').props.onClick());
    assert.equal(calls.filter(c=>c[1]==='start').length,0);
    assert.match(JSON.stringify(renderer.toJSON()),/oc_target/);
    assert.match(JSON.stringify(renderer.toJSON()),/已确认的正文/);
    await act(async()=>button('确认目标并继续执行').props.onClick());
    assert.match(JSON.stringify(renderer.toJSON()),/WebSocket disconnected/);
    await act(async()=>button('确认目标并继续执行').props.onClick());
    const submitted=calls.filter(c=>c[1]==='start');assert.equal(submitted.length,2);
    assert.equal(submitted[0][2].request_id,submitted[1][2].request_id);
    assert.equal(submitted[1][2].confirm_writes,true);
    assert.equal(submitted[1][2].version,definition.version);
  }finally{act(()=>renderer?.unmount());global.window=previous;}
});
