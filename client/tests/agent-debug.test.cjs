const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { act, create } = require('react-test-renderer');

function load(name, mocks = {}) {
  const filename = path.resolve(__dirname, '../src/client/' + name + '.tsx');
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = new Module(filename, module); mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = mod.require.bind(mod); mod.require = id => mocks[id] || (id.startsWith('./')?load(id.slice(2)):original(id)); mod._compile(compiled, filename); return mod.exports;
}

test('version opens on the fifth consecutive click, resets after an interval, and toolbar actions work', () => {
  const { TopToolbar, versionTap } = load('top-toolbar', { './release': { CURRENT_RELEASE: { version: '0.2.2' } } });
  let taps = { count: 0, time: 0 };
  for (let i = 1; i <= 4; i++) { taps = versionTap(taps, i * 100); assert.equal(taps.open, false); }
  assert.equal(versionTap(taps, 600).open, true);
  assert.equal(versionTap(taps, 5000).count, 1);
  let opened = 0, chats = 0; let renderer;
  act(() => { renderer = create(React.createElement(TopToolbar, { title: '对话', model: 'test', connected: true, busy: false, dark: true,
    onNewChat: () => chats++ })); });
  assert.equal(renderer.root.findAllByProps({ 'data-debug-version': true }).length, 0);
  const { ProductBrand } = load('workspace-shell', { './release': {CURRENT_RELEASE:{version:'0.2.3'}}, './top-toolbar':{versionTap} });
  let brand; act(()=>{brand=create(React.createElement(ProductBrand,{developer:false,onToggle:()=>opened++}));});
  const version = brand.root.findByProps({'data-product-version':true});
  for(let i=0;i<4;i++) act(()=>version.props.onClick()); assert.equal(opened,0);
  act(()=>version.props.onClick()); assert.equal(opened,1);
  for(let i=0;i<5;i++) act(()=>version.props.onClick()); assert.equal(opened,2);
  act(()=>brand.unmount());
  act(() => renderer.root.findAllByType('button').find(n => n.children.join('') === '新对话').props.onClick());
  assert.equal(renderer.root.findAllByType('button').filter(n=>['视频','任务','浅色','深色'].includes(n.children.join(''))).length,0);
  assert.equal(chats, 1); act(() => renderer.unmount());
});

test('fixed top toolbar owns the sidebar toggle and reflects whether a panel is available',()=>{
  const {TopToolbar}=load('top-toolbar');let count=0,view;
  const props={title:'聊天',model:'test',connected:true,busy:false,dark:true,onTheme(){},onNewChat(){},onMonitor(){},onVideo(){},onDebug(){},onSidebarToggle:()=>count++};
  act(()=>{view=create(React.createElement(TopToolbar,{...props,sidebarAvailable:false}));});
  assert.equal(view.root.findByProps({'aria-label':'打开侧栏'}).props.disabled,true);
  act(()=>view.update(React.createElement(TopToolbar,{...props,sidebarAvailable:true,sidebarOpen:true})));
  const close=view.root.findByProps({'aria-label':'收起侧栏'});assert.equal(close.props['aria-expanded'],true);
  act(()=>close.props.onClick());assert.equal(count,1);act(()=>view.unmount());
});

test('request comparison keeps full changed messages and tool/schema changes', () => {
  const { requestDiff } = load('agent-debug', { './api': {}, './Markdown': { Markdown: () => null } });
  const large = '完整上下文'.repeat(9000) + '最后一行';
  const before = { messages: [{ role: 'system', content: large }, { role: 'user', content: '之前' }], tools: ['old'] };
  const after = { messages: [{ role: 'system', content: large }, { role: 'user', content: '之后' }, { role: 'tool', content: large }], tools: ['new'] };
  const diff = requestDiff(before, after);
  assert.equal(diff.changes.length, 2); assert.equal(diff.changes[1].after.content, large); assert.deepEqual(diff.keys, ['tools']);
});

test('network settings load before saving, keep proxy credentials private and allow direct mode',async()=>{
  const calls=[];let resolveLoad;const first=new Promise(resolve=>resolveLoad=resolve);
  const {ModelNetworkSettings}=load('model-network',{'./api':{rpc:async(_i,method,args)=>{calls.push([method,args]);if(method==='load_model_network')return first;return {...args.payload,has_password:true};}}});
  let view;await act(async()=>{view=create(React.createElement(ModelNetworkSettings,{info:{port:1,token:'test'}}));});
  assert.equal(view.root.findByType('fieldset').props.disabled,true);
  await act(async()=>resolveLoad({mode:'system',proxy_url:'http://localhost:7890',username:'',has_password:true}));
  await act(async()=>view.root.findByProps({'aria-label':'模型连接方式'}).props.onChange({target:{value:'custom'}}));
  assert.equal(view.root.findByProps({'aria-label':'模型代理密码'}).props.value,'');
  await act(async()=>view.root.findByProps({'aria-label':'模型代理密码'}).props.onChange({target:{value:'NEW_PROXY_SECRET'}}));
  await act(async()=>view.root.findAllByType('button').find(b=>b.children.join('')==='保存模型网络').props.onClick());
  assert.equal(calls.at(-1)[1].payload.password,'NEW_PROXY_SECRET');assert.equal(view.root.findByProps({'aria-label':'模型代理密码'}).props.value,'');
  await act(async()=>view.root.findByProps({'aria-label':'模型连接方式'}).props.onChange({target:{value:'direct'}}));
  await act(async()=>view.root.findAllByType('button').find(b=>b.children.join('')==='保存模型网络').props.onClick());
  assert.equal(calls.at(-1)[1].payload.mode,'direct');assert.equal(calls.at(-1)[1].payload.password,'');await act(async()=>view.unmount());
});

test('timing view separates nested scopes, identifies bottleneck, and never invents first-token time',()=>{
  const {PerformanceView,httpStages,PerformanceStats}=load('performance-view');
  const timing={network_mode:'direct',total_ms:10000,marks:{headers:8000,first_text:9000,body_end:10000},spans:[{phase:'connect_tcp',label:'DNS / TCP',duration_ms:100,start_ms:0},{phase:'receive_response_headers',label:'等待响应头',duration_ms:7900,start_ms:100},{phase:'receive_response_body',label:'整个响应流',duration_ms:2000,start_ms:8000}]};
  assert.equal(httpStages(timing).length,4);assert(!httpStages(timing).some(s=>s.label==='整个响应流'));
  let view;act(()=>{view=create(React.createElement(PerformanceView,{timing,chain:{total_ms:12000,state:'succeeded',stages:[{label:'排队',duration_ms:10},{label:'模型调用 1',duration_ms:10000}]}}));});
  assert(JSON.stringify(view.toJSON()).includes('等待响应头'));assert(JSON.stringify(view.toJSON()).includes('最耗时'));act(()=>view.unmount());
  act(()=>{view=create(React.createElement(PerformanceStats,{value:{completed:99,failed:1,timeouts:1,failure_rate:1/99,duration_ms:{p50:100,p95:1000,p99:null},first_text_ms:{n:98}}}));});
  assert(JSON.stringify(view.toJSON()).includes('样本不足100'));act(()=>view.unmount());
});

test('toolbar is a text menu and attachment entries remain usable during agent work', () => {
  const { TopToolbar }=load('top-toolbar',{'./release':{CURRENT_RELEASE:{version:'test'}}});
  let files=0,folders=0,view;
  act(()=>{view=create(React.createElement(TopToolbar,{title:'对话',model:'test',connected:true,busy:true,dark:true,
    onNewChat(){},onMonitor(){},onVideo(){},onTheme(){},onDebug(){},onFile(){files++;},onFolder(){folders++;}}));});
  for(const b of view.root.findAllByType('button')) assert.ok(!b.props.className.includes('desk-btn'));
  const button=label=>view.root.findAllByType('button').find(b=>b.children.join('')===label);
  assert.equal(button('文件').props.disabled,false);assert.equal(button('打开文件夹').props.disabled,false);
  act(()=>button('文件').props.onClick());act(()=>button('打开文件夹').props.onClick());
  assert.equal(files,1);assert.equal(folders,1);act(()=>view.unmount());
});

test('debug dialog shows complete payloads and explicitly requests read-only interpretation without changing task focus', async () => {
  const calls = []; const timers = new Map(); let serial = 0;
  global.window = { setInterval(fn) { timers.set(++serial, fn); return serial; }, clearInterval(id) { timers.delete(id); } };
  global.document = { activeElement: { focus() {} } };
  const large = '全量消息'.repeat(8000) + '保留结尾';
  const row = { id: 'call2', task_id: 'own-task', session: 'own-session', channel: 'desktop', created: 2, state: 'complete', duration_ms: 1300, first_byte_ms: 80, model: 'test', endpoint: '/chat/completions', status: 200, error: '' };
  const previous = { ...row, id: 'call1', created: 1 };
  const detail = { call: row, request: { model: 'test', messages: [{ role: 'system', content: large }, { role: 'tool', content: '工具真实结果' }], tools: [{ name: 'lookup' }] },
    request_raw: JSON.stringify({ messages: [{ role: 'system', content: large }] }), response: { choices: [{ message: { role: 'assistant', content: '实际返回' } }], usage: { prompt_tokens: 8000, completion_tokens: 10 } }, response_raw: 'data: 全量返回\n\ndata: [DONE]\n\n' };
  const rpc = async (_info, name, args) => {
    calls.push([name, args]);
    if (name === 'list_debug_calls') return { items: [row, previous], has_more: false, settings: { enabled: true } };
    if (name === 'get_debug_call') return args.call_id === 'call2' ? detail : { ...detail, call: previous, request: { messages: [{ role: 'system', content: large }] } };
    if (name === 'get_agent_task') return { task: { id: args.task_id, text: '问题', state: args.task_id === 'explain-task' ? 'running' : 'succeeded', answer: '', events: [{ seq: 1, ts: 1, kind: 'tool_end', data: { input: { x: '参数' }, result: { output: large } } }] } };
    if (name === 'explain_debug_call') return { task_id: 'explain-task' };
    if (name === 'set_debug_recording') return { enabled: args.enabled };
    return { ok: true };
  };
  const { AgentDebugDialog } = load('agent-debug', { './api': { rpc }, './Markdown': { Markdown: ({ text }) => React.createElement('p', null, text) } });
  let closed = 0, renderer;
  await act(async () => { renderer = create(React.createElement(AgentDebugDialog, { info: { port: 1, token: 'test' }, onClose: () => closed++ })); });
  const button = text => renderer.root.findAllByType('button').find(n => n.children.join('') === text);
  assert.equal(renderer.root.findByProps({ role: 'dialog' }).props['aria-modal'], 'true');
  assert.ok(renderer.root.findAllByType('pre').some(n => n.children.join('').includes(large)));
  assert.ok(calls.filter(c => c[0] === 'get_agent_task').every(c => c[1].focus === false));
  act(() => button('原始载荷').props.onClick());
  assert.ok(renderer.root.findAllByType('pre').some(n => n.children.join('').includes('data: [DONE]')));
  await act(async () => button('请求差异').props.onClick());
  assert.ok(JSON.stringify(renderer.toJSON()).includes('工具真实结果'));
  assert.ok(JSON.stringify(renderer.toJSON()).includes('不含正文的次数与用量元数据'));
  await act(async () => button('暂停详细载荷').props.onClick()); assert.equal(calls.find(c => c[0] === 'set_debug_recording')[1].enabled, false);
  act(() => button('协助解读').props.onClick());
  act(() => renderer.root.findByType('textarea').props.onChange({ target: { value: '为何新增工具结果？' } }));
  await act(async () => button('请模型协助解读').props.onClick());
  assert.deepEqual(calls.find(c => c[0] === 'explain_debug_call')[1], { call_id: 'call2', question: '为何新增工具结果？' });
  assert.equal(button('请模型协助解读').props.disabled, true);
  act(() => renderer.root.findByProps({ role: 'dialog' }).props.onKeyDown({ key: 'Escape', preventDefault() {}, stopPropagation() {} }));
  assert.equal(closed, 1); act(() => renderer.unmount()); assert.equal(timers.size, 0);
});
