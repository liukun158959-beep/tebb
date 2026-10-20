const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { act, create } = require('react-test-renderer');

function load(name, mocks) {
  const file = path.resolve(__dirname, '../src/client', name);
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = new Module(file, module); mod.filename = file; mod.paths = Module._nodeModulePaths(path.dirname(file));
  const original = mod.require.bind(mod); mod.require = id => mocks[id] || original(id);
  mod._compile(code, file); return mod.exports;
}
const snapshot = () => ({ ok: true, date: '2026-10-20', agenda: { ok: true, items: [
  { summary: 'old event', event_id: 'event', start: '09:00', end: '10:00' }] }, tasks: { ok: true, items: [
  { summary: 'old task', guid: 'task' }] } });
const text = node => typeof node === 'string' ? node : (node?.children || []).map(text).join('');
async function mount(rpc, debug = null, initial = snapshot()) {
  const { useBoardActions } = load('board-actions.ts', { './api': { rpc } });
  const { BoardPane } = load('board.tsx', {
    'reend-components': { Button: p => React.createElement('button', p, p.children),
      TacticalPanel: p => React.createElement('section', {}, p.children) },
    './status': { StatusGrid: () => null },
    './datetime-field': { DateTimeField: p => React.createElement('input', { 'data-date': p.marker,
      value: p.value, onChange: e => p.onChange(e.target.value) }) } });
  const state = { data: null, actions: null };
  function Harness() {
    const [data, setData] = React.useState(initial);
    const actions = useBoardActions({ info: { port: 1, token: 'test' }, boardClock: debug, setBoard: setData, onLoaded() {} });
    state.data = data; state.actions = actions;
    return React.createElement(BoardPane, { data, nowIso: '2026-10-20T08:00:00+08:00', loading: actions.loading,
      loadError: actions.loadError, debug: !!debug, onRefresh: () => actions.loadBoardSnapshot(true), statuses: [], fetches: 0,
      deleting: actions.rowDeleting, deleteErrors: actions.agendaErrors, taskErrors: actions.taskErrors,
      createError: actions.createError, createNotice: actions.createNotice, createNonce: actions.createNonce,
      onCreateEvent: actions.createEvent, onDeleteEvent: actions.deleteAgenda, onDeleteTask: actions.deleteTask });
  }
  let view; await act(async () => { view = create(React.createElement(Harness)); });
  return { state, view };
}

test('business failure and disconnect preserve actual creation form, snapshot and stage error; success clears once', async () => {
  const calls = []; let fail = true, disconnect = false;
  const { state, view } = await mount(async (_i, method, args) => {
    calls.push({ method, args });
    if (disconnect) throw new Error('连接已断开，尚未收到操作结果；请先核查是否已执行。');
    return fail ? { ok: false, stage: 'cache_save', remote_state: 'confirmed', error: '飞书已成功，本地缓存未确认，请核对。' }
      : { ok: true, snapshot: { ...snapshot(), summary: 'confirmed' } };
  });
  const title = () => view.root.findByProps({ placeholder: '日程标题' });
  const date = marker => view.root.findByProps({ 'data-date': marker });
  act(() => {
    title().props.onChange({ target: { value: 'retained draft' } });
    date('start').props.onChange({ target: { value: '2026-10-20T09:00' } });
    date('end').props.onChange({ target: { value: '2026-10-20T10:00' } });
  });
  const submit = () => view.root.findByType('form').props.onSubmit({ preventDefault() {} });
  await act(async () => submit());
  assert.equal(title().props.value, 'retained draft');
  assert.equal(date('start').props.value, '2026-10-20T09:00');
  assert.equal(state.data.agenda.items[0].summary, 'old event');
  assert(text(view.toJSON()).includes('本地缓存未确认'));
  assert.equal(state.actions.createNonce, 0);
  disconnect = true;
  await act(async () => submit());
  assert.equal(title().props.value, 'retained draft');
  assert(text(view.toJSON()).includes('先核查是否已执行'));
  assert.equal(calls.length, 2); // Each explicit submit once; never automatic retry.
  disconnect = false; fail = false;
  await act(async () => submit());
  assert.equal(title().props.value, '');
  assert.equal(state.data.summary, 'confirmed');
  assert.equal(state.actions.createNonce, 1);
  act(() => view.unmount());
});

test('delete failure keeps existing rows, exposes row error and releases busy', async () => {
  const { state, view } = await mount(async () => ({ ok: false, error: '远端删除结果未确认，不要重发。' }));
  await act(async () => state.actions.deleteAgenda('event'));
  await act(async () => state.actions.deleteTask('task'));
  assert.equal(state.data.agenda.items.length, 1);
  assert.equal(state.data.tasks.items.length, 1);
  assert(text(view.toJSON()).includes('远端删除结果未确认'));
  assert.equal(state.actions.rowDeleting, false);
  act(() => view.unmount());
});

test('late load responses cannot overwrite later mutations or clear newer failures', async () => {
  const reads = []; let createResolve;
  const { state, view } = await mount((_i, method) => method === 'load_board'
    ? new Promise(resolve => reads.push(resolve)) : new Promise(resolve => createResolve = resolve));
  let oldRead, mutation, duringWrite;
  act(() => { oldRead = state.actions.loadBoardSnapshot(false); });
  act(() => { mutation = state.actions.createEvent('title', 'start', 'end'); });
  act(() => { duringWrite = state.actions.loadBoardSnapshot(false); });
  await act(async () => { createResolve({ ok: true, snapshot: { ...snapshot(), summary: 'new' } }); await mutation; });
  await act(async () => { reads[0]({ ...snapshot(), summary: 'stale' }); await oldRead;
    reads[1]({ ...snapshot(), summary: 'also stale' }); await duringWrite; });
  assert.equal(state.data.summary, 'new');
  assert.equal(state.actions.loading, false);
  assert.equal(state.actions.rowDeleting, false);
  act(() => view.unmount());
});

test('overlapping reads keep newest result and refresh failure retains existing data with visible error', async () => {
  const resolves = [];
  const { state, view } = await mount(() => new Promise(resolve => resolves.push(resolve)));
  let first, second;
  act(() => { first = state.actions.loadBoardSnapshot(false); second = state.actions.loadBoardSnapshot(true); });
  await act(async () => { resolves[1]({ ...snapshot(), summary: 'newer' }); await second; });
  await act(async () => { resolves[0]({ ok: false, error: 'old failure' }); await first; });
  assert.equal(state.data.summary, 'newer'); assert.equal(state.actions.loadError, '');
  let failed; act(() => { failed = state.actions.loadBoardSnapshot(true); });
  await act(async () => { resolves[2]({ ok: false, error: '刷新忙碌，请稍后核对。' }); await failed; });
  assert.equal(state.data.summary, 'newer');
  assert(text(view.toJSON()).includes('刷新忙碌'));
  assert.equal(state.actions.loading, false);
  act(() => view.unmount());
});

test('debug board only changes local samples and duplicate clicks do not send twice', async () => {
  let calls = 0;
  const debug = await mount(async () => { calls++; }, '2026-10-20T08:00:00+08:00');
  await act(async () => debug.state.actions.deleteAgenda('event'));
  await act(async () => debug.state.actions.createEvent('sample', '2026-10-20T09:00', '2026-10-20T10:00'));
  assert.equal(calls, 0); assert.equal(debug.state.data.agenda.items[0].summary, 'sample');
  act(() => debug.view.unmount());
  let finish;
  const real = await mount(() => { calls++; return new Promise(resolve => finish = resolve); });
  let pending; act(() => { pending = real.state.actions.deleteTask('task'); void real.state.actions.deleteTask('task'); });
  assert.equal(calls, 1);
  await act(async () => { finish(snapshot()); await pending; });
  act(() => real.view.unmount());
});

test('first load failure keeps a visible refresh action and can recover without remounting', async () => {
  let failed = true;
  const { state, view } = await mount(async () => failed ? { ok: false, error: '看板更新忙碌' } : snapshot(), null, null);
  await act(async () => state.actions.loadBoardSnapshot(false));
  assert.equal(state.data, null);
  assert(text(view.toJSON()).includes('看板更新忙碌'));
  assert.equal(view.root.findAllByProps({ 'aria-busy': 'true' }).length, 0);
  failed = false;
  const refresh = view.root.findAllByType('button').find(n => n.children.join('') === '刷新');
  await act(async () => refresh.props.onClick());
  assert.equal(state.data.ok, true);
  assert.equal(state.actions.loadError, '');
  act(() => view.unmount());
});
