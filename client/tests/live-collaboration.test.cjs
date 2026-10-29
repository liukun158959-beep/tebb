const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { create, act } = require('react-test-renderer');
const markdownModules = {};
test.before(async () => {
  markdownModules['react-markdown'] = await import('react-markdown');
  markdownModules['remark-gfm'] = await import('remark-gfm');
});

function load(name, mocks = {}) {
  const base = path.resolve(__dirname, '../src/client', name);
  const filename = fs.existsSync(base+'.tsx') ? base+'.tsx' : base+'.ts';
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = new Module(filename, module); mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename)); const original = mod.require.bind(mod);
  mod.require = id => markdownModules[id] || mocks[id] || (id === './release' ? {CURRENT_RELEASE:{version:'0.2.3'}} : null) || (id.endsWith('.css') ? {} : id.startsWith('./') ? load(id.slice(2), mocks) : original(id));
  mod._compile(compiled, filename); return mod.exports;
}
function env() {
  const values = new Map(), timers = new Map(); let seq = 0;
  global.window = { location: { hash: '', pathname: '/client.html', search: '?preview=chat&live=1' }, history: { replaceState(_a, _b, url) { window.location.hash=''; window.cleaned=url; } }, setInterval(fn) { timers.set(++seq,fn);return seq; }, clearInterval(id) { timers.delete(id); } };
  global.sessionStorage = { getItem: key => values.get(key) || null, setItem: (key,value) => values.set(key,value) };
  global.document = { documentElement: { classList: { contains: () => true } }, activeElement: { focus() {} }, getElementById() {} };
  return { values, timers };
}
const strings = node => typeof node === 'string' ? node : (node.children || []).map(strings).join('');

test('pending replacement is labelled as a retained draft in both chat surfaces', async () => {
  env();
  const {RunProgress,DraftNotice}=load('run-progress');
  const run={id:'r',state:'running',answer_pending:true,events:[],status:'正在读取资料'};
  let view;await act(async()=>{view=create(React.createElement(RunProgress,{run}));});
  assert(strings(view.toJSON()).includes('保留上一段草稿'));
  await act(async()=>view.update(React.createElement(RunProgress,{run:{...run,answer_pending:false}})));
  assert(!strings(view.toJSON()).includes('保留上一段草稿'));
  await act(async()=>view.update(React.createElement(DraftNotice,{run})));
  assert.equal(view.root.findByProps({role:'status'}).children.join(''),'上一段草稿 · 正在继续处理');
  await act(async()=>view.unmount());
});

test('browser capability stays scoped, is removed from the address, and malformed grants are rejected', () => {
  env(); const mocks = { './api': {}, './chat-prototype': {}, './startup': {} };
  const { browserAccess } = load('live-collaboration', mocks);
  window.location.hash = '#collaboration='+encodeURIComponent(JSON.stringify({port:8765, token:'x'.repeat(48),scope:'other'}));
  assert.deepEqual(browserAccess(), {port:8765,token:'x'.repeat(48),scope:'collaboration'});
  assert.equal(window.location.hash,''); assert.equal(window.cleaned,'/client.html?preview=chat&live=1');
  assert.equal(browserAccess().scope,'collaboration');
  window.location.hash = '#collaboration='+encodeURIComponent(JSON.stringify({port:0,token:'invalid'}));
  assert.equal(browserAccess(),null);
});

test('real collaboration only submits explicit actions; polling does not create or replay work', async () => {
  const { timers } = env(); const calls=[]; let props;
  let state = {version:1,next:20,revision:1,selected:'group',agents:[],conversations:[],messages:[],runs:[],requests:[],notice:''};
  const rpc = async (_info,method,args) => { calls.push({method,args}); if(method==='collaboration_action') state={...state,revision:state.revision+1};return {ok:true,state}; };
  const { LiveCollaboration } = load('live-collaboration', { './api':{rpc}, './startup':{startupReady(){}}, './chat-prototype':{ChatPrototype: p=>{props=p;return React.createElement('div');}} });
  let view; await act(async()=>{view=create(React.createElement(LiveCollaboration,{info:{port:1,token:'test'}}));});
  assert.equal(calls.filter(c=>c.method==='collaboration_action').length,0);
  await act(async()=>{await props.live.act({type:'send',text:'真实消息',mode:'message',target:''});});
  const mutation=calls.find(c=>c.method==='collaboration_action').args.action;
  assert.equal(mutation.conversation,'group');assert.equal(mutation.text,'真实消息');assert(mutation.request_id);
  await act(async()=>{await [...timers.values()][0]();});
  assert.equal(calls.filter(c=>c.method==='collaboration_action').length,1);
  await act(async()=>view.unmount());assert.equal(timers.size,0);
});

test('an uncertain request retries with the same id; polling cannot erase its error or roll back newer state', async () => {
  const { timers }=env();let props;const ids=[];let attempts=0;
  const base={version:1,next:20,revision:2,selected:'group',agents:[],conversations:[],messages:[],runs:[],requests:[],notice:''};
  const rpc=async(_i,method,args)=>{if(method==='load_collaboration')return {ok:true,state:{...base,revision:1}};
    ids.push(args.action.request_id);if(++attempts===1)throw new Error('连接断开，结果未明');return {ok:true,state:base};};
  const { LiveCollaboration }=load('live-collaboration',{'./api':{rpc},'./startup':{startupReady(){}},'./chat-prototype':{ChatPrototype:p=>{props=p;return React.createElement('div');}}});
  let view;await act(async()=>{view=create(React.createElement(LiveCollaboration,{info:{port:1,token:'test'}}));});
  const action={type:'send',text:'不能重复的消息',mode:'task',target:''};
  await act(async()=>{assert.equal(await props.live.act(action),false);});
  await act(async()=>{await [...timers.values()][0]();});assert.match(props.live.error,/结果未明/);
  await act(async()=>{assert.equal(await props.live.act(action),true);});assert.equal(ids[0],ids[1]);
  await act(async()=>{await [...timers.values()][0]();});assert.equal(props.live.state.revision,2);
  await act(async()=>view.unmount());
});

test('real workspace renders actual output and sends actions without changing demo storage', async () => {
  env();global.localStorage={getItem(){throw new Error('real mode must not read demo storage');},setItem(){throw new Error('real mode must not write demo storage');}};
  const { initialDemo }=load('chat-prototype-state');const state=initialDemo();
  state.runs=[{...state.runs[0],state:'queued',kind:'task',model:'actual-model',calls:1,input_tokens:10,output_tokens:5}];state.messages=[];
  state.runs[0].citation_check={ok:true,pages_read:1,matched:[{url:'https://example.org/body',level:'body',truncated:true},{url:'https://example.org/summary',level:'snippet',truncated:false}],unverified:[]};
  const actions=[];const {ChatPrototype}=load('chat-prototype',{'./startup':{startupReady(){}}});let view;
  await act(async()=>{view=create(React.createElement(ChatPrototype,{live:{state,busy:false,error:'',act:async action=>{actions.push(action);return true;}}}));});
  assert(!strings(view.toJSON()).includes('真实协作验证'));
  await act(async()=>view.root.findByProps({'aria-label':'当前任务'}).props.onClick());assert(!strings(view.toJSON()).includes('推进一步'));
  assert(strings(view.toJSON()).includes('正文节选'));assert(strings(view.toJSON()).includes('仅搜索摘要'));assert(strings(view.toJSON()).includes('事实与结论仍需审阅'));
  await act(async()=>view.root.findByProps({'aria-label':'协作消息'}).props.onChange({target:{value:'真实提问'}}));
  await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));
  assert.equal(actions[0].text,'真实提问');assert.equal(actions[0].mode,'auto');
  await act(async()=>view.unmount());
});

test('a finished planner displays the collaboration delivery outcome instead of premature completion', async () => {
  for(const [status,label] of [['running','协作进行中'],['failed','协作未完成'],['succeeded','协作已交付']]) {
    env();const {initialDemo}=load('chat-prototype-state');const state=initialDemo();state.messages=[];
    state.runs=[{...state.runs[0],state:'succeeded',kind:'plan',delivery_state:status}];
    const {ChatPrototype}=load('chat-prototype');let view;
    await act(async()=>{view=create(React.createElement(ChatPrototype,{live:{state,busy:false,error:'',act:async()=>true}}));});
    await act(async()=>view.root.findByProps({'aria-label':'当前任务'}).props.onClick());
    assert(strings(view.root.findByProps({'data-demo-run':'r1'})).includes(label));
    await act(async()=>view.unmount());
  }
});
