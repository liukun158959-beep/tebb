const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

class Surface {
  constructor(parent = null) { this.parent = parent; this.events = new Map(); this.hidden = false; this.disabled = false; this.classList = { contains: () => this.hidden }; }
  addEventListener(name, fn) { this.events.set(name, fn); }
  removeEventListener(name) { this.events.delete(name); }
  fire(name, event = {}) { this.events.get(name)?.(event); }
  contains(node) { return node === this || !!node?.parent && this.contains(node.parent); }
  closest(selector) { return this.dataset?.action && (selector === 'button[data-action]' || selector.includes(this.dataset.action)) ? this : null; }
}

function load() {
  const window = new Surface(), document = new Surface(), menu = new Surface(), bubble = new Surface();
  const copy = new Surface(menu), action = new Surface(menu), hint = {};
  copy.dataset = { action: 'copy' }; action.dataset = { action: 'main' };
  menu.querySelector = (selector) => selector.includes('data-action') ? copy : hint;
  menu.getBoundingClientRect = () => ({ x: 20, y: 30, width: 140, height: 200 });
  let selection = null, error = '', closed = 0;
  const copied = [], locks = [], actions = [], invocations = [], native = new Map();
  window.getSelection = () => selection;
  const context = { exports: {}, window, document, navigator: { clipboard: { writeText: async (text) => copied.push(text) } },
    require: (name) => name.endsWith('/core') ? { isTauri: () => true, invoke: async (...args) => invocations.push(args) }
      : { listen: async (name, fn) => { native.set(name, fn); return () => native.delete(name); } }, Error, Object, Array, Promise };
  const compiled = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../src/pet-menu.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(compiled, context);
  const api = context.exports;
  const controls = api.installPetMenu({ menu, bubble, close: () => { closed++; menu.hidden = true; },
    run: (name) => actions.push(name), lockSelection: (locked) => locks.push(locked), errorText: () => error });
  const select = (text, anchor = action, focus = action) => {
    selection = { isCollapsed: !text, anchorNode: anchor, focusNode: focus, toString: () => text };
    document.fire('selectionchange');
  };
  const click = (target) => menu.fire('click', { target, preventDefault() {} });
  return { ...api, window, document, menu, bubble, copy, action, hint, controls, copied, locks, actions, invocations, native,
    select, click, closed: () => closed, setError: (text) => { error = text; controls.refresh(); } };
}

test('menu action errors preserve existing recovery advice exactly once', () => {
  const { petActionError } = load();
  assert.equal(petActionError('打开主窗口', '找不到主窗口。恢复：重启客户端。'), '打开主窗口失败：找不到主窗口。恢复：重启客户端。');
  assert.equal(petActionError('打开主窗口', new Error('窗口操作失败')), '打开主窗口失败：窗口操作失败。恢复：重启客户端。');
  assert.equal(petActionError('今天的安排', '读取失败。恢复：稍后重试。'), '今天的安排失败：读取失败。恢复：稍后重试。');
});

test('outside click, Escape, focus loss and native outside click close the menu', () => {
  for (const event of ['pointerdown', 'keydown', 'blur', 'native']) {
    const ui = load();
    if (event === 'native') ui.native.get('dismiss-pet-menu')();
    else ui.window.fire(event, { target: new Surface(), key: 'Escape', button: 0 });
    assert.equal(ui.closed(), 1, event);
  }
  const ui = load();
  ui.menu.hidden = true;
  ui.window.fire('pointerdown', { target: new Surface(), button: 0 });
  assert.equal(ui.closed(), 0);
});

test('dragging menu text keeps menu open and does not activate its action', () => {
  const ui = load();
  ui.window.fire('pointerdown', { target: ui.action, button: 0 });
  ui.select('打开主窗口');
  ui.window.fire('pointerup');
  ui.click(ui.action);
  assert.equal(ui.closed(), 0);
  assert.deepEqual(ui.actions, []);
  assert.deepEqual(ui.locks, [true, false]);
  ui.select('');
  ui.click(ui.action);
  assert.deepEqual(ui.actions, ['main']);
});

test('copy preserves text selected before pressing its button, including bubble text', async () => {
  const ui = load();
  ui.select('文件路径：C:/assets/Core.js', ui.bubble, ui.bubble);
  assert.equal(ui.copy.disabled, false);
  let prevented = false;
  ui.window.fire('pointerdown', { target: ui.copy, button: 0, preventDefault() { prevented = true; } });
  ui.select('');
  ui.click(ui.copy);
  await new Promise(setImmediate);
  assert.equal(prevented, true);
  assert.deepEqual(ui.copied, ['文件路径：C:/assets/Core.js']);
  assert.equal(ui.hint.textContent, '已复制');
  assert.equal(ui.closed(), 0);
});

test('copy error without selection; reject selections outside pet text; release locks outside window', async () => {
  const ui = load();
  ui.controls.refresh();
  assert.equal(ui.copy.disabled, true);
  ui.setError('Cubism Core 加载失败');
  assert.equal(ui.copy.textContent, '复制错误信息');
  ui.select('页面外的文字', ui.action, new Surface());
  ui.click(ui.copy);
  await Promise.resolve();
  assert.deepEqual(ui.copied, ['Cubism Core 加载失败']);
  ui.window.fire('pointerdown', { target: ui.action, button: 0 });
  ui.native.get('pet-pointer-released')();
  assert.deepEqual(ui.locks, [true, false]);
  await ui.syncPetMenuRegion(ui.menu);
  assert.equal(ui.invocations[0][0], 'set_pet_menu_region');
  assert.equal(ui.invocations[0][1].rect.w, 140);
  ui.menu.hidden = true;
  await ui.syncPetMenuRegion(ui.menu);
  assert.equal(ui.invocations[1][1].rect, null);
});
