const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { act, create } = require('react-test-renderer');

function load(name, mocks = {}) {
  const filename = path.resolve(__dirname, '../src/client/' + name);
  const mod = new Module(filename, module);
  mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = mod.require.bind(mod);
  mod.require = id => Object.hasOwn(mocks, id) ? mocks[id] : id.endsWith('.png') ? 'poster.png' : original(id);
  mod._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText, filename);
  return mod.exports;
}
function storage() {
  const values = new Map();
  global.localStorage = { getItem: key => values.get(key) || null, setItem: (key, value) => values.set(key, value) };
  return values;
}
const prefs = load('release-preferences.ts');
function release(rpc = async () => {}) {
  return load('release.tsx', { './release-preferences': prefs, './api': { rpc }, './Markdown': {
    MdLink: ({ href, children }) => React.createElement('a', { href }, children),
    Markdown: ({ text }) => React.createElement('p', null, text) } });
}

test('daily preference persists, uses local day, and a new version is shown on the same day', () => {
  const saved = storage();
  assert.deepEqual(prefs.readReleasePreferences(), { daily: false, seen: {} });
  assert.equal(prefs.localDay(new Date(2026, 9, 8, 0, 1)), '2026-10-08');
  const value = { daily: true, seen: { '0.2.2': '2026-10-08' } };
  prefs.saveReleasePreferences(value);
  assert.deepEqual(prefs.readReleasePreferences(), value);
  assert.equal(prefs.shouldShowRelease(value, '0.2.2', '2026-10-08'), false);
  assert.equal(prefs.shouldShowRelease(value, '0.2.3', '2026-10-08'), true);
  assert.equal(prefs.shouldShowRelease(value, '0.2.2', '2026-10-09'), true);
  assert.equal(prefs.shouldShowRelease({ ...value, daily: false }, '0.2.2', '2026-10-08'), true);
  saved.set(prefs.RELEASE_STORAGE_KEY, '{broken');
  assert.equal(prefs.readReleasePreferences().daily, false);
});

test('intro waits for onboarding, opens once per app mount, and manual viewing bypasses daily suppression', () => {
  storage();
  const { useReleaseIntro } = release();
  let intro, renderer;
  function Host({ ready }) { intro = useReleaseIntro(ready); return null; }
  act(() => { renderer = create(React.createElement(Host, { ready: false })); });
  assert.equal(intro.open, false);
  act(() => renderer.update(React.createElement(Host, { ready: true })));
  assert.equal(intro.open, true);
  act(() => { intro.setDaily(true); intro.close(); });
  act(() => renderer.update(React.createElement(Host, { ready: false })));
  act(() => renderer.update(React.createElement(Host, { ready: true })));
  assert.equal(intro.open, false);
  act(() => renderer.unmount());
  act(() => { renderer = create(React.createElement(Host, { ready: true })); });
  assert.equal(intro.daily, true);
  assert.equal(intro.open, false);
  act(() => intro.show());
  assert.equal(intro.open, true);
  act(() => renderer.unmount());
});

test('settings make no startup request, surface failure, allow retry, and render formal release details', async () => {
  const calls = [];
  const { ReleaseSettings } = release(async (_, method) => {
    calls.push(method);
    if (calls.length === 1) throw Error('offline');
    return { ok: true, latest_version: '0.3.0', status: 'available', notes: '正式说明',
      release_url: 'https://github.com/liukun158959-beep/tebb/releases/tag/v0.3.0' };
  });
  let renderer;
  act(() => { renderer = create(React.createElement(ReleaseSettings, { info: { port: 1, token: 'test' }, onShowIntro() {} })); });
  assert.equal(calls.length, 0);
  const button = () => renderer.root.findAllByType('button').find(n => n.children.join('') === '检查更新');
  await act(async () => button().props.onClick());
  assert.match(JSON.stringify(renderer.toJSON()), /暂时无法检查更新/);
  assert.equal(renderer.root.findByType('a').props.href, 'https://github.com/liukun158959-beep/tebb/releases/latest');
  await act(async () => button().props.onClick());
  assert.deepEqual(calls, ['check_updates', 'check_updates']);
  assert.match(JSON.stringify(renderer.toJSON()), /发现新版本 v0.3.0/);
  assert.match(JSON.stringify(renderer.toJSON()), /正式说明/);
  assert.equal(renderer.root.findByType('a').props.href, 'https://github.com/liukun158959-beep/tebb/releases/tag/v0.3.0');
  act(() => renderer.unmount());
});

test('intro supports Escape and does not swallow arbitrary content clicks', () => {
  global.document = { activeElement: null };
  const { ReleaseIntro } = release();
  let closed = 0, renderer;
  act(() => { renderer = create(React.createElement(ReleaseIntro, { daily: false, onDaily() {}, onClose() { closed++; } })); });
  renderer.root.findByProps({ role: 'dialog' }).props.onKeyDown({ key: 'Escape', preventDefault() {}, stopPropagation() {} });
  assert.equal(closed, 1);
  const overlay = renderer.root.findByProps({ 'data-release-intro': true });
  overlay.props.onMouseDown({ target: {}, currentTarget: {} });
  assert.equal(closed, 1);
  act(() => renderer.unmount());
});
