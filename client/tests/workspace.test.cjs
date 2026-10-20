const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { act, create } = require('react-test-renderer');

function load(name, mocks = {}) {
  const base = path.resolve(__dirname, '../src/client', name);
  const filename = fs.existsSync(base + '.tsx') ? base + '.tsx' : base + '.ts';
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = new Module(filename, module); mod.filename = filename; mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const original = mod.require.bind(mod);
  mod.require = id => mocks[id] || (id.startsWith('./') ? load(id.slice(2), mocks) : original(id));
  mod._compile(code, filename); return mod.exports;
}
function environment() {
  global.window = new EventTarget();
  global.ResizeObserver = class { observe() {} disconnect() {} };
}
const renderedText = node => typeof node === 'string' ? node : (node.children || []).map(renderedText).join('');
const button = (view, label) => view.root.findAllByType('button').find(b => renderedText(b) === label);
const source = { id: 's1', name: '技术.md', kind: 'file', path: 'E:/fixture/技术.md', chars: 12 };
const markdown = { Markdown: ({ text }) => React.createElement('article', { 'data-markdown': '' }, text), openableHref: url => /^https?:\/\//.test(url || '') ? url : null };
const mocks = rpc => ({ './api': { rpc }, './Markdown': markdown, './terminal': { TerminalPane: () => React.createElement('section', { 'data-terminal': '' }, '终端') }, '@tauri-apps/api/core': { isTauri: () => false }, '@tauri-apps/api/event': {} });
const workspaceProps = { info: { port: 1, token: 'test' }, enabled: true, attachments: [], onAttach() {}, onDetach() {}, onLink() {}, onRequest() {}, children: React.createElement('textarea', { defaultValue: '保留草稿' }) };

test('file preview paginates without attaching, and preserves the conversation across collapse and tab switches', async () => {
  environment(); const calls = []; let attached = [];
  const { SideWorkspace, previewSource, previewQuote } = load('side-workspace', mocks(async (_i, method, args) => {
    calls.push({ method, args }); return { ok: true, source: { text: args.offset ? '第二页' : '# 第一页', chars: 12, next_offset: args.offset ? null : 5 } };
  }));
  let view; await act(async () => { view = create(React.createElement(SideWorkspace, { ...workspaceProps, onAttach: values => attached = values })); });
  const draft = view.root.findByType('textarea');
  await act(async () => previewSource(source)); assert.equal(attached.length, 0);
  assert.equal(view.root.findByProps({ 'data-markdown': '' }).children[0], '# 第一页');
  await act(async () => button(view, '继续读取').props.onClick());
  assert.equal(calls[1].args.offset, 5); assert.equal(view.root.findByProps({ 'data-markdown': '' }).children[0], '# 第一页第二页');
  await act(async () => button(view, '加入本轮资料').props.onClick()); assert.deepEqual(attached, [source]);
  await act(async () => button(view, '收起').props.onClick()); assert.equal(view.root.findByType('textarea'), draft);
  await act(async () => previewQuote({ n: 1, title: '来源', doc: '原文', doc_id: 'd', text: '摘要', context: '**完整片段**' }));
  assert.equal(view.root.findByProps({ 'data-markdown': '' }).children[0], '**完整片段**');
  assert.equal(view.root.findByType('textarea'), draft); await act(async () => view.unmount());
});

test('closing or stopping a pending preview ignores late data and never creates an attachment', async () => {
  environment(); let finish, attachments = 0;
  const { SideWorkspace, previewSource } = load('side-workspace', mocks(() => new Promise(resolve => finish = resolve)));
  let view; await act(async () => { view = create(React.createElement(SideWorkspace, { ...workspaceProps, onAttach: () => attachments++ })); previewSource(source); });
  // The listener is installed after mount.
  await act(async () => previewSource(source));
  await act(async () => button(view, '停止等待').props.onClick());
  await act(async () => finish({ ok: true, source: { text: '迟到内容', chars: 4, next_offset: null } }));
  assert(!renderedText(view.toJSON()).includes('迟到内容')); assert.equal(attachments, 0);
  await act(async () => button(view, '重试').props.onClick());
  await act(async () => view.root.findByProps({ 'aria-label': '关闭 技术.md' }).props.onClick());
  await act(async () => finish({ ok: true, source: { text: '关闭后的内容', chars: 6, next_offset: null } }));
  assert.equal(view.root.findByProps({'aria-label':'侧边工作区'}).props.hidden, true); assert.equal(view.root.findByType('textarea').props.defaultValue, '保留草稿');
  await act(async () => view.unmount());
});

test('browser is embedded, hides behind dialogs or collapsed panes, and inserts only an explicitly requested link', async () => {
  environment(); const calls = []; let linked = '', listener;
  const moduleMocks = mocks(async () => ({}));
  moduleMocks['@tauri-apps/api/core'] = { isTauri: () => true, invoke: async (name, args) => { calls.push({ name, args }); } };
  moduleMocks['@tauri-apps/api/event'] = { listen: async (_name, callback) => { listener = callback; return () => {}; } };
  const { SideWorkspace } = load('side-workspace', moduleMocks);
  let view; await act(async () => { view = create(React.createElement(SideWorkspace, { ...workspaceProps, onLink: url => linked = url }), {
    createNodeMock: () => ({ getBoundingClientRect: () => ({ left: 600, top: 90, width: 400, height: 450 }) }) }); });
  await act(async () => window.dispatchEvent(new CustomEvent('desk-open-preview', { detail: { kind: 'web', url: 'https://example.org' } })));
  assert(calls.some(c => c.name === 'open_side_browser')); assert.equal(linked, '');
  await act(async () => listener({ payload: { url: 'https://example.org/page', title: '网页标题', loading: false } }));
  await act(async () => button(view, '引用链接到草稿').props.onClick()); assert.equal(linked, 'https://example.org/page');
  await act(async () => view.update(React.createElement(SideWorkspace, { ...workspaceProps, suspended: true, onLink: url => linked = url })));
  assert.equal(calls.filter(c => c.name === 'side_browser_layout').at(-1).args.visible, false);
  await act(async () => view.root.findByProps({ 'aria-label': '关闭 网页' }).props.onClick());
  assert(calls.some(c => c.name === 'side_browser_action' && c.args.action === 'close')); await act(async () => view.unmount());
});

test('citations from different answers do not share stale source tabs', () => {
  const { quoteIdentity } = load('side-workspace', mocks());
  const cite = { n: 1, doc_id: 'same', title: 'same', doc: 'same', text: 'same', context: '# 原文A' };
  assert.notEqual(quoteIdentity(cite), quoteIdentity({ ...cite, context: '# 原文B' }));
  assert.equal(quoteIdentity(cite), quoteIdentity({ ...cite }));
});

test('sidebar reports toolbar state and never overlays the conversation with a reopen button', async()=>{
  environment();const states=[];
  const {SideWorkspace,openTerminal,togglePreview}=load('side-workspace',mocks());let view;
  await act(async()=>{view=create(React.createElement(SideWorkspace,{...workspaceProps,onVisibilityChange:value=>states.push(value)}));});
  assert.deepEqual(states.at(-1),{open:false,available:false});
  await act(async()=>openTerminal());assert.deepEqual(states.at(-1),{open:true,available:true});
  await act(async()=>togglePreview());assert.deepEqual(states.at(-1),{open:false,available:true});
  assert.equal(view.root.findAllByProps({className:'desk-menu-item workspace-reopen'}).length,0);
  assert.equal(view.root.findByProps({'aria-label':'侧边工作区'}).props.hidden,true);
  await act(async()=>togglePreview());assert.equal(view.root.findByProps({'aria-label':'侧边工作区'}).props.hidden,false);
  await act(async()=>view.unmount());
});

test('terminal opens in the shared sidebar, preserves chat draft, and can be reopened', async () => {
  environment(); let requests = 0;
  const { SideWorkspace, openTerminal, togglePreview } = load('side-workspace', mocks());
  let view; await act(async () => { view = create(React.createElement(SideWorkspace, { ...workspaceProps, onRequest: () => requests++ })); });
  const draft = view.root.findByType('textarea');
  await act(async () => openTerminal()); assert.equal(requests, 1); assert.equal(view.root.findAllByProps({ 'data-terminal': '' }).length, 1);
  await act(async () => openTerminal()); assert.equal(view.root.findAllByProps({ 'data-terminal': '' }).length, 1);
  await act(async () => button(view, '收起').props.onClick()); assert.equal(view.root.findByType('textarea'), draft);
  await act(async () => togglePreview()); assert.equal(view.root.findAllByProps({ 'data-terminal': '' }).length, 1);
  await act(async () => view.unmount());
});

test('slash menu picks the actual category in All and shows purpose, source and selection separately', async () => {
  global.window = { addEventListener() {}, removeEventListener() {} };
  const { SlashMenu } = load('slash-menu'); let selected;
  let view; await act(async () => { view = create(React.createElement(SlashMenu, { open: true, parent: 'all', repoPicking: false, active: 0,
    items: [{ id: 's', label: '摘要', description: '压缩重点', source: '本地技能', parent: 'skill', picked: true }],
    error: '', loading: false, onParent() {}, onClose() {}, onBack() {}, onRetry() {}, onPick: (...args) => selected = args })); });
  const item = view.root.findByProps({ 'data-slash-item': '' });
  await act(async () => item.props.onClick()); assert.deepEqual(selected, ['s', 'skill']);
  assert(renderedText(item).includes('本地技能')); assert(renderedText(item).includes('已加入')); await act(async () => view.unmount());
});

test('RPC rejects disconnects, invalid replies and explicit timeouts instead of leaving controls waiting forever', async () => {
  const { rpc } = load('api'); let socket;
  global.WebSocket = class { constructor() { socket = this; } close() { this.onclose?.(); } send() {} };
  const info = { port: 1, token: 'test' };
  let pending = rpc(info, 'read'); socket.onclose(); await assert.rejects(pending, /连接已断开/);
  pending = rpc(info, 'read'); socket.onmessage({ data: '{bad' }); await assert.rejects(pending, /无法解析/);
  pending = rpc(info, 'read', {}, { timeoutMs: 5 }); await assert.rejects(pending, /超时/);
  pending = rpc(info, 'read'); socket.onmessage({ data: JSON.stringify({ type: 'rpc_result', result: { ok: true, result: { value: 42 } } }) });
  assert.deepEqual(await pending, { value: 42 });
});

test('notebook stream settles once on early close and retains already delivered text', () => {
  const { streamNotebook } = load('api'); let socket; const errors = [], tokens = [], done = [];
  global.WebSocket = class { constructor() { socket = this; } close() { this.onclose?.(); } send(value) { this.sent = JSON.parse(value); } };
  const start = () => streamNotebook({port: 1, token: 'test'}, 'session', 'question', ['doc'], {}, {
    onToken: text => tokens.push(text), onStatus() {}, onDone: value => done.push(value), onError: (...args) => errors.push(args),
  });
  start(); socket.onopen(); assert.equal(socket.sent.type, 'notebook');
  socket.onmessage({data: JSON.stringify({type:'token', data:'partial'})});
  socket.onclose(); socket.onerror();
  socket.onmessage({data: JSON.stringify({type:'done', data:JSON.stringify({answer:'late', cites:[]})})});
  assert.deepEqual(tokens, ['partial']); assert.equal(errors.length, 1); assert.match(errors[0][0], /后台可能继续/);
  assert.deepEqual(done, []);
  start(); socket.onerror(); socket.onclose(); assert.equal(errors.length, 2);
});

test('notebook stream rejects malformed events and keeps structured failure body without claiming completion', () => {
  const { streamNotebook } = load('api'); let socket;
  global.WebSocket = class { constructor() { socket = this; } close() { this.onclose?.(); } send() {} };
  for (const frame of ['{bad', JSON.stringify({type:'done',data:'{bad'}),
      JSON.stringify({type:'done',data:JSON.stringify({answer:'',cites:[]})}), JSON.stringify({type:'unknown',data:'bad'})]) {
    let failures=0;
    streamNotebook({port:1,token:'test'},'s','q',['d'],{}, {
      onToken() {},onStatus() {},onDone(){assert.fail('invalid completion');},onError(message){failures++;assert.match(message,/无法解析/);},
    });
    socket.onmessage({data:frame}); socket.onclose(); assert.equal(failures,1);
  }
  let failed;
  streamNotebook({port:1,token:'test'},'s','q',['d'],{}, {
    onToken() {},onStatus() {},onDone(){assert.fail('failure is not completion');},onError:(message,result)=>{failed={message,result};},
  });
  socket.onmessage({data:JSON.stringify({type:'error',data:'save unconfirmed',result:{answer:'full body',answer_complete:true,cites:[]}})});
  assert.equal(failed.result.answer,'full body');assert.equal(failed.message,'save unconfirmed');
  let count=0;
  streamNotebook({port:1,token:'test'},'s','q',['d'],{}, {
    onToken() {},onStatus() {},onError(){assert.fail('successful close is not an error');},onDone(result){count++;assert.equal(result.answer,'complete');},
  });
  socket.onmessage({data:JSON.stringify({type:'done',data:JSON.stringify({answer:'complete',cites:[]})})});
  socket.onclose(); assert.equal(count,1);
});

test('notebook titles are reading links, citations use complete stored Markdown, and batch/delete actions are explicit', async () => {
  let quoted, deleted = [], merged;
  const cite = { n: 1, doc: '来源', title: '原文', doc_id: 'd', text: '摘要', context: '# 完整片段\n**要点**' };
  const note = { id: 'n1', question: '技术要点', answer: '答案[1]', cites: [cite], saved: '2026-10-10', files: [{ name: '资料.md', path: 'fixture/资料.md' }] };
  const motion = new Proxy({}, { get: (_, tag) => React.forwardRef(({ children, initial, animate, transition, ...props }, ref) => React.createElement(tag, { ...props, ref }, children)) });
  const { NoteMode } = load('note', { 'framer-motion': { motion }, './side-workspace': { previewQuote: c => quoted = c },
    './Markdown': { Markdown: ({ text, onCite }) => React.createElement('article', {}, text, React.createElement('button', { onClick: () => onCite?.(1) }, '引用1')) } });
  let shared='';let view; await act(async () => { view = create(React.createElement(NoteMode, { sessionId: 'session', onCollaborate:id=>shared=id, book: { docs: [], turns: [], notes: [note] },
    checked: [], error: '', status: '', busy: false, saveStatus: '', saveBad: false, docUrl: '', onCheck() {}, onSave() {}, onDelete() {},
    onMarkdown() {}, onFeishu() {}, onOpenFile() {}, onRevealFile() {}, onDeleteFile: (...args) => deleted.push(args), onSummarize: ids => merged = ids })); });
  const title = view.root.findByProps({ 'data-note-answer-toggle': 'n1' }); assert.equal(title.type, 'a');
  assert.equal(view.root.findAllByProps({ 'data-note-summarize': '' }).length, 0);
  await act(async () => title.props.onClick({ preventDefault() {} }));
  assert.equal(view.root.findByProps({ 'data-note-reader': 'n1' }).type, 'article');
  await act(async()=>button(view,'作为资料交给伙伴').props.onClick());assert.equal(shared,'n1');
  await act(async () => button(view, '引用1').props.onClick()); assert.deepEqual(quoted, cite);
  await act(async () => button(view, '删除文件').props.onClick()); assert.equal(deleted.length, 0);
  await act(async () => button(view, '确认删除文件').props.onClick()); assert.deepEqual(deleted, [['n1', '资料.md']]);
  await act(async () => button(view, '多选').props.onClick());
  await act(async () => view.root.findAllByType('input').find(i => i.props.type === 'checkbox').props.onChange({ target: { checked: true } }));
  await act(async () => view.root.findByProps({ 'data-note-summarize': '' }).props.onClick()); assert.deepEqual(merged, ['n1']);
  await act(async () => view.unmount());
});

test('only trusted local webviews have capabilities, even when remote content shares the main window', () => {
  const capability = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../src-tauri/capabilities/default.json'), 'utf8'));
  assert.deepEqual(capability.webviews, ['pet', 'main', 'pet-chat']); assert(!capability.windows?.length); assert(!capability.remote);
});


test('open folder intent reaches project panel once and closing the tab clears it',async()=>{
 environment();const requests=[];
 const {SideWorkspace,openProject,openProjectFolder}=load('side-workspace',{
  ...mocks(async()=>{throw Error('Unexpected RPC');}),
  './project-panel':{ProjectPanel:props=>{requests.push(props.folderRequest);return React.createElement('div',{'data-project-panel':''},'项目目录');}}
 });
 let view;await act(async()=>{view=create(React.createElement(SideWorkspace,workspaceProps));});
 await act(async()=>openProjectFolder());assert.equal(requests.at(-1),1);
 await act(async()=>view.root.findByProps({'aria-label':'关闭 项目文件'}).props.onClick());
 await act(async()=>openProject());assert.equal(requests.at(-1),0);
 assert.equal(view.root.findByType('textarea').props.defaultValue,'保留草稿');
 await act(async()=>view.unmount());
});
