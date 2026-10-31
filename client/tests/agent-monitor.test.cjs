const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { act, create } = require('react-test-renderer');

test('monitor stops only selected task, continues same conversation and sends only when checked', async () => {
  const filename = path.resolve(__dirname, '../src/client/agent-monitor.tsx');
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = new Module(filename, module);
  mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const calls = [];
  const task = { id: 'selected-task', session: 'feishu-own-context', channel: 'feishu', text: '问天气', state: 'running',
    answer: '已查到部分信息', error: '', created: 1, elapsed: 8, events: [{ seq: 1, kind: 'status', ts: 2, data: '正在查询天气' }], source: {} };
  const settings = { parallel: 2, call_timeout: 90, task_timeout: 300, pet_progress: true };
  const rpc = async (_info, method, args) => {
    calls.push([method, args]);
    if (method === 'list_agent_tasks') return { items: [task], settings };
    if (method === 'get_agent_task') return { task, history: [{ role: 'user', text: '问天气' }] };
    if (method === 'cancel_agent_task') return { ok: true, task: { ...task, state: 'cancelled' } };
    return { ok: true };
  };
  const original = mod.require.bind(mod);
  mod.require = id => id === './task-budget' ? require('./component-fixture.cjs').loader({})('task-budget') : id === './page-stack' ? { usePageActive: () => true } : id === './api' ? { rpc } : id === './video' ? { TaskVideos: () => null, VideoSettings: () => null } : id === './Markdown' ? {
    Markdown: ({ text }) => React.createElement('p', null, text) } : original(id);
  mod._compile(compiled, filename);
  global.window = { setInterval: () => 1, clearInterval() {} };
  let renderer;
  await act(async () => { renderer = create(React.createElement(mod.exports.AgentMonitor, { info: { port: 1, token: 'test' } })); });
  const button = label => renderer.root.findAllByType('button').find(n => n.children.join('') === label);
  assert.ok(JSON.stringify(renderer.toJSON()).includes('已查到部分信息'));
  await act(async () => button('停止任务').props.onClick());
  assert.deepEqual(calls.find(c => c[0] === 'cancel_agent_task')[1], { task_id: 'selected-task' });
  act(() => renderer.root.findByType('textarea').props.onChange({ target: { value: '继续查北京' } }));
  await act(async () => button('在本地继续').props.onClick());
  assert.deepEqual(calls.filter(c => c[0] === 'continue_agent_task').at(-1)[1], { task_id: 'selected-task', text: '继续查北京', send_back: false });
  const send = renderer.root.findAllByType('input').find(n => n.props.type === 'checkbox' && n.parent.children.some(c => typeof c === 'string' && c.includes('发送回')));
  act(() => send.props.onChange({ target: { checked: true } }));
  await act(async () => button('继续并回复飞书').props.onClick());
  assert.equal(calls.filter(c => c[0] === 'continue_agent_task').at(-1)[1].send_back, true);
  act(() => renderer.unmount());
});
