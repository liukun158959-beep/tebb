const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module');
const ts = require('typescript'), React = require('react');
const { act, create } = require('react-test-renderer');
function load(name, mocks={}) {
  const base=path.resolve(__dirname,'../src/client',name);const filename=fs.existsSync(base+'.tsx')?base+'.tsx':base+'.ts';
  const mod=new Module(filename,module);mod.filename=filename;mod.paths=Module._nodeModulePaths(path.dirname(filename));
  const original=mod.require.bind(mod);mod.require=id=>mocks[id]||(id.startsWith('./')?load(id.slice(2),mocks):original(id));
  mod._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText,filename);return mod.exports;
}
const text=n=>typeof n==='string'?n:(n.children||[]).map(text).join('');

test('pet retains real progress after chat closes, reports failure, and respects dismissal for descendants',()=>{
  const filename=path.resolve(__dirname,'../src/pet-feedback.ts');const mod=new Module(filename,module);
  mod._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);
  const {PetFeedback}=mod.exports;const feedback=new PetFeedback();const progress={enabled:true};
  const old={id:'old',state:'failed',error:'上次启动前的失败'};
  assert.equal(feedback.update(progress,[old],false),null);
  const run={id:'new',state:'running',kind:'chat',title:'hello',events:['正在调用模型']};
  assert.equal(feedback.update(progress,[old,run],true),null);
  assert.match(feedback.update(progress,[old,run],false).text,/正在调用模型/);
  run.answer='你好，';assert.match(feedback.update(progress,[old,run],false).text,/你好/);
  run.state='succeeded';run.answer='你好，有什么可以帮你？';
  assert.match(feedback.update(progress,[old,run],false).text,/已完成.*hello\n你好/);
  feedback.dismiss();assert.equal(feedback.update(progress,[old,run],false),null);
  const child={id:'child',parent:'new',state:'running'};
  assert.equal(feedback.update(progress,[old,run,child],false),null);
  const next={id:'next',state:'running',kind:'route',answer:'{"mode":"task","instructions":"raw JSON"}'};
  assert(!feedback.update(progress,[old,run,child,next],false).text.includes('raw JSON'));
  next.state='timed_out';next.error='模型调用超时';child.state='succeeded';
  assert.equal(feedback.update(progress,[old,run,child,next],false).error,true);
  assert.match(feedback.current.text,/模型调用超时/);
  assert.equal(feedback.update({enabled:false},[old,run,child,next],false),null);
  const concurrent=new PetFeedback();const ongoing={id:'ongoing',created:1,state:'running'};
  concurrent.update(progress,[ongoing],false);
  const failed={id:'fresh',created:2,state:'failed',error:'新消息调用失败'};
  assert.match(concurrent.update(progress,[ongoing,failed],false).text,/新消息调用失败/);
  assert.match(concurrent.update(progress,[ongoing,failed],true) || '',/^$/);
  assert.match(concurrent.update(progress,[ongoing,failed],false).text,/新消息调用失败/);
});

test('visited pages preserve inputs, and their activity context pauses while hidden',async()=>{
  const {PageStack,usePageActive}=load('page-stack');let mounts=0;
  function Page(){const [draft,setDraft]=React.useState('');const active=usePageActive();React.useEffect(()=>{mounts++;},[]);return React.createElement('input',{value:draft,'data-active':active,onChange:e=>setDraft(e.target.value)});}
  let view;await act(async()=>{view=create(React.createElement(PageStack,{active:'one'},React.createElement(Page)));});
  const input=view.root.findByType('input');await act(async()=>input.props.onChange({target:{value:'保留输入'}}));
  await act(async()=>view.update(React.createElement(PageStack,{active:'two'},React.createElement('p',null,'第二页'))));
  assert.equal(input.props['data-active'],false);assert.equal(input.props.value,'保留输入');
  await act(async()=>view.update(React.createElement(PageStack,{active:'one'},React.createElement(Page))));
  assert.equal(input.props['data-active'],true);assert.equal(input.props.value,'保留输入');assert.equal(mounts,1);
  await act(async()=>view.unmount());
});

test('streaming passes complete Markdown and an appended reveal range, then retains output without a timer',()=>{
  const {StreamText}=load('stream-text',{'./Markdown':{Markdown:({text,reveal})=>React.createElement('article',{'data-reveal':reveal},text)}});let view;
  act(()=>{view=create(React.createElement(StreamText,{text:'第一段',active:true}));});
  act(()=>view.update(React.createElement(StreamText,{text:'第一段，新增',active:true})));
  assert.equal(text(view.root.findByType('article')),'第一段，新增');
  assert.equal(view.root.findByType('article').props['data-reveal'].from,'第一段'.length);
  act(()=>view.update(React.createElement(StreamText,{text:'第一段，新增',active:false})));
  assert.equal(text(view.root.findByType('article')),'第一段，新增');assert.equal(view.root.findByType('article').props['data-reveal'],undefined);act(()=>view.unmount());
});

test('floating chat shares real conversation, scopes drafts, handles IME and closes without cancelling',async()=>{
  const windowBus=new EventTarget();windowBus.innerWidth=340;windowBus.innerHeight=420;windowBus.setInterval=()=>1;windowBus.clearInterval=()=>{};global.window=windowBus;
  let callback;const invoked=[],calls=[];
  const state={revision:1,selected:'a',agents:[{id:'tebb',name:'小光'}],conversations:[{id:'a',title:'会话A'},{id:'b',title:'会话B'}],messages:[],runs:[],models:[]};
  const {PetChat}=load('pet-chat',{
    './composer-picker':load('composer-picker'),
    './api':{rpc:async(_i,method,args)=>{calls.push({method,args});return method==='load_task_progress'?{enabled:true}: {state};}},
    './chat-prototype-state':{runActive:r=>r.state==='running',RUN_LABELS:{running:'执行中'}},
    './stream-text':{StreamText:({text})=>React.createElement('p',null,text)},'./companion-ui':{AppIcon:()=>null},
    '@tauri-apps/api/core':{isTauri:()=>true,invoke:async(name,args)=>invoked.push({name,args})},
    '@tauri-apps/api/event':{listen:async(_name,cb)=>{callback=cb;return()=>{};}}
  });let view;await act(async()=>{view=create(React.createElement(PetChat,{backend:{port:1,token:'test'}}));});
  const input=()=>view.root.findByProps({'aria-label':'浮动聊天消息'});const choice=()=>view.root.findByProps({'aria-label':'当前会话'});
  await act(async()=>input().props.onChange({target:{value:'@小光 请帮我验证'}}));
  await act(async()=>input().props.onKeyDown({key:'Enter',nativeEvent:{isComposing:true},preventDefault(){throw Error('IME must not submit');}}));
  assert.equal(calls.filter(c=>c.method==='collaboration_action').length,0);
  await act(async()=>choice().props.onChange({target:{value:'b'}}));assert.equal(input().props.value,'');
  await act(async()=>choice().props.onChange({target:{value:'a'}}));assert.equal(input().props.value,'@小光 请帮我验证');
  await act(async()=>view.root.findByProps({'aria-label':'关闭对话框'}).props.onClick());
  assert.deepEqual(invoked.at(-1),{name:'set_pet_chat_visible',args:{visible:false}});
  await act(async()=>callback({payload:false}));await act(async()=>callback({payload:true}));
  assert.equal(input().props.value,'@小光 请帮我验证');
  await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));
  const sent=calls.find(c=>c.method==='collaboration_action').args.action;assert.equal(sent.conversation,'a');assert.equal(sent.mode,'auto');assert(sent.request_id);
  assert(!calls.some(c=>c.args?.action?.type==='cancel'));assert.equal(input().props.value,'');await act(async()=>view.unmount());
});

test('floating @ and / select authorized members and skills, and pin changes reflect actual native results',async()=>{
  const bus=new EventTarget();bus.setInterval=()=>1;bus.clearInterval=()=>{};global.window=bus;
  let failPin=false;const calls=[];
  const state={revision:1,selected:'a',agents:[{id:'tebb',name:'小光'},{id:'coder',name:'码可'}],
    conversations:[{id:'a',title:'会话A',members:['tebb']}],messages:[],runs:[],models:[],skills:[{id:'github-summary',description:'整理项目近况'}]};
  const {PetChat}=load('pet-chat',{
    './composer-picker':load('composer-picker'),
    './api':{rpc:async(_i,method,args)=>{calls.push({method,args});return method==='load_task_progress'?{enabled:true}:{state};}},
    './chat-prototype-state':{runActive:()=>false,RUN_LABELS:{}},'./stream-text':{StreamText:()=>null},'./companion-ui':{AppIcon:()=>null},
    '@tauri-apps/api/core':{isTauri:()=>true,invoke:async(name,args)=>{
      if(name==='get_pet_chat_state')return {visible:true,pinned:false};
      if(name==='set_pet_chat_pinned'){if(failPin)throw Error('pin failed');return args.pinned;}
    }},'@tauri-apps/api/event':{listen:async()=>()=>{}}
  });let view;await act(async()=>{view=create(React.createElement(PetChat,{backend:{port:1,token:'test'}}));});
  const input=()=>view.root.findByProps({'aria-label':'浮动聊天消息'});
  const key=value=>input().props.onKeyDown({key:value,nativeEvent:{},preventDefault(){}});
  await act(async()=>input().props.onChange({target:{value:'@',selectionStart:1}}));
  assert.equal(view.root.findAllByProps({role:'option'}).length,1);
  await act(async()=>key('Enter'));assert.equal(input().props.value,'@小光 ');assert.equal(calls.filter(c=>c.method==='collaboration_action').length,0);
  await act(async()=>input().props.onChange({target:{value:'@小光 /项目',selectionStart:7}}));
  assert(text(view.root.findByProps({role:'listbox'})).includes('github-summary'));
  await act(async()=>key('Enter'));assert.equal(input().props.value,'@小光 ');
  assert.equal(view.root.findByProps({'aria-label':'移除技能 github-summary'}).type,'button');
  await act(async()=>input().props.onChange({target:{value:'@小光 整理项目近况',selectionStart:11}}));
  await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));
  assert.deepEqual(calls.find(c=>c.method==='collaboration_action').args.action.skills,['github-summary']);
  await act(async()=>view.root.findByProps({'aria-label':'置顶对话框'}).props.onClick());
  assert.equal(view.root.findByProps({'aria-label':'取消置顶'}).props['aria-pressed'],true);
  failPin=true;await act(async()=>view.root.findByProps({'aria-label':'取消置顶'}).props.onClick());
  assert.equal(view.root.findByProps({'aria-label':'取消置顶'}).props['aria-pressed'],true);
  assert(text(view.root.findByProps({role:'alert'})).includes('pin failed'));
  await act(async()=>view.unmount());
});

test('floating toolbar creates a new conversation without cancelling work, preserves draft, and deduplicates a retry',async()=>{
  const bus=new EventTarget();bus.setInterval=()=>1;bus.clearInterval=()=>{};global.window=bus;
  const calls=[];let failed=true;
  let state={revision:1,selected:'old',agents:[{id:'tebb',name:'小缇'}],conversations:[{id:'old',title:'旧对话',coordinator:'tebb'}],messages:[],runs:[],models:[]};
  const {PetChat}=load('pet-chat',{
    './composer-picker':load('composer-picker'),'./api':{rpc:async(_i,method,args)=>{
      if(method==='load_task_progress')return {enabled:true};
      if(method==='collaboration_action'){
        calls.push(args.action);
        if(args.action.type==='new_chat'){if(failed){failed=false;throw Error('reply lost');}state={...state,revision:2,selected:'fresh',conversations:[...state.conversations,{id:'fresh',title:'新对话',coordinator:'tebb'}]};}
      }return {state};}},
    './chat-prototype-state':{runActive:()=>false,RUN_LABELS:{}},'./stream-text':{StreamText:()=>null},'./companion-ui':load('companion-ui'),
    '@tauri-apps/api/core':{isTauri:()=>false,invoke:async()=>{}},'@tauri-apps/api/event':{listen:async()=>()=>{}}
  });let view;await act(async()=>{view=create(React.createElement(PetChat,{backend:{port:1,token:'test'}}));});
  const input=()=>view.root.findByProps({'aria-label':'浮动聊天消息'});
  await act(async()=>input().props.onChange({target:{value:'保留旧草稿',selectionStart:5}}));
  const toolbar=view.root.findByProps({role:'toolbar'});assert.equal(toolbar.findAllByType('button').length,4);
  assert(toolbar.findAllByType('button').every(button=>button.findAllByType('svg').length===1));
  await act(async()=>view.root.findByProps({'aria-label':'创建新对话'}).props.onClick());
  await act(async()=>view.root.findByProps({'aria-label':'创建新对话'}).props.onClick());
  assert.equal(calls[0].request_id,calls[1].request_id);assert.equal(calls[0].agent_id,'tebb');assert.equal(calls[0].conversation,'old');
  assert.equal(view.root.findByProps({'aria-label':'当前会话'}).props.value,'fresh');assert.equal(input().props.value,'');
  await act(async()=>view.root.findByProps({'aria-label':'当前会话'}).props.onChange({target:{value:'old'}}));
  assert.equal(input().props.value,'保留旧草稿');assert(!calls.some(a=>a.type==='cancel'));await act(async()=>view.unmount());
});


test('floating clarification resumes the original root instead of creating another task',async()=>{
  const bus=new EventTarget();bus.setInterval=()=>1;bus.clearInterval=()=>{};global.window=bus;const calls=[];
  const state={revision:1,selected:'dm',agents:[{id:'tebb',name:'tebb'}],conversations:[{id:'dm',title:'对话',coordinator:'tebb'}],messages:[{id:'question',conversation:'dm',sender:'tebb',run:'root',text:'请说明目标'}],runs:[{id:'root',conversation:'dm',agent:'tebb',kind:'route',state:'needs_input',answer:'请说明目标',events:[],updates:[],calls:1}],models:[]};
  const {PetChat}=load('pet-chat',{'./stream-text':{StreamText:({text})=>React.createElement('p',null,text)},'./api':{rpc:async(_i,method,args)=>{if(method==='load_task_progress')return {enabled:true};if(method==='collaboration_action')calls.push(args.action);return {state};}},'@tauri-apps/api/core':{isTauri:()=>false,invoke:async()=>false},'@tauri-apps/api/event':{listen:async()=>()=>{}}});let view;
  await act(async()=>{view=create(React.createElement(PetChat,{backend:{port:1,token:'test'}}));});
  const supplement=view.root.findAllByType('button').find(node=>text(node)==='补充目标');assert(supplement);
  await act(async()=>supplement.props.onClick());await act(async()=>view.root.findByProps({'aria-label':'浮动聊天消息'}).props.onChange({target:{value:'把Hello改小写',selectionStart:10}}));
  await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));
  assert.equal(calls[0].run,'root');assert.equal(calls[0].conversation,'dm');await act(async()=>view.unmount());
});
