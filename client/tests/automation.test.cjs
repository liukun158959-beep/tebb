const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { act, create } = require('react-test-renderer');

test('preview, archive and group publication are distinct; enable checks targets before saving schedule', async () => {
  const filename = path.resolve(__dirname, '../src/client/automation.tsx');
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = new Module(filename, module);
  mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const calls = [];
  const settings = { profile: 'test', wiki_url: 'https://example/wiki/w', chat_id: 'oc_test', base_url: 'https://example/base/b',
    base_token: 'b', table_id: 'tbl', topics: ['Agent engineering'], lookback_days: 7, highlights: 3 };
  const run = { id: 'draft-id', day: '2026-10-08', status: 'draft', report: { lead: '技术重点', candidate_count: 3,
    items: [{ title: '官方资料', url: 'https://github.com/example', summary: '事实', value: '建议', caution: '待验证' }] } };
  const rpc = async (_info, method, args) => {
    calls.push([method, args]);
    if (method === 'load_news') return { ok: true, settings, runs: [run] };
    if (method === 'list_automation_jobs') return { ok: true, items: [] };
    if (method === 'list_agent_tasks') return { items: [] };
    if (method === 'check_news_targets') return { ok: true, targets: { wiki_title: '技术库', chat_name: '目标群' } };
    return { ok: true, run_id: method === 'run_news' ? 'draft-id' : undefined };
  };
  const original = mod.require.bind(mod);
  mod.require = id => id === './page-stack' ? { usePageActive: () => true } : id === './api' ? { rpc } : id === './Markdown' ? { Markdown: ({ text }) => React.createElement('p', null, text) } : original(id);
  mod._compile(compiled, filename);
  global.window = { setInterval: () => 1, clearInterval() {} };
  let renderer;
  await act(async () => { renderer = create(React.createElement(mod.exports.AutomationPane, { info: { port: 1, token: 'test' } })); });
  const button = label => renderer.root.findAllByType('button').find(n => n.children.join('') === label);
  await act(async () => button('生成今日预览').props.onClick());
  assert.deepEqual(calls.filter(c => c[0] === 'run_news').at(-1)[1], { publish: false });
  await act(async () => button('仅归档文档和资讯表').props.onClick());
  assert.deepEqual(calls.filter(c => c[0] === 'run_news').at(-1)[1], { publish: true, run_id: 'draft-id', send_group: false });
  await act(async () => button('发布这份日报到飞书').props.onClick());
  assert.deepEqual(calls.filter(c => c[0] === 'run_news').at(-1)[1], { publish: true, run_id: 'draft-id' });
  const checkbox = renderer.root.findAllByType('input').find(n => n.props.type === 'checkbox');
  act(() => checkbox.props.onChange({ target: { checked: true } }));
  const before = calls.length;
  await act(async () => button('保存定时任务').props.onClick());
  assert.equal(calls[before][0], 'check_news_targets');
  const saved = calls.findLast(c => c[0] === 'save_automation_job')[1].payload;
  assert.equal(saved.enabled, true); assert.equal(saved.hour, 9); assert.equal(saved.minute, 0);
  act(() => renderer.unmount());
});

test('active result cleanup freezes edits and deletion; failed save preserves form and explains confirmed stage', async () => {
  const filename = path.resolve(__dirname, '../src/client/automation.tsx');
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = new Module(filename, module); mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename));
  let job = { id: 'job', name: 'existing', action: 'retro_write', enabled: false, cadence: 'daily', weekdays: [],
    hour: 12, minute: 0, doc: 'target', last_result: 'fail', active: false };
  let refresh; const calls = [];
  const rpc = async (_info, method, args) => {
    calls.push([method, args]);
    if (method === 'load_news') return { settings: { profile: '', wiki_url: '', base_url: '', table_id: '',
      base_token: '', chat_id: '', topics: [], lookback_days: 7, highlights: 5 }, runs: [] };
    if (method === 'list_automation_jobs') return { ok: true, items: [{ ...job }] };
    if (method === 'list_agent_tasks') return { items: [] };
    return { ok: false, stage: 'snapshot', change_state: 'committed', error: '配置已保存，但列表读取失败，请重新读取核对。' };
  };
  const original = mod.require.bind(mod);
  mod.require = id => id === './page-stack' ? { usePageActive: () => true } : id === './api' ? { rpc }
    : id === './Markdown' ? { Markdown: () => null } : original(id);
  mod._compile(code, filename);
  global.window = { setInterval: fn => { refresh = fn; return 1; }, clearInterval() {} };
  let view; await act(async () => { view = create(React.createElement(mod.exports.AutomationPane, { info: { port: 1, token: 'test' } })); });
  const button = name => view.root.findAllByType('button').find(n => n.children.join('') === name);
  await act(async () => button('修改').props.onClick());
  const name = () => view.root.findAllByType('input').find(n => n.props.value === 'existing' || n.props.value === 'retained draft');
  act(() => name().props.onChange({ target: { value: 'retained draft' } }));
  await act(async () => button('保存定时任务').props.onClick());
  assert.equal(name().props.value, 'retained draft');
  assert.match(view.root.findByProps({ role: 'alert' }).children.join(''), /已保存.*读取失败/);
  assert.equal(calls.filter(([m]) => m === 'save_automation_job').length, 1);
  job = { ...job, active: true }; // Final receipt is pending even though last_result says fail.
  await act(async () => refresh());
  for (const label of ['修改', '删除定时', '立即执行', '保存定时任务']) assert.equal(button(label).props.disabled, true, label);
  act(() => button('新增定时任务').props.onClick());
  assert.equal(!!button('保存定时任务').props.disabled, false);
  assert.equal(calls.filter(([m]) => m === 'delete_automation_job').length, 0);
  act(() => view.unmount());
});
