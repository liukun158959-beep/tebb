const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { act, create } = require('react-test-renderer');
global.window = new EventTarget();
function load(name, mocks) {
  const filename = path.resolve(__dirname, '../src/client', name + '.tsx');
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = new Module(filename, module); mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = mod.require.bind(mod);
  mod.require = id => id.endsWith('.css') ? {} : mocks[id] || original(id);
  mod._compile(code, filename); return mod.exports;
}
const text = node => typeof node === 'string' ? node : (node.children || []).map(text).join('');
const button = (view, label) => view.root.findAllByType('button').find(b => text(b) === label);
function fixture(rpc) {
  global.ResizeObserver = class { observe() {} disconnect() {} };
  const writes = [], handlers = [], clears = [];
  const { TerminalPane } = load('terminal', { './api': { rpc }, './loading-text': { LoadingText: ({ text }) => React.createElement('span', {}, text) },
    '@xterm/addon-fit': { FitAddon: class { fit() {} } }, '@xterm/xterm': { Terminal: class {
      options = {}; getSelection() { return 'copied output'; } buffer = {active:{length:0}}; clear() { clears.push(true); } loadAddon() {} open() {} reset() {} dispose() {} write(data) { writes.push(new TextDecoder().decode(data)); }
      onData(callback) { handlers.push(callback); return { dispose() {} }; }
    } } });
  return { TerminalPane, writes, handlers, clears };
}
const job = { id: 'job1', request_id: 'r', session: 's', command: 'echo hi', cwd: '/workspace', state: 'running',
  owner: 'agent', task_id: 'task1', created: 1, ended: null, exit_code: null, error: '', output_bytes: 3, timeout: 60 };
const info = { port: 1, token: 'test' };

test('sandbox terminal shows start/stop feedback, streams PTY bytes, queues input and never stops on collapse', async () => {
  const calls = []; let finish, stopped = false;
  const fixtureData = fixture(async (_info, method, args) => {
    calls.push({ method, args });
    if (method === 'terminal_status') return { ready: true, distro: 'Ubuntu', error: '' };
    if (method === 'terminal_list') return { jobs: stopped ? [{ ...job, state: 'cancelled', ended: 2 }] : [] };
    if (method === 'terminal_start') return new Promise(resolve => finish = () => resolve({ job }));
    if (method === 'terminal_read') return { job: stopped ? { ...job, state: 'cancelled' } : job, data: args.offset ? '' : btoa('hi\n'), offset: 3 };
    if (method === 'terminal_cancel') { stopped = true; return { ok: true }; }
    return { ok: true };
  });
  let view; await act(async () => { view = create(React.createElement(fixtureData.TerminalPane, { info, session: 's', attachments: [], onDraft() {}, onPreview() {} }), { createNodeMock: () => ({}) }); });
  await act(async () => view.root.findByProps({ 'aria-label': '要执行的命令' }).props.onChange({ target: { value: 'echo hi' } }));
  await act(async () => button(view, '执行').props.onClick());
  assert(text(view.toJSON()).includes('正在提交命令')); assert(button(view, '执行').props.disabled);
  await act(async () => finish()); assert.equal(fixtureData.writes[0], 'hi\n');
  await act(async () => fixtureData.handlers[0]('answer\r'));
  assert(calls.some(c => c.method === 'terminal_input' && c.args.text === 'answer\r'));
  await act(async () => button(view, '停止命令').props.onClick()); assert(text(view.toJSON()).includes('已停止'));
  assert(calls.some(c => c.method === 'terminal_cancel' && c.args.job_id === job.id));
  const cancelCount = calls.filter(c => c.method === 'terminal_cancel').length;
  await act(async () => view.unmount()); assert.equal(calls.filter(c => c.method === 'terminal_cancel').length, cancelCount);
});

test('failed sandbox check prevents execution and explains the problem without host fallback', async () => {
  const calls = [];
  const { TerminalPane } = fixture(async (_i, method) => { calls.push(method); return method === 'terminal_list' ? { jobs: [] } : { ready: false, distro: 'Missing', error: '找不到发行版' }; });
  let view; await act(async () => { view = create(React.createElement(TerminalPane, { info, session: 's', attachments: [], onDraft() {}, onPreview() {} })); });
  assert(text(view.toJSON()).includes('找不到发行版')); assert(button(view, '执行').props.disabled); assert(!calls.includes('terminal_start'));
  await act(async () => view.unmount());
});

test('interpretation only creates a draft and output is never submitted automatically', async () => {
  let draft = ''; const calls = [];
  const { TerminalPane } = fixture(async (_i, method) => { calls.push(method);
    if (method === 'terminal_list') return { jobs: [{ ...job, state: 'succeeded', exit_code: 0 }] };
    if (method === 'terminal_read') return { job: { ...job, state: 'succeeded', exit_code: 0 }, data: btoa('hi\n'), offset: 3 };
    return { ready: true, distro: 'Ubuntu', error: '' };
  });
  let view; await act(async () => { view = create(React.createElement(TerminalPane, { info, session: 's', attachments: [], onDraft: s => draft = s, onPreview() {} })); });
  assert.equal(draft, ''); await act(async () => button(view, '解读输出').props.onClick());
  assert(draft.includes('echo hi')); assert(draft.includes('hi')); assert(draft.includes('退出码：0')); assert(!calls.includes('chat'));
  await act(async () => view.unmount());
});


test('manual terminal binds the authorized project and sends its grant revision', async () => {
  const calls=[];const project={primary:'E:/fixture/project',revision:'grant-one',commands:true};
  const {TerminalPane}=fixture(async(_info,method,args)=>{
    calls.push({method,args});
    if(method==='project_workspace')return {ok:true,workspace:project};
    if(method==='terminal_status')return {ready:true,distro:'Ubuntu',error:''};
    if(method==='terminal_list')return {jobs:[]};
    if(method==='terminal_start')return {job:{...job,state:'succeeded',session:'project:'+project.primary}};
    if(method==='terminal_read')return {job:{...job,state:'succeeded'},data:'',offset:0};
    return {ok:true};
  });
  let view;await act(async()=>{view=create(React.createElement(TerminalPane,{info,session:'old-session',attachments:[],onDraft(){},onPreview(){}}));});
  await act(async()=>view.root.findByProps({'aria-label':'要执行的命令'}).props.onChange({target:{value:'python3 -m unittest'}}));
  await act(async()=>button(view,'执行').props.onClick());
  const sent=calls.find(c=>c.method==='terminal_start').args;
  assert.equal(sent.session,'project:E:/fixture/project');assert.equal(sent.workspace_revision,'grant-one');
  assert(text(view.toJSON()).includes('项目主目录'));
  await act(async()=>view.unmount());
});


test('VS Code panel tools preserve execution and draft while clearing or copying presentation', async () => {
  const calls=[]; let copied='';
  global.navigator={clipboard:{writeText:async value=>{copied=value;}}};
  const data=fixture(async(_i,method,args)=>{calls.push({method,args});
    if(method==='terminal_list')return {jobs:[{...job,state:'succeeded',exit_code:0}]};
    if(method==='terminal_read')return {job:{...job,state:'succeeded',exit_code:0},data:btoa('hi'),offset:2};
    return {ready:true,distro:'Ubuntu',error:''};
  });
  let view;await act(async()=>{view=create(React.createElement(data.TerminalPane,{info,session:'s',attachments:[],onDraft(){},onPreview(){}}),{createNodeMock:()=>({focus(){}})});});
  await act(async()=>view.root.findByProps({'aria-label':'要执行的命令'}).props.onChange({target:{value:'keep my draft'}}));
  await act(async()=>view.root.findByProps({'aria-label':'清屏'}).props.onClick());
  assert.equal(data.clears.length,1);assert(!calls.some(c=>c.method==='terminal_cancel'));
  await act(async()=>view.root.findByProps({'aria-label':'复制终端输出'}).props.onClick());assert.equal(copied,'copied output');
  await act(async()=>view.root.findByProps({'aria-label':'新建命令草稿'}).props.onClick());
  assert.equal(view.root.findByProps({'aria-label':'要执行的命令'}).props.value,'keep my draft');
  assert(!calls.some(c=>c.method==='terminal_start'));
  await act(async()=>view.root.findByProps({'aria-label':'显示执行记录'}).props.onClick());
  assert.equal(view.root.findByProps({'aria-label':'显示执行记录'}).props['aria-pressed'],true);
  await act(async()=>view.unmount());
});
