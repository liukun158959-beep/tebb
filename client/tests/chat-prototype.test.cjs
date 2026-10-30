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
  const filename = fs.existsSync(base + '.tsx') ? base + '.tsx' : base + '.ts';
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = new Module(filename, module); mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = mod.require.bind(mod);
  mod.require = id => mocks[id] || markdownModules[id] || (id.endsWith('.css') ? {} : id === './release' ? { CURRENT_RELEASE: {version:'0.2.3'} } : id === './startup' ? { startupReady() {} } : id.startsWith('./') ? load(id.slice(2), mocks) : original(id));
  mod._compile(compiled, filename); return mod.exports;
}
const { initialDemo, demoReducer: reduce, restoreDemo, DEMO_STORAGE } = load('chat-prototype-state');
const send = (state, text, rest = {}) => reduce(state, { type: 'send', text, target: '', mode: 'message', ...rest });
const text = node => typeof node === 'string' ? node : (node.children || []).map(text).join('');
const button = (view, label) => view.root.findAllByType('button').find(node => node.props['aria-label'] === label || text(node) === label);
async function openGroup(view){
  await act(async()=>button(view,'新对话').props.onClick());
  await act(async()=>view.root.findByProps({'aria-label':'新对话伙伴'}).props.onChange({target:{value:'__group__'}}));
}

function environment() {
  const saved = new Map(), timers = new Map(); let seq = 0;
  global.localStorage = { getItem: key => saved.get(key) || null, setItem: (key, value) => saved.set(key, value) };
  global.window = { setInterval(fn) { timers.set(++seq, fn); return seq; }, clearInterval(id) { timers.delete(id); } };
  global.document = { documentElement: { classList: { contains: () => true, toggle() {} } }, activeElement: { focus() {} }, getElementById() {} };
  return { saved, timers };
}

test('new conversations retain one draft across navigation, commit on send and require explicit discard',async()=>{
 const {saved}=environment(),seed=initialDemo();seed.models=[{id:'chosen',model:'Chosen model'}];saved.set(DEMO_STORAGE,JSON.stringify(seed));
 const {ChatPrototype}=load('chat-prototype');let view;await act(async()=>{view=create(React.createElement(ChatPrototype));});
 const before=JSON.parse(saved.get(DEMO_STORAGE));
 await act(async()=>button(view,'新对话').props.onClick());
 assert.equal(JSON.parse(saved.get(DEMO_STORAGE)).conversations.length,before.conversations.length);
 await act(async()=>view.root.findByProps({'aria-label':'原型消息'}).props.onChange({target:{value:'请保留这份新的草稿'}}));
 await act(async()=>view.root.findByProps({'aria-label':'本轮模型'}).props.onChange({target:{value:'chosen'}}));
 await act(async()=>view.root.findByProps({'data-conversation':'dm-writer'}).props.onClick());
 assert.equal(view.root.findByProps({'aria-label':'原型消息'}).props.value,'');
 await act(async()=>button(view,'新对话').props.onClick());
 assert.equal(view.root.findByProps({'aria-label':'原型消息'}).props.value,'请保留这份新的草稿');
 assert.equal(view.root.findByProps({'aria-label':'本轮模型'}).props.value,'chosen');
 await act(async()=>button(view,'新对话').props.onClick());
 assert.equal(JSON.parse(saved.get(DEMO_STORAGE)).conversations.length,before.conversations.length);
 await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));
 const after=JSON.parse(saved.get(DEMO_STORAGE));
 assert.equal(after.conversations.length,before.conversations.length+1);
 assert.equal(after.messages.filter(m=>m.conversation===after.selected&&m.sender==='user').length,1);
 assert.equal(view.root.findByProps({'aria-label':'原型消息'}).props.value,'');
 assert.equal(view.root.findAllByProps({'aria-label':'新对话草稿'}).length,0);
 await act(async()=>button(view,'新对话').props.onClick());
 await act(async()=>view.root.findByProps({'aria-label':'原型消息'}).props.onChange({target:{value:'暂时保留'}}));
 await act(async()=>button(view,'放弃新对话草稿').props.onClick());
 await act(async()=>button(view,'保留草稿').props.onClick());
 assert.equal(view.root.findByProps({'aria-label':'原型消息'}).props.value,'暂时保留');
 await act(async()=>button(view,'放弃新对话草稿').props.onClick());
 await act(async()=>button(view,'确认放弃草稿').props.onClick());
 assert.deepEqual(JSON.parse(saved.get(DEMO_STORAGE)).conversations,after.conversations);
 await act(async()=>button(view,'新对话').props.onClick());
 assert.equal(view.root.findByProps({'aria-label':'原型消息'}).props.value,'');
 await act(async()=>view.unmount());
});

test('uncertain first sends preserve the frozen body across navigation and reconcile without an automatic resend',async()=>{
 environment();const seed=initialDemo(),actions=[];const {ChatPrototype}=load('chat-prototype');let view;
 const live={state:seed,busy:false,error:'回执丢失',act:async action=>{actions.push(structuredClone(action));return false;}};
 await act(async()=>{view=create(React.createElement(ChatPrototype,{live}));});
 await act(async()=>button(view,'新对话').props.onClick());
 await act(async()=>view.root.findByProps({'aria-label':'协作消息'}).props.onChange({target:{value:'发送结果需要核对'}}));
 await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));
 const first=actions.find(a=>a.type==='send');
 assert.match(first.conversation,/^dm-[a-f0-9]{32}$/);assert.deepEqual(first.new_chat,{agent_id:'tebb',project_id:''});
 assert.equal(view.root.findByProps({'aria-label':'协作消息'}).props.readOnly,true);
 assert.equal(view.root.findByProps({'aria-label':'新对话伙伴'}).props.disabled,true);
 await act(async()=>view.root.findByProps({'data-conversation':'dm-writer'}).props.onClick());
 await act(async()=>button(view,'新对话').props.onClick());
 assert.equal(view.root.findByProps({'aria-label':'协作消息'}).props.value,first.text);
 await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));
 assert.deepEqual(actions.filter(a=>a.type==='send'),[first,first]);
 const committed=reduce(seed,first);committed.revision=2;
 await act(async()=>view.update(React.createElement(ChatPrototype,{live:{...live,state:committed,error:''}})));
 assert.equal(view.root.findAllByProps({'aria-label':'新对话草稿'}).length,0);
 assert.equal(view.root.findByType('h1').children[0],first.text);
 assert.equal(actions.filter(a=>a.type==='send').length,2);
 assert.equal(committed.conversations.length,seed.conversations.length+1);
 await act(async()=>view.unmount());
});

test('composer grows, caps overflow and shrinks after clearing, including width changes',()=>{
  environment();let resized;global.ResizeObserver=class{constructor(cb){resized=cb;}observe(){}disconnect(){}};
  global.getComputedStyle=()=>({minHeight:'44px',maxHeight:'180px',borderTopWidth:'1px',borderBottomWidth:'1px'});
  let content=40;const node={style:{},get scrollHeight(){return content;},getBoundingClientRect:()=>({width:500})};
  const {useAutoGrowInput}=load('auto-grow-input');
  function Input({value}){const ref=React.useRef(null);useAutoGrowInput(ref,value,'conversation');return React.createElement('textarea',{ref,value,readOnly:true});}
  let view;act(()=>{view=create(React.createElement(Input,{value:''}),{createNodeMock:()=>node});});
  assert.equal(node.style.height,'44px');content=130;
  act(()=>view.update(React.createElement(Input,{value:'多行内容'})));assert.equal(node.style.height,'132px');assert.equal(node.style.overflowY,'hidden');
  content=400;act(()=>resized([{contentRect:{width:260}}]));assert.equal(node.style.height,'180px');assert.equal(node.style.overflowY,'auto');
  content=40;act(()=>view.update(React.createElement(Input,{value:''})));assert.equal(node.style.height,'44px');assert.equal(node.style.overflowY,'hidden');
  act(()=>view.unmount());delete global.ResizeObserver;delete global.getComputedStyle;
});

test('incremental sync preserves unchanged history, applies removals and refuses stale action replies',()=>{
  const {CollaborationSync}=load('collaboration-sync');const sync=new CollaborationSync();
  const state={...initialDemo(),revision:1};sync.update({state,sync:'one'});
  const previous=sync.state.messages[0];const added={id:'stream',conversation:state.selected,sender:'tebb',text:'第一段'};
  const next=sync.update({sync:'two',patch:{base:'one',values:{revision:2},collections:{messages:{upsert:[added],order:[previous.id,added.id]}}}});
  assert.equal(next.messages[0],previous);assert.equal(next.messages[1].text,'第一段');assert.equal(next.messages.length,2);
  sync.snapshot({...next,revision:3,selected:'dm-writer'});
  assert.equal(sync.update({sync:'old',patch:{base:'two',values:{revision:2,selected:'dm-tebb'},collections:{}}}),null);
  assert.equal(sync.state.selected,'dm-writer');assert.equal(sync.sync,'');
  assert.equal(sync.update({state,sync:'old'}),null);assert.equal(sync.state.revision,3);
});

test('a frontend hot reload stays compatible with the previous backend without hiding real failures',async()=>{
  const calls=[];const {loadCollaboration}=load('collaboration-sync',{'./api':{rpc:async(_info,_method,args)=>{
    calls.push(args);if('since' in args)throw new Error("Bridge.load_collaboration() got an unexpected keyword argument 'since'");return {state:initialDemo()};
  }}});
  const info={port:108,token:'fixture'};await loadCollaboration(info,'baseline');await loadCollaboration(info,'');
  assert.deepEqual(calls,[{since:'baseline'},{},{}]);
  const broken=load('collaboration-sync',{'./api':{rpc:async()=>{throw new Error('database unavailable');}}});
  await assert.rejects(()=>broken.loadCollaboration(info,''),/database unavailable/);
});

test('normal chat shows one root task with expandable real child progress',()=>{
  environment();const state=initialDemo();state.selected='dm-tebb';state.revision=1;
  state.runs=[{id:'route',conversation:state.selected,agent:'tebb',state:'succeeded',kind:'route',events:[],updates:[],applied:0,title:'路由',step:0},
    {id:'task',parent:'route',conversation:state.selected,agent:'tebb',state:'running',kind:'task',title:'研究',answer:'正在整理来源。',step:0,updates:[],applied:0,status:'正在调用：web_search',events:['正在思考与组织回答。','tool_start: web_search Agent docs','tool_end: web_search success']}];
  state.messages=[{id:'route-message',conversation:state.selected,sender:'tebb',run:'route',text:'已开始处理，任务进度会持续更新。'},
    {id:'task-message',conversation:state.selected,sender:'tebb',run:'task',text:'正在整理来源。'}];
  const {ChatPrototype}=load('chat-prototype');let view;
  act(()=>{view=create(React.createElement(ChatPrototype,{developerMode:false,live:{state,act:async()=>true,busy:false,error:''}}));});
  const chat=view.root.findByProps({'aria-label':'协作聊天记录'});
  assert.equal(chat.findAll(node=>node.type==='article').length,1);act(()=>view.root.findByProps({'aria-label':'当前任务'}).props.onClick());const tasks=view.root.findAll(node=>node.props.className?.includes?.('cp-task')&&node.type==='article');assert.equal(tasks.length,1);
  assert(text(view.root).includes('正在思考与组织回答。'));assert(text(view.root).includes('联网搜索 · 开始'));assert(text(view.root).includes('联网搜索 · 结束'));
  assert(text(view.root).includes('正在整理来源。'));act(()=>view.unmount());
});

test('chat stays available during work; task supplements are scoped and applied at the next step', () => {
  const first = initialDemo(), original = JSON.stringify(first);
  let state = send(first, '进度如何？');
  assert.equal(state.messages.length, first.messages.length + 2);
  assert.equal(state.runs.length, 2); assert.equal(state.runs[0].step, 0);
  state = send(state, '重点看部署成本', { run: 'r1' });
  assert.deepEqual(state.runs[0].updates, ['重点看部署成本']); assert.equal(state.runs[0].applied, 0);
  assert.deepEqual(state.runs[1].updates, []);
  state = reduce(state, { type: 'advance' }); assert.equal(state.runs[0].applied, 1);
  state = reduce(state, { type: 'select', id: 'dm-writer' });
  const rejected = send(state, '跨会话注入', { run: 'r1' });
  assert.match(rejected.notice, /不属于当前会话/); assert.equal(rejected.messages.length, state.messages.length);
  assert.equal(JSON.stringify(first), original); // Reducer replay must not mutate historical snapshots.
  const rejectedMention = send(state, '@研究员 请帮忙', { mode: 'task' });
  assert.match(rejectedMention.notice, /不在当前会话/); assert.equal(rejectedMention.runs.length, state.runs.length);
});

test('independent runs advance together; stopping one cancels its dependents but preserves other conversations', () => {
  let state = reduce(initialDemo(), { type: 'select', id: 'dm-tebb' });
  state = send(state, '另一个任务', { mode: 'task' }); const independent = state.runs.at(-1).id;
  state = reduce(state, { type: 'advance' });
  assert.equal(state.runs.find(run => run.id === 'r1').step, 1);
  assert.equal(state.runs.find(run => run.id === independent).step, 1);
  state = reduce(state, { type: 'cancel', id: 'r1' });
  assert.equal(state.runs[0].state, 'cancelled'); assert.equal(state.runs[1].state, 'cancelled');
  assert.equal(state.runs.find(run => run.id === independent).state, 'running');
  const count = state.messages.length;
  state = reduce(state, { type: 'cancel', id: 'r1' }); assert.equal(state.messages.length, count);
  state = reduce(state, { type: 'advance' }); assert.equal(state.runs[0].step, 1);
});

test('handoff is visible, completion creates one artifact and delivery decisions cannot duplicate', () => {
  let state = initialDemo();
  for (let i = 0; i < 3; i++) state = reduce(state, { type: 'advance' });
  assert.equal(state.runs[0].state, 'succeeded'); assert.equal(state.runs[1].state, 'waiting');
  state = reduce(state, { type: 'advance' });
  assert.equal(state.runs[1].state, 'running'); assert.equal(state.runs[1].step, 0);
  for (let i = 0; i < 3; i++) state = reduce(state, { type: 'advance' });
  assert.equal(state.runs[1].state, 'succeeded'); assert.match(state.runs[1].artifact, /未进行联网检索/);
  assert.equal(state.messages.filter(message => message.run === 'r2' && message.kind === 'result').length, 1);
  state = reduce(state, { type: 'advance' });
  assert.equal(state.messages.filter(message => message.run === 'r2' && message.kind === 'result').length, 1);
  state = reduce(state, { type: 'request', run: 'r2' }); state = reduce(state, { type: 'request', run: 'r2' });
  assert.equal(state.requests.length, 1);
  const request = state.requests[0].id;
  state = reduce(state, { type: 'decide', id: request, approved: true }); const count = state.messages.length;
  assert.match(state.messages.at(-1).text, /没有实际向飞书发送/);
  state = reduce(state, { type: 'decide', id: request, approved: true }); assert.equal(state.messages.length, count);
});

test('agent identities, group routing and private memory survive restore without leaking into messages', () => {
  let state = initialDemo();
  const privateText = '只属于这个 Agent 的演示记忆';
  state = reduce(state, { type: 'agent', agent: { ...state.agents[1], name: '技术观察员', memory: privateText } });
  assert.equal(state.conversations.find(c => c.id === 'dm-research').title, '技术观察员');
  state = reduce(state, { type: 'group', title: '新研究群', goal: '比较部署方案', members: ['tebb', 'research', 'research'] });
  assert.deepEqual(state.conversations.at(-1).members, ['tebb', 'research']);
  state = send(state, '@技术观察员 单独研究', { mode: 'task' });
  assert.equal(state.runs.at(-1).agent, 'research'); assert.equal(state.runs.at(-1).dependency, undefined);
  assert(!JSON.stringify(state.messages).includes(privateText));
  const restored = restoreDemo(JSON.stringify(state)); assert.deepEqual(restored, JSON.parse(JSON.stringify(state)));
  for (const raw of ['broken', '{}', JSON.stringify({ ...state, agents: [null] }), JSON.stringify({ ...state, runs: [{ ...state.runs[0], agent: 'missing' }] })]) {
    assert.deepEqual(restoreDemo(raw), initialDemo());
  }
});

test('UI sends while running, preserves per-conversation drafts and targets supplements to the selected task', async () => {
  const { saved } = environment(); const { ChatPrototype } = load('chat-prototype'); let view;
  await act(async () => { view = create(React.createElement(ChatPrototype, {standalone:true})); });
  const composer = () => view.root.findByProps({ 'aria-label': '原型消息' });
  await act(async () => composer().props.onChange({ target: { value: '运行中也能聊天' } }));
  assert.equal(button(view, '发送↑').props.disabled, false);
  await act(async () => view.root.findByType('form').props.onSubmit({ preventDefault() {} }));
  assert(text(view.toJSON()).includes('当前有 2 项后台任务'));
  await act(async () => composer().props.onChange({ target: { value: '群草稿' } }));
  const dm = () => view.root.findAllByType('button').find(node => node.props.className?.includes('cp-conversation') && node.props['data-chat-partner'] === 'research');
  await act(async () => dm().props.onClick()); assert.equal(composer().props.value, '');
  await act(async () => composer().props.onChange({ target: { value: '私聊草稿' } }));
  const group = () => view.root.findAllByType('button').find(node => node.props.className?.includes('cp-conversation') && text(node).includes('AI 研究室'));
  await act(async () => group().props.onClick()); assert.equal(composer().props.value, '群草稿');
  await act(async () => button(view, '当前任务').props.onClick());
  const run = view.root.findByProps({ 'data-demo-run': 'r1' });
  await act(async () => run.findAllByType('button').find(node => text(node) === '补充要求').props.onClick());
  await act(async () => composer().props.onChange({ target: { value: '优先关注部署成本' } }));
  await act(async () => view.root.findByType('form').props.onSubmit({ preventDefault() {} }));
  assert(text(view.toJSON()).includes('等待下一步'));
  await act(async () => button(view, '推进一步 →').props.onClick());
  const persisted = JSON.parse(saved.get(DEMO_STORAGE));
  assert.equal(persisted.runs[0].applied, 1); assert.equal(persisted.runs[1].updates.length, 0);
  await act(async () => view.unmount());
});

test('UI creates groups and Agent settings; reset is explicit and does not touch unrelated storage', async () => {
  const { saved } = environment(); saved.set('real-chat-sentinel', 'keep');
  const { ChatPrototype } = load('chat-prototype'); let view;
  await act(async () => { view = create(React.createElement(ChatPrototype, {standalone:true})); });
  await openGroup(view);
  let modal = view.root.findByProps({ role: 'dialog' });
  await act(async () => modal.findAllByType('input').find(node => node.props.type !== 'checkbox').props.onChange({ target: { value: '技术观察' } }));
  await act(async () => modal.findByType('form').props.onSubmit({ preventDefault() {} }));
  assert(view.root.findByType('h1').children.includes('技术观察'));
  await act(async () => button(view, '伙伴').props.onClick());
  await act(async () => button(view, '创建 Agent').props.onClick());
  modal = view.root.findByProps({ role: 'dialog' });
  await act(async () => modal.findAllByType('input').find(node => node.props.maxLength === 24).props.onChange({ target: { value: '审阅者' } }));
  await act(async () => modal.findByType('form').props.onSubmit({ preventDefault() {} }));
  assert(JSON.parse(saved.get(DEMO_STORAGE)).agents.some(agent => agent.name === '审阅者'));
  await act(async () => button(view, '重置演示').props.onClick());
  assert.equal(JSON.parse(saved.get(DEMO_STORAGE)).agents.length, 4);
  await act(async () => button(view, '确认重置原型').props.onClick());
  assert.equal(JSON.parse(saved.get(DEMO_STORAGE)).agents.length, 3); assert.equal(saved.get('real-chat-sentinel'), 'keep');
  await act(async () => view.unmount());
});

test('automatic simulation is opt-in and timers stop when the view closes', async () => {
  const { timers } = environment(); const { ChatPrototype } = load('chat-prototype'); let view;
  await act(async () => { view = create(React.createElement(ChatPrototype, {standalone:true})); });
  assert.equal(timers.size, 0);
  await act(async () => button(view, '自动演示').props.onClick()); assert.equal(timers.size, 1);
  await act(async () => [...timers.values()][0]());
  await act(async () => button(view, '暂停').props.onClick()); assert.equal(timers.size, 0);
  await act(async () => button(view, '自动演示').props.onClick());
  await act(async () => view.unmount()); assert.equal(timers.size, 0);
});

test('Chinese IME Enter and Shift+Enter do not submit a draft', async () => {
  const { saved } = environment(); const { ChatPrototype } = load('chat-prototype'); let view;
  await act(async () => { view = create(React.createElement(ChatPrototype, {standalone:true})); });
  const composer = () => view.root.findByProps({ 'aria-label': '原型消息' });
  await act(async () => composer().props.onChange({ target: { value: '中文输入' } }));
  const before = JSON.parse(saved.get(DEMO_STORAGE)).messages.length;
  for (const rest of [{ nativeEvent: { isComposing: true } }, { keyCode: 229 }, { shiftKey: true }]) {
    await act(async () => composer().props.onKeyDown({ key: 'Enter', shiftKey: false, nativeEvent: {}, preventDefault() { throw new Error('must not submit'); }, ...rest }));
    assert.equal(JSON.parse(saved.get(DEMO_STORAGE)).messages.length, before);
  }
  let prevented = false;
  await act(async () => composer().props.onKeyDown({ key: 'Enter', nativeEvent: {}, preventDefault() { prevented = true; } }));
  assert.equal(prevented, true); assert.equal(JSON.parse(saved.get(DEMO_STORAGE)).messages.length, before + 2);
  await act(async () => view.unmount());
});

test('standalone preview loads no real App; normal startup still loads the real App', async () => {
  for (const preview of [true, false]) {
    environment(); global.window.location = { search: preview ? '?preview=chat' : '' };
    global.document.getElementById = () => ({});
    const filename = path.resolve(__dirname, '../src/client/main.tsx');
    const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
      module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
    const mod = new Module(filename, module); mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename));
    const original = mod.require.bind(mod); let element, appLoads = 0, previewLoads = 0, receivedProps;
    mod.require = id => {
      if (id.endsWith('.css')) return {};
      if (id === 'react-dom/client') return { createRoot: () => ({ render(value) { element = value; } }) };
      if (id === './render-boundary') return load('render-boundary');
      if (id === './App') { appLoads++; return { App: () => React.createElement('div', { 'data-real-app': '' }) }; }
      if (id === './chat-prototype') { previewLoads++; return { ChatPrototype: props => { receivedProps = props; return React.createElement('div', { 'data-preview': '' }); } }; }
      return original(id);
    };
    mod._compile(compiled, filename); let view;
    await act(async () => { view = create(element); });
    assert.equal(appLoads, preview ? 0 : 1); assert.equal(previewLoads, preview ? 1 : 0);
    if (preview) assert.equal(receivedProps.standalone, true);
    await act(async () => view.unmount());
  }
});


const contextEvent = (rest = {}) => ({ preventDefault() {}, currentTarget: { getBoundingClientRect: () => ({ right: 200, top: 100 }), focus() {} }, clientX: 120, clientY: 100, ...rest });

test('context menu manages conversations; memory has one dedicated composer entry', async () => {
  const { saved } = environment(); const { ChatPrototype } = load('chat-prototype'); let view;
  await act(async () => { view = create(React.createElement(ChatPrototype, {standalone:true})); });
  const dm = () => view.root.findByProps({ 'data-chat-partner': 'research' });
  await act(async () => dm().props.onContextMenu(contextEvent()));
  assert(button(view, '删除对话')); assert(!button(view, '调试伙伴记忆'));
  await act(async () => view.root.findByProps({ role: 'menu', 'aria-label':'会话操作' }).props.onKeyDown({ key: 'Escape', preventDefault() {} }));
  assert.equal(view.root.findAllByProps({ role: 'menu', 'aria-label':'会话操作' }).length, 0);
  for(let i=0;i<5;i++) await act(async () => view.root.findByProps({'data-product-version':true}).props.onClick());
  assert.equal(saved.get('tebb.developer-mode'), 'true');
  await act(async () => dm().props.onContextMenu(contextEvent()));
  assert(!button(view, '调试伙伴记忆'));
  assert(button(view, '记忆'));
  for(let i=0;i<5;i++) await act(async () => view.root.findByProps({'data-product-version':true}).props.onClick());
  await act(async () => dm().props.onKeyDown(contextEvent({ key: 'F10', shiftKey: true })));
  assert(!button(view, '调试伙伴记忆'));
  await act(async () => view.root.findByProps({ className: 'cp-menu-dismiss' }).props.onMouseDown());
  assert.equal(view.root.findAllByProps({ role: 'menu', 'aria-label':'会话操作' }).length, 0);
  await act(async () => view.unmount());
});

test('delete inactive DM removes history, preserves companions and opens a fresh conversation', async () => {
  const { saved } = environment(); const { ChatPrototype } = load('chat-prototype'); let view;
  await act(async () => { view = create(React.createElement(ChatPrototype, {standalone:true})); });
  let dm = view.root.findByProps({ 'data-chat-partner': 'writer' });
  await act(async () => dm.props.onClick());
  await act(async () => view.root.findByProps({ 'aria-label': '原型消息' }).props.onChange({ target: { value: '待删除消息' } }));
  await act(async () => view.root.findByType('form').props.onSubmit({ preventDefault() {} }));
  await act(async () => dm.props.onContextMenu(contextEvent()));
  await act(async () => button(view, '删除对话').props.onClick());
  await act(async () => button(view, '确认删除').props.onClick());
  let state = JSON.parse(saved.get(DEMO_STORAGE));
  assert(!state.messages.some(m => m.text === '待删除消息'));
  assert.equal(state.agents.length, 3);
  await act(async () => button(view,'伙伴').props.onClick());
  const countBefore=state.conversations.length;
  await act(async () => view.root.findByProps({'data-companion':'writer'}).props.onClick());
  state = JSON.parse(saved.get(DEMO_STORAGE));
  assert.equal(state.conversations.length,countBefore);
  assert.equal(view.root.findByProps({'aria-label':'新对话伙伴'}).props.value,'writer');
  assert.equal(view.root.findByType('h1').children[0], '新对话');
  await act(async () => view.unmount());
});

test('active conversation deletion is disabled and normal companion settings hide memory', async () => {
  environment(); const { ChatPrototype } = load('chat-prototype'); let view;
  await act(async () => { view = create(React.createElement(ChatPrototype, {standalone:true})); });
  const group = view.root.findAllByType('button').find(b => b.props.className?.includes('cp-conversation') && text(b).includes('AI 研究室'));
  await act(async () => group.props.onContextMenu(contextEvent()));
  await act(async () => button(view, '删除对话').props.onClick());
  assert.equal(button(view, '确认删除').props.disabled, true);
  await act(async () => button(view, '取消').props.onClick());
  await act(async () => button(view, '伙伴').props.onClick());
  await act(async () => button(view, '创建 Agent').props.onClick());
  const modal = view.root.findByProps({ role: 'dialog' });
  assert(!text(modal).includes('Agent 私有记忆'));
  assert.equal(modal.findAllByType('svg').length, 4);
  await act(async () => view.unmount());
});

test('Markdown formats messages, tables, fenced code and artifacts, and rejects executable links and HTML', async () => {
  environment(); const { ChatPrototype } = load('chat-prototype'); let state = initialDemo();
  const md = '# 标题\n\n**重点**\n\n| 项目 | 结果 |\n| --- | --- |\n| A | B |\n\n```js\nconst a = 1;\n```\n\n[来源](https://example.org) [危险](javascript:alert%281%29)\n\n<script>alert(1)</script>';
  state.messages = [{ id: 'md1', conversation: state.selected, sender: 'writer', text: md, kind: 'result', run: 'r1' }];
  state.runs[0].state = 'succeeded';
  state.runs[0].artifact = md;
  let view; await act(async () => { view = create(React.createElement(ChatPrototype, { standalone: true, live: { state, busy: false, error: '', act: async () => true } })); });
  assert.equal(view.root.findAllByType('table').length, 1);
  assert(view.root.findAllByType('strong').some(n => text(n) === '重点'));
  assert(view.root.findAllByType('code').some(n => text(n).includes('const a = 1;')));
  assert.equal(view.root.findAllByType('script').length, 0);
  assert(!view.root.findAllByType('a').some(n => /^javascript:/i.test(n.props.href || '')));
  const link = view.root.findAllByType('a').find(n => n.props.href === 'https://example.org/');
  assert.equal(link.props.target, '_blank');
  await act(async () => button(view, '当前任务').props.onClick());
  assert(view.root.findByProps({ 'data-demo-run': 'r1' }));
  await act(async () => view.root.findByProps({ className: 'cp-artifact-link' }).props.onClick());
  const artifact = view.root.findByProps({ role: 'dialog' });
  assert.equal(artifact.findAllByType('table').length, 1);
  assert.equal(artifact.findAllByType('script').length, 0);
  assert(artifact.findAllByType('code').some(n => text(n).includes('const a = 1;')));
  await act(async () => view.unmount());
});

test('composer keeps a plain draft without preview controls and inserts sources without submitting', async () => {
  const { saved } = environment(); const { ChatPrototype } = load('chat-prototype'); let view;
  await act(async () => { view = create(React.createElement(ChatPrototype, {standalone:true})); });
  const composer = () => view.root.findByProps({ 'aria-label': '原型消息' });
  const before = JSON.parse(saved.get(DEMO_STORAGE)).messages.length;
  await act(async () => composer().props.onChange({ target: { value: '**草稿重点**' } }));
  assert.equal(button(view, '预览 Markdown'), undefined);
  assert.equal(button(view, '插入代码'), undefined);
  assert.equal(view.root.findAllByProps({ 'aria-label': 'Markdown 草稿预览' }).length, 0);
  assert(!composer().props.hidden);
  assert.equal(JSON.parse(saved.get(DEMO_STORAGE)).messages.length, before);
  assert.equal(composer().props.value, '**草稿重点**');
  await act(async () => view.update(React.createElement(ChatPrototype, { composeRequest: { id: 1, text: 'https://example.org/source' } })));
  assert(composer().props.value.endsWith('https://example.org/source'));
  assert.equal(JSON.parse(saved.get(DEMO_STORAGE)).messages.length, before);
  await act(async () => view.unmount());
});

test('streaming output renders Markdown immediately, reveals new text and keeps safe rich output on completion', async () => {
  const { StreamText } = load('stream-text'); let view;
  const first = '# 研究结论\n\n**重点**';
  await act(async () => { view = create(React.createElement(StreamText, { text: first, active: true, externalLinks: true })); });
  assert.equal(text(view.root.findByType('h1')), '研究结论');
  assert.equal(text(view.root.findByType('strong')), '重点');
  const next = first + '，补充内容\n\n| 项目 | 结果 |\n| --- | --- |\n| A | B |\n\n```js\nconst a = 1;\n```\n\n[来源](https://example.org) [危险](javascript:alert%281%29)\n\n<script>alert(1)</script>';
  await act(async () => view.update(React.createElement(StreamText, { text: next, active: true, externalLinks: true })));
  assert.equal(view.root.findAllByType('table').length, 1);
  assert(view.root.findAllByType('code').some(n => text(n).includes('const a = 1;')));
  assert(view.root.findAllByType('span').filter(n => n.props.className?.includes('tebb-stream-new')).every(n => !text(n).includes('研究结论') && !text(n).includes('重点')));
  assert(view.root.findAllByType('span').some(n => n.props.className?.includes('tebb-stream-new') && text(n).includes('补充内容')));
  assert.equal(view.root.findAllByType('script').length, 0);
  assert(!view.root.findAllByType('a').some(n => /^javascript:/i.test(n.props.href || '')));
  assert.equal(view.root.findAllByProps({ 'aria-label': '正在输出' }).length, 1);
  await act(async () => view.update(React.createElement(StreamText, { text: next, active: false, externalLinks: true })));
  assert.equal(view.root.findAllByType('table').length, 1);
  assert.equal(view.root.findAllByProps({ 'aria-label': '正在输出' }).length, 0);
  assert.equal(view.root.findAllByType('span').filter(n => n.props.className?.includes('tebb-stream-new')).length, 0);
  assert.equal(view.root.findAllByType('a').find(n => n.props.href === 'https://example.org/').props.target, '_blank');
  await act(async () => view.unmount());
});

test('sending keeps task details closed, preserves raw Markdown and leaves progress reachable', async () => {
  environment(); const { ChatPrototype } = load('chat-prototype'); const state = initialDemo(), actions = [];
  let view; await act(async () => { view = create(React.createElement(ChatPrototype, { live: { state, busy: false, error: '', act: async action => { actions.push(action); return true; } } })); });
  assert.equal(view.root.findByProps({ 'aria-label': '协作详情' }).props['aria-hidden'], true);
  const input = view.root.findByProps({ 'aria-label': '协作消息' });
  await act(async () => input.props.onChange({ target: { value: '**完整消息**\n\n```py\nprint(1)\n```' } }));
  await act(async () => view.root.findByType('form').props.onSubmit({ preventDefault() {} }));
  assert.equal(actions[0].text, '**完整消息**\n\n```py\nprint(1)\n```');
  assert.equal(input.props.value, '');
  assert.equal(view.root.findByProps({ 'aria-label': '协作详情' }).props['aria-hidden'], true);
  await act(async () => button(view, '查看运行任务').props.onClick());
  assert.equal(view.root.findByProps({ 'aria-label': '协作详情' }).props['aria-hidden'], false);
  await act(async () => view.unmount());
});

test('restoring legacy companion names keeps custom names, model, permissions and private preferences', () => {
  const state = initialDemo(); delete state.companionVersion;
  state.agents[0].name = 'tebb'; state.agents[1].name = '我自己的伙伴'; state.agents[1].memory = '私有偏好';
  state.agents[2].name = '写作者'; delete state.agents[2].avatar;
  const next = restoreDemo(JSON.stringify(state));
  assert.deepEqual(next.agents.map(a => a.name), ['tebb · 通用助手', '我自己的伙伴', '写作助手']);
  assert.equal(next.agents[1].memory, '私有偏好');
  assert.equal(next.agents[2].avatar, 'cat');
  next.agents[2].name = '写作者';
  assert.equal(restoreDemo(JSON.stringify(next)).agents[2].name, '写作者');
});

test('projects restore old history and keep conversation membership across reloads', () => {
  const legacy = initialDemo(); delete legacy.projects;
  legacy.runs.forEach(r=>r.state='cancelled');
  const migrated = restoreDemo(JSON.stringify(legacy));
  assert.deepEqual(migrated.projects, []); assert.deepEqual(migrated.messages, legacy.messages);
  let state = reduce(migrated, {type:'create_project',name:'Atlas'}); const pid=state.projects[0].id;
  state = reduce(state, {type:'move_conversation',id:'group-research',project_id:pid});
  state = reduce(state, {type:'rename_project',id:pid,name:'Atlas 开发'});
  const restored = restoreDemo(JSON.stringify(state));
  assert.equal(restored.projects[0].name,'Atlas 开发');
  assert.equal(restored.conversations.find(c=>c.id==='group-research').project_id,pid);
  assert.deepEqual(restored.messages,legacy.messages); assert.deepEqual(restored.runs,legacy.runs);
});

test('sidebar creates, renames and deletes projects, and moving finished conversations preserves drafts and work', async () => {
  const {saved}=environment();const {ChatPrototype}=load('chat-prototype');let view;
  const seed=initialDemo();seed.runs.forEach(r=>r.state='cancelled');saved.set(DEMO_STORAGE,JSON.stringify(seed));
  await act(async()=>{view=create(React.createElement(ChatPrototype));});
  const state=()=>JSON.parse(saved.get(DEMO_STORAGE));
  const submitModal=async()=>act(async()=>view.root.findByProps({role:'dialog'}).findByType('form').props.onSubmit({preventDefault(){}}));
  const initial=state();
  await act(async()=>view.root.findByProps({'aria-label':'原型消息'}).props.onChange({target:{value:'保留我的草稿'}}));
  await act(async()=>button(view,'打开文件夹').props.onClick());
  await act(async()=>view.root.findByProps({'aria-label':'项目名称'}).props.onChange({target:{value:'Atlas'}}));
  await submitModal();const pid=state().projects[0].id;
  assert(view.root.findByProps({'data-chat-project':pid}));
  await act(async()=>button(view,'AI 研究室的会话操作').props.onClick({preventDefault(){},currentTarget:{getBoundingClientRect:()=>({right:100,top:100})},clientX:100,clientY:100}));
  await act(async()=>button(view,'移动到项目').props.onClick());
  await act(async()=>view.root.findByProps({'aria-label':'目标项目'}).props.onChange({target:{value:pid}}));
  await submitModal();
  assert.equal(state().conversations.find(c=>c.id==='group-research').project_id,pid);
  assert.equal(view.root.findByProps({'aria-label':'原型消息'}).props.value,'保留我的草稿');
  assert.deepEqual(state().runs,initial.runs);assert.deepEqual(state().messages,initial.messages);
  await act(async()=>button(view,'Atlas的项目设置').props.onClick());
  await act(async()=>view.root.findByProps({'aria-label':'项目名称'}).props.onChange({target:{value:'Atlas 开发'}}));
  await submitModal();assert.equal(state().projects[0].name,'Atlas 开发');
  const toggle=view.root.findByProps({'aria-controls':`cp-project-${pid}`});
  await act(async()=>toggle.props.onClick());assert.equal(view.root.findByProps({id:`cp-project-${pid}`}).props.hidden,true);
  await act(async()=>toggle.props.onClick());assert.equal(view.root.findByProps({id:`cp-project-${pid}`}).props.hidden,false);
  await act(async()=>button(view,'Atlas 开发的项目设置').props.onClick());
  await act(async()=>button(view,'删除项目').props.onClick());
  await act(async()=>button(view,'确认删除项目').props.onClick());
  assert.deepEqual(state().projects,[]);assert(!state().conversations.find(c=>c.id==='group-research').project_id);
  assert.deepEqual(state().runs,initial.runs);assert.deepEqual(state().messages,initial.messages);
  assert.equal(view.root.findByProps({'aria-label':'原型消息'}).props.value,'保留我的草稿');
  await act(async()=>view.unmount());
});

test('the unified draft and group picker keep explicit project ownership', async () => {
  const {saved}=environment();let state=reduce(initialDemo(),{type:'create_project',name:'研究'});
  const pid=state.projects[0].id;state.selected='dm-tebb';state.conversations.find(c=>c.id==='dm-tebb').project_id=pid;
  saved.set(DEMO_STORAGE,JSON.stringify(state));
  const {ChatPrototype}=load('chat-prototype');let view;await act(async()=>{view=create(React.createElement(ChatPrototype));});
  const count=state.conversations.length;
  await act(async()=>button(view,'新对话').props.onClick());
  assert.equal(view.root.findByProps({'aria-label':'新对话所属项目'}).props.value,pid);
  assert.equal(JSON.parse(saved.get(DEMO_STORAGE)).conversations.length,count);
  await openGroup(view);
  const dialog=view.root.findByProps({role:'dialog'});assert.equal(dialog.findByProps({'aria-label':'协作群所属项目'}).props.value,pid);
  await act(async()=>dialog.findAllByType('input').find(n=>n.props.maxLength===40).props.onChange({target:{value:'资料研究'}}));
  await act(async()=>dialog.findByType('form').props.onSubmit({preventDefault(){}}));
  state=JSON.parse(saved.get(DEMO_STORAGE));assert.equal(state.conversations.find(c=>c.id===state.selected).project_id,pid);
  await act(async()=>button(view,'新对话').props.onClick());
  await act(async()=>view.root.findByProps({'aria-label':'新对话所属项目'}).props.onChange({target:{value:''}}));
  assert.equal(view.root.findByProps({'aria-label':'新对话所属项目'}).props.value,'');
  assert.equal(JSON.parse(saved.get(DEMO_STORAGE)).conversations.length,count+1);
  await act(async()=>view.unmount());
});

test('failed project saves keep the name and dialog available for retry', async () => {
  environment();const {ChatPrototype}=load('chat-prototype');const actions=[];let view;
  const state=initialDemo();state.projects=[{id:'p1',name:'旧项目'}];
  await act(async()=>{view=create(React.createElement(ChatPrototype,{live:{state,busy:false,error:'',act:async action=>{actions.push(action);return false;}}}));});
  await act(async()=>button(view,'旧项目的项目设置').props.onClick());
  await act(async()=>view.root.findByProps({'aria-label':'项目名称'}).props.onChange({target:{value:'未保存的项目'}}));
  await act(async()=>view.root.findByProps({role:'dialog'}).findByType('form').props.onSubmit({preventDefault(){}}));
  assert.equal(actions[0].type,'rename_project');assert.equal(actions[0].name,'未保存的项目');
  assert.equal(view.root.findByProps({'aria-label':'项目名称'}).props.value,'未保存的项目');
  await act(async()=>view.unmount());
});

test('real folder entry opens the chooser and creates a folder-bound project without a manual name form',async()=>{
 environment();const actions=[],calls=[];const {ChatPrototype}=load('chat-prototype',{'./api':{rpc:async(_i,method,args)=>{calls.push({method,args});return {paths:['E:/Atlas']};}}});let view;
 await act(async()=>{view=create(React.createElement(ChatPrototype,{backend:{port:1,token:'fixture'},live:{state:initialDemo(),busy:false,error:'',act:async a=>{actions.push(a);return true;}}}));});
 await act(async()=>button(view,'打开文件夹').props.onClick());
 assert.equal(calls[0].args.operation,'pick');assert.equal(actions[0].type,'open_folder_project');assert.equal(actions[0].primary,'E:/Atlas');
 assert.equal(view.root.findAllByProps({'aria-label':'项目名称'}).length,0);
 await act(async()=>view.unmount());
});

test('migration preview separates evidence categories and makes no move until explicit confirmation',async()=>{
 environment();const {ProjectMigration}=load('project-migration',{'./api':{rpc:async()=>({token:'proof',rows:[{id:'a',title:'明确的历史',paths:['E:/Atlas'],status:'matched',project_name:'Atlas'},{id:'b',title:'多个目录',paths:['E:/A','E:/B'],status:'multiple',project_name:''},{id:'c',title:'执行中',paths:['E:/A'],status:'busy',project_name:''}]})}});
 const calls=[];let closed=0,view;await act(async()=>{view=create(React.createElement(ProjectMigration,{backend:{port:1,token:'fixture'},busy:false,onApply:async(token,ids)=>{calls.push({token,ids});return true;},onClose:()=>closed++}));});
 assert.equal(calls.length,0);assert(text(view.toJSON()).includes('存在多个工作目录'));assert(text(view.toJSON()).includes('任务进行中'));
 assert.equal(view.root.findAllByType('input').filter(n=>n.props.disabled).length,2);
 await act(async()=>button(view,'确认归类 1 段对话').props.onClick());
 assert.deepEqual(calls,[{token:'proof',ids:['a']}]);assert.equal(closed,1);await act(async()=>view.unmount());
});


test('normal App starts in core chat and keeps its draft mounted across settings and source previews', async () => {
  environment(); global.window.addEventListener = () => {}; global.window.removeEventListener = () => {}; global.window.dispatchEvent = () => true;
  let sideProps, liveProps, mounts = 0, unmounts = 0;
  const Empty = () => null;
  function LiveStub(props) {
    liveProps = props;
    const [value, setValue] = React.useState('');
    React.useEffect(() => { mounts++; return () => unmounts++; }, []);
    return React.createElement('div', null, React.createElement('input', { 'data-core-draft': true, value, onChange: e => setValue(e.target.value) }), props.navigation);
  }
  const defaultModule = new Proxy({}, { get(_o, key) { return key === '__esModule' ? true : Empty; } });
  const modules = {
    './companion-ui': load('companion-ui'),
    './workspace-shell': load('workspace-shell'),
    './workspace-navigation': load('workspace-navigation'),
    './page-stack': load('page-stack'),
    './live-collaboration': { LiveCollaboration: LiveStub },
    './debug': { debugPane: () => null, debugRequested: () => false, installDeskDebug() {} },
    './settings': { readStoredBackground: () => ({ url: '', error: '' }), SettingsPane: Empty },
    './release': { useReleaseIntro: () => ({ open: false }), ReleaseSettings: Empty },
    './memory': { splitIndex: () => -1, modelVerbatim: () => [], MemoryPane: Empty },
    './sampling': { DEFAULT_SAMPLING: { effort: '', temperature: 1, top_p: 1 } },
    './slash-menu': { slashQuery: () => null, filterSlash: items => items, SLASH_PARENTS: [], NEED_REPO: new Set() },
    './api': { backendInfo: () => new Promise(() => {}) },
    './board-actions': load('board-actions', { './api': { rpc: async () => ({ ok: true }) } }),
    './side-workspace': { SideWorkspace: props => { sideProps = props; return React.createElement('div', null, props.children); } },
    './startup': { startupStatus() {}, startupReady() {}, startupFailed() {} },
    'reend-components': { Button: props => React.createElement('button', props), GlitchText: props => React.createElement('span', null, props.children) },
    'framer-motion': { AnimatePresence: props => props.children, motion: { section: props => React.createElement('section', null, props.children) } },
    '@tauri-apps/api/core': { isTauri: () => false },
    '@tauri-apps/api/event': {},
  };
  const filename = path.resolve(__dirname, '../src/client/App.tsx');
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = new Module(filename, module); mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = mod.require.bind(mod); mod.require = id => modules[id] || (id.endsWith('.css') ? {} : id.startsWith('./') ? defaultModule : original(id));
  mod._compile(compiled, filename); let view;
  await act(async () => { view = create(React.createElement(mod.exports.App)); });
  const root = () => view.root.findAllByType('div').find(n => n.props['data-pane']);
  assert.equal(root().props['data-pane'], 'collaboration'); assert.equal(mounts, 1);
  const draft = () => view.root.findByProps({ 'data-core-draft': true });
  await act(async () => draft().props.onChange({ target: { value: '跨页保留的草稿' } }));
  await act(async () => sideProps.onRequest());
  assert.equal(root().props['data-pane'], 'collaboration');
  await act(async () => sideProps.onLink('https://example.org/source'));
  assert.equal(liveProps.composeRequest.text, 'https://example.org/source');
  await act(async () => sideProps.onTerminalDraft('可引用的终端输出'));
  assert.equal(liveProps.composeRequest.text, '可引用的终端输出');
  assert.equal(root().props['data-pane'], 'collaboration');
  await act(async () => button(view, '设置').props.onClick());
  assert.equal(root().props['data-pane'], 'settings');
  const slot = view.root.findByProps({'data-content-slot': true});
  const route = () => view.root.findByProps({className: 'tebb-route-page'});
  assert.equal(route().props.hidden, false); assert.equal(route().findAllByType('section').length, 1);
  assert.equal(route().findByType('section').props.initial, undefined);
  assert.equal(draft().props.value, '跨页保留的草稿'); assert.equal(unmounts, 0);
  const rail = view.root.findByProps({'aria-label':'工作区快捷栏'});
  await act(async () => rail.findAllByType('button').find(n => n.props['aria-label'] === '伙伴聊天').props.onClick());
  assert.equal(root().props['data-pane'], 'collaboration'); assert.equal(draft().props.value, '跨页保留的草稿');
  assert.equal(mounts, 1);
  assert.equal(view.root.findByProps({'data-content-slot': true}), slot);
  assert.equal(route().props.hidden, true); assert.equal(route().findAllByType('section').length, 1);
  assert.equal(view.root.findByProps({'data-page-panel': 'settings'}).props.hidden, true);
  await act(async()=>rail.findAllByType('button').find(n=>n.props['aria-label']==='笔记').props.onClick());
  assert.equal(root().props['data-pane'],'chat');assert.equal(view.root.findAllByProps({'data-notebook-empty':true}).length,1);
  assert.match(text(view.root.findByProps({'data-notebook-empty':true})),/还没有连上本地后端/);
  assert.equal(draft().props.value,'跨页保留的草稿');assert.equal(unmounts,0);
  await act(async () => view.unmount()); assert.equal(unmounts, 1);
});


test('@ member picker supports filtering, keyboard selection, multiple mentions and IME without submitting', async () => {
  const { saved } = environment(); const { ChatPrototype } = load('chat-prototype'); let view;
  await act(async()=>{view=create(React.createElement(ChatPrototype,{standalone:true}));});
  const composer=()=>view.root.findByProps({'aria-label':'原型消息'});
  const before=JSON.parse(saved.get(DEMO_STORAGE)).messages.length;
  await act(async()=>composer().props.onChange({target:{value:'@',selectionStart:1}}));
  assert.equal(view.root.findAllByProps({role:'option'}).length,3);
  await act(async()=>composer().props.onKeyDown({key:'ArrowDown',nativeEvent:{},preventDefault(){}}));
  await act(async()=>composer().props.onKeyDown({key:'Enter',nativeEvent:{},preventDefault(){}}));
  assert.equal(composer().props.value,'@研究助手 ');
  assert.equal(JSON.parse(saved.get(DEMO_STORAGE)).messages.length,before);
  await act(async()=>composer().props.onChange({target:{value:'@研究助手 @写',selectionStart:'@研究助手 @写'.length}}));
  assert.equal(view.root.findAllByProps({role:'option'}).length,1);
  await act(async()=>composer().props.onKeyDown({key:'Enter',nativeEvent:{isComposing:true},preventDefault(){throw Error('IME must stay active');}}));
  assert.equal(composer().props.value,'@研究助手 @写');
  await act(async()=>composer().props.onKeyDown({key:'Enter',nativeEvent:{},preventDefault(){}}));
  assert.equal(composer().props.value,'@研究助手 @写作助手 ');
  assert.equal(JSON.parse(saved.get(DEMO_STORAGE)).messages.length,before);
  await act(async()=>view.unmount());
});

test('unified send carries chosen skill, model and scoped attachments without leaking them to another conversation', async () => {
  environment();const {ChatPrototype}=load('chat-prototype');const state=initialDemo();state.messages=[];
  state.skills=[{id:'code-review',description:'review code'}];state.models=[{id:'model-one',model:'test-model'}];
  const files=[{id:'file-one',name:'source.md',kind:'file',path:'E:/fixture/source.md',chars:20}];
  const actions=[];const live={state,busy:false,error:'',act:async action=>{actions.push(action);return true;}};let view;
  await act(async()=>{view=create(React.createElement(ChatPrototype,{live,attachments:files,onDetach(){}}));});
  const composer=()=>view.root.findByProps({'aria-label':'协作消息'});
  await act(async()=>composer().props.onChange({target:{value:'/code',selectionStart:5}}));
  await act(async()=>view.root.findByProps({role:'option'}).props.onClick());
  await act(async()=>view.root.findByProps({'aria-label':'本轮模型'}).props.onChange({target:{value:'model-one'}}));
  await act(async()=>composer().props.onChange({target:{value:'检查这个文件',selectionStart:6}}));
  assert.equal(view.root.findAllByProps({'aria-label':'消息类型'}).length,0);
  await act(async()=>view.update(React.createElement(ChatPrototype,{live:{...live,state:{...state,selected:'dm-research'}},attachments:files,onDetach(){}})));
  await act(async()=>composer().props.onChange({target:{value:'另一个会话',selectionStart:5}}));
  await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));
  assert.deepEqual(actions[0].attachments,[]);assert.deepEqual(actions[0].skills,[]);assert.equal(actions[0].mode,'auto');
  await act(async()=>view.update(React.createElement(ChatPrototype,{live,attachments:files,onDetach(){}})));
  await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));
  assert.deepEqual(actions[1].attachments,['file-one']);assert.deepEqual(actions[1].skills,['code-review']);assert.equal(actions[1].model_id,'model-one');
  await act(async()=>view.unmount());
});


test('history and companions use separate tabs; companion settings do not replace the active conversation',async()=>{
 const {saved}=environment();const {ChatPrototype}=load('chat-prototype');let view;
 await act(async()=>{view=create(React.createElement(ChatPrototype,{standalone:true}));});
 const original=JSON.parse(saved.get(DEMO_STORAGE)).selected;
 assert(view.root.findAllByProps({'data-conversation':'group-research'}).length);
 assert.equal(view.root.findAllByProps({'data-companion':'research'}).length,0);
 await act(async()=>button(view,'伙伴').props.onClick());
 assert.equal(view.root.findAllByProps({'data-conversation':'group-research'}).length,0);
 await act(async()=>button(view,'研究助手的设置').props.onClick());
 assert(view.root.findByProps({role:'dialog'}));assert.equal(JSON.parse(saved.get(DEMO_STORAGE)).selected,original);
 await act(async()=>button(view,'关闭弹窗').props.onClick());
 await act(async()=>view.unmount());
});

test('successful answer can be collected without sending a new message or opening an external app',async()=>{
 environment();let called;window.dispatchEvent=()=>true;
 const {ChatPrototype}=load('chat-prototype',{'./api':{rpc:async(_info,method,args)=>{called={method,args};return {ok:true};}}});
 const state=initialDemo();const run=state.runs[0];run.state='succeeded';run.kind='task';run.answer='实际成果';
 state.messages.push({id:'collect-test',conversation:run.conversation,sender:run.agent,text:run.answer,run:run.id,kind:'result'});
 let view;await act(async()=>{view=create(React.createElement(ChatPrototype,{backend:{port:1,token:'test'},live:{state,busy:false,error:'',act:async()=>{throw Error('must not send');}}}));});
 await act(async()=>button(view,'收藏到笔记').props.onClick());
 assert.equal(called.method,'save_collaboration_note');assert.equal(called.args.run_id,run.id);
 assert(text(view.toJSON()).includes('已收藏到知识库'));
 await act(async()=>view.unmount());
});


test('one visible create entry serves both tabs, with group choice and correct workspace scope',async()=>{
 environment();const {ChatPrototype}=load('chat-prototype');let view;
 await act(async()=>{view=create(React.createElement(ChatPrototype,{live:{state:initialDemo(),act:async()=>true,busy:false,error:''},onProject(){}}));});
 assert.equal(view.root.findAllByType('button').filter(b=>text(b)==='新对话').length,1);
 assert(button(view,'工作目录与权限'));
 await act(async()=>button(view,'伙伴').props.onClick());
 assert.equal(view.root.findAllByType('button').filter(b=>text(b)==='新对话').length,1);
 await openGroup(view);assert(view.root.findByProps({role:'dialog'}));
 assert.equal(view.root.findAllByProps({className:'cp-project-context'}).length,0);
 assert(!button(view,'工作目录与权限'));
 await act(async()=>view.unmount());
});

test('composer menu fits narrow and short viewports and is anchored by the available edge',()=>{
 const {pickerBounds}=load('composer-popover');
 const small=pickerBounds({left:250,top:300,bottom:350,width:200},{width:340,height:420});
 assert.equal(small.position,'fixed');assert(small.left+small.width<=332);assert.equal(small.top,'auto');
 assert.equal(small.bottom,128);assert(small.maxHeight<=284);
 const low=pickerBounds({left:10,top:20,bottom:60,width:300},{width:600,height:500});
 assert.equal(low.top,68);assert.equal(low.bottom,'auto');assert(low.maxHeight<=424);
});


test('awaiting goal offers supplement without spinning and keeps human message unchanged',async()=>{
  environment();const state=initialDemo();state.selected='dm-tebb';
  const root={id:'clarify',conversation:state.selected,agent:'tebb',state:'needs_input',kind:'route',title:'test',step:0,updates:[],applied:0,calls:1,answer:'请补充具体目标',clarification:'请补充具体目标',events:['待补充'],chain:{state:'needs_input',calls:1,tool_calls:0,elapsed_ms:null,error:'',answer:'请补充具体目标',children:[]}};
  state.runs=[root];state.messages=[{id:'human',conversation:state.selected,sender:'user',run:root.id,text:'test'},{id:'answer',conversation:state.selected,sender:'tebb',run:root.id,text:root.answer}];
  const {ChatPrototype}=load('chat-prototype');let view;
  act(()=>{view=create(React.createElement(ChatPrototype,{developerMode:false,live:{state,act:async()=>true,busy:false,error:''}}));});
  const chat=view.root.findByProps({'aria-label':'协作聊天记录'});assert(text(chat).includes('test'));assert(text(chat).includes('尚未开始执行'));
  const supplement=chat.findAllByType('button').find(node=>text(node)==='改用文字补充');assert(supplement);
  await act(async()=>{await supplement.props.onClick();});assert(text(view.root).includes('提交补充'));act(()=>view.unmount());
});


const choiceRun=()=>({id:'choice-root',conversation:'dm-tebb',agent:'tebb',state:'needs_input',kind:'route',title:'需求评审会',step:0,updates:[],applied:0,events:[],clarification:'2026-10-29 20:00，请选择时长',clarification_version:'v1',clarification_form:{version:'v1',questions:[{id:'duration',question:'评审会持续多久？',options:[{id:'half',label:'30分钟',value:'持续30分钟'},{id:'hour',label:'1小时',value:'持续1小时'}]}]}});
test('structured choices have no default and only explicit continue submits, keeping answers on failure',async()=>{
  environment();const {ClarificationCard}=load('clarification-card');const actions=[];let view;
  await act(async()=>{view=create(React.createElement(ClarificationCard,{run:choiceRun(),onAction:async action=>{actions.push(action);return false;}}));});
  const next=()=>view.root.findAllByType('button').find(b=>text(b)==='继续执行');assert(next().props.disabled);assert(view.root.findAllByType('input').every(i=>!i.props.checked));
  act(()=>view.root.findAllByType('input').find(i=>i.props.value==='hour').props.onChange());assert.equal(actions.length,0);assert(!next().props.disabled);
  await act(async()=>{await next().props.onClick();});assert.equal(actions.length,1);assert.equal(actions[0].conversation,'dm-tebb');assert.equal(actions[0].version,'v1');assert.deepEqual(actions[0].answers,{duration:{option_id:'hour'}});
  assert(text(view.root).includes('保留当前选择'));assert(view.root.findAllByType('input').find(i=>i.props.value==='hour').props.checked);act(()=>view.unmount());
});
test('custom clarification and legacy option generation use the same original task binding',async()=>{
  environment();const {ClarificationCard}=load('clarification-card');const actions=[];let view;const props={run:choiceRun(),onAction:async a=>{actions.push(a);return true;}};
  await act(async()=>{view=create(React.createElement(ClarificationCard,props));});
  act(()=>view.root.findAllByType('label').find(l=>text(l)==='自定义').findByType('input').props.onChange());
  act(()=>view.root.findByProps({'aria-label':'评审会持续多久？的自定义答案'}).props.onChange({target:{value:'持续2小时'}}));
  await act(async()=>view.root.findAllByType('button').find(b=>text(b)==='继续执行').props.onClick());assert.deepEqual(actions[0].answers,{duration:{custom:'持续2小时'}});act(()=>view.unmount());
  const legacy={...choiceRun(),clarification_form:undefined};await act(async()=>{view=create(React.createElement(ClarificationCard,{...props,run:legacy}));});
  await act(async()=>view.root.findAllByType('button').find(b=>text(b)==='生成可选答案').props.onClick());assert.equal(actions[1].type,'clarification_options');assert.equal(actions[1].id,legacy.id);act(()=>view.unmount());
});
test('global inbox shows a foreign waiting task and answers it without replacing current draft',async()=>{
  environment();const state=initialDemo();state.selected='dm-writer';state.runs=[choiceRun()];state.messages=[];const actions=[];
  const {ChatPrototype}=load('chat-prototype');let view;await act(async()=>{view=create(React.createElement(ChatPrototype,{developerMode:false,live:{state,act:async a=>{actions.push(a);return true;},busy:false,error:''}}));});
  act(()=>view.root.findByProps({'aria-label':'协作消息'}).props.onChange({target:{value:'保留写作草稿'}}));
  act(()=>view.root.findByProps({'aria-label':'待你处理'}).props.onClick());assert(text(view.root).includes('全部会话 · 待你处理'));assert(text(view.root).includes('需求评审会'));
  const card=view.root.findByProps({'aria-label':'补充信息选项'});act(()=>card.findAllByType('input').find(i=>i.props.value==='hour').props.onChange());
  await act(async()=>card.findAllByType('button').find(b=>text(b)==='继续执行').props.onClick());assert.equal(actions[0].conversation,'dm-tebb');assert.equal(actions[0].id,'choice-root');assert.equal(view.root.findByProps({'aria-label':'协作消息'}).props.value,'保留写作草稿');act(()=>view.unmount());
});
