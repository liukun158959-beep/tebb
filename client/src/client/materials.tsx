import { useEffect, useRef, useState } from 'react';
import { rpc, type BackendInfo } from './api';

type Source = { id: string; doc_id: string; title: string; locator?: string; kind?: string; revision?: string;
  project_id: string; freshness: string; indexed: boolean; index_version?: string; observed_version?: string;
  checked_at?: number; indexed_at?: string; error?: string; legacy?: boolean;
  operation?: { id: string; state: string; stage: string; error?: string } };
type Page = { ok: boolean; error?: string; change_state?: string; sources: Source[]; projects: { id: string; name: string }[] };
type Preview = { source_id: string; version: string; text: string; offset: number; next_offset: number | null; chars: number };
const names: Record<string, string> = { unknown: '尚未核对', not_indexed: '尚未索引', current: '与最近核对版本一致', stale: '索引待更新', missing: '原文件缺失', error: '读取失败', revoked: '授权已失效' };
const operationStates: Record<string, string> = { running: '正在处理', completed: '已完成', failed: '未完成', cancelled: '已取消' };
const stages: Record<string, string> = { reading: '读取正文', observed: '正文已读取', indexing: '计算索引', finished: '已收尾', cancelled: '已取消' };
const changes: Record<string, string> = { partial: '已部分变更', committed: '已提交', unconfirmed: '结果未确认', cancelled: '已取消', not_changed: '未变更' };

export function MaterialsPane({ info, debug, onIndexed }: { info: BackendInfo | null; debug?: boolean; onIndexed: () => void }) {
  const [scope, setScope] = useState(''), [page, setPage] = useState<Page | null>(null);
  const [busy, setBusy] = useState<{ source: string; operation: string } | null>(null);
  const [note, setNote] = useState(''), [error, setError] = useState('');
  const [locator, setLocator] = useState(''), [title, setTitle] = useState(''), [question, setQuestion] = useState('');
  const [hits, setHits] = useState<{ doc: string; text: string; source_id?: string; source_version?: string }[]>([]);
  const [preview, setPreview] = useState<(Preview & { label: string }) | null>(null);
  const [paging, setPaging] = useState(false);
  const serial = useRef(0), alive = useRef(true), currentScope = useRef(scope); currentScope.current = scope;
  const stopRequested = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; serial.current++; }; }, []);
  async function call<T>(payload: Record<string, unknown>) {
    if (debug) throw Error('演示页不读取真实资料。');
    if (!info) throw Error('本地后端尚未连接。');
    return rpc<T>(info, 'material_operation', { payload: { project_id: scope, ...payload } });
  }
  async function load(expected = scope) {
    try {
      const result = await call<Page>({ action: 'list', project_id: expected });
      if (!result.ok) throw Error(result.error || '资料列表读取失败');
      if (alive.current && currentScope.current === expected) setPage(result);
    } catch (err) { if (alive.current && currentScope.current === expected) setError(String(err)); }
  }
  useEffect(() => { serial.current++; setPage(null); setPreview(null); setHits([]); setError(''); setNote(''); void load(); }, [scope, info, debug]);
  useEffect(() => { if (!busy) return; const timer = setInterval(() => void load(), 1200); return () => clearInterval(timer); }, [busy, scope]);
  async function run(action: string, row?: Source, extra: Record<string, unknown> = {}) {
    const generation = ++serial.current, expected = scope, operation = crypto.randomUUID().replaceAll('-', '');
    stopRequested.current = false;
    setBusy({ source: row?.id || '', operation }); setError(''); setNote('');
    try {
      const result = await call<Page & { source?: Preview; retrieved?: { kept: typeof hits } }>({ action, source_id: row?.id, operation_id: operation, ...extra });
      if (!alive.current || serial.current !== generation || currentScope.current !== expected) return;
      if (stopRequested.current) { setNote('已停止等待，请核对原操作状态。'); return; }
      if (!result.ok) throw Error(`${result.error || '资料操作失败'}${result.change_state ? `（${changes[result.change_state] || '请查询实际状态'}，请核对后再操作）` : ''}`);
      if (result.source) setPreview({ ...result.source, label: action === 'read_current' ? '当前来源的本次读取快照' : '索引使用的历史版本' });
      if (result.retrieved) setHits(result.retrieved.kept);
      setNote(action === 'refresh' ? '单来源索引已提交。' : action === 'remove_index' ? '索引已移除，来源与原文件保留。' : '操作已完成，请以核对时间和版本为准。');
      if (['refresh', 'remove_index'].includes(action)) onIndexed();
    } catch (err) { if (alive.current && serial.current === generation) setError(String(err)); }
    finally { if (alive.current && serial.current === generation) { setBusy(null); await load(expected); } }
  }
  async function choose(kind: 'file' | 'folder', row?: Source) {
    if (debug || !info) { setError('请在已连接的正常客户端选择资料。'); return; }
    const generation = ++serial.current, expected = scope, operation = crypto.randomUUID().replaceAll('-', '');
    stopRequested.current = false;
    setBusy({ source: row?.id || '', operation }); setError(''); setNote('');
    let done = 0;
    try {
      const selected = await rpc<{ ok: boolean; error?: string; sources: { id: string; name: string; kind: string; files?: { id: string; name: string }[] }[]; errors?: { name: string; error: string }[] }>(info, 'pick_local_sources', { kind, request_id: operation });
      if (!selected.ok) throw Error(selected.error || '资料选择失败');
      const files = selected.sources.flatMap(s => s.kind === 'folder' ? s.files || [] : [s]);
      if (row && files.length !== 1) throw Error('重新定位请选择一个文件。');
      for (const file of files) {
        if (!alive.current || stopRequested.current || serial.current !== generation || currentScope.current !== expected) break;
        const result = await call<Page>({ action: row ? 'rebind' : 'register', source_id: row?.id, revision: row?.revision, snapshot_id: file.id, title: file.name });
        if (!result.ok) throw Error(result.error || '来源登记未完成');
        done++;
      }
      if (alive.current && serial.current === generation) setNote(`已${row ? '重新定位' : '登记'} ${done} 份资料；加入索引需单独点击。${selected.errors?.length ? `另有 ${selected.errors.length} 项读取错误。` : ''}`);
    } catch (err) { if (alive.current && serial.current === generation) { setError(String(err)); setNote(`本次已登记 ${done} 份；其余未继续。`); } }
    finally { if (alive.current && serial.current === generation) { setBusy(null); await load(expected); } }
  }
  async function cancel() {
    if (!busy || !info) return;
    stopRequested.current = true;
    try {
      if (busy.source) await call({ action: 'cancel', source_id: busy.source, operation_id: busy.operation });
      await rpc(info, 'cancel_local_source_request', { request_id: busy.operation });
      setNote('已请求取消；请核对原操作和索引状态，不会自动重放。');
    } catch (err) { setError(String(err)); }
  }
  async function more() {
    if (paging || !preview || preview.next_offset === null) return;
    setPaging(true);
    try {
      const result = await call<{ ok: boolean; error?: string; source: Preview }>({ action: 'read_version', source_id: preview.source_id, version: preview.version, offset: preview.next_offset });
      if (!result.ok) throw Error(result.error || '版本读取失败');
      setPreview(p => p && p.version === result.source.version ? { ...p, ...result.source, text: p.text + result.source.text } : p);
    } catch (err) { setError(String(err)); }
    finally { setPaging(false); }
  }
  return <section aria-label="资料工作区" className="my-5 space-y-3 rounded-lg border border-border p-4">
    <header className="flex flex-wrap items-center gap-3"><h2 className="font-semibold">资料工作区</h2>
      <label className="text-xs">范围 <select aria-label="资料范围" className="rounded border border-border bg-card p-1" disabled={!!busy} value={scope} onChange={e => setScope(e.target.value)}><option value="">全局资料（兼容历史）</option>{page?.projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      <button type="button" className="desk-btn" onClick={() => void load()}>重新读取状态</button>
    </header>
    <p className="text-xs text-muted-foreground">只登记明确选择的资料；手动核对与刷新。核对时间说明最近一次读取，不代表实时同步。项目范围不会自动使用全局资料。</p>
    <div className="flex flex-wrap gap-2"><button className="desk-btn" disabled={!!busy} onClick={() => void choose('file')}>登记本地文件</button><button className="desk-btn" disabled={!!busy} onClick={() => void choose('folder')}>登记文件夹资料</button>{busy && <button className="desk-btn" onClick={() => void cancel()}>取消本次操作</button>}</div>
    <details><summary className="cursor-pointer text-xs">登记飞书文档</summary><div className="mt-2 flex flex-wrap gap-2"><input aria-label="资料标题" placeholder="标题" value={title} onChange={e => setTitle(e.target.value)} className="rounded border border-border bg-card p-2 text-sm"/><input aria-label="飞书资料地址" placeholder="文档地址或 token" value={locator} onChange={e => setLocator(e.target.value)} className="min-w-0 flex-1 rounded border border-border bg-card p-2 text-sm"/><button className="desk-btn" disabled={!!busy || !locator.trim()} onClick={() => void run('register', undefined, { locator, title })}>登记</button></div></details>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}{note && <p role="status" className="text-sm">{note}</p>}
    {!page && !error && <p role="status" className="text-xs text-muted-foreground">正在读取资料登记…</p>}
    {page && !page.sources.length && <p className="text-sm text-muted-foreground">当前范围还没有登记资料。选择文件后可预览，再明确加入索引。</p>}
    <div className="space-y-3">{page?.sources.map(row => <article key={row.id} className="rounded border border-border p-3 text-sm">
      <h3 className="font-medium">{row.title}</h3><p className="break-all text-xs text-muted-foreground">{row.locator}</p>
      <p className="my-1 text-xs">{names[row.freshness] || row.freshness} · {row.checked_at ? `核对于 ${new Date(row.checked_at * 1000).toLocaleString()}` : '尚无核对时间'}{row.index_version ? ` · 索引版本 ${row.index_version.slice(0, 10)}` : row.indexed ? ' · 历史索引版本未知' : ''}</p>
      {row.error && <p className="text-xs text-destructive">{row.error}</p>}{row.operation && <p className="text-xs text-muted-foreground">{operationStates[row.operation.state] || '请核对状态'} · {stages[row.operation.stage] || '处理中'}{row.operation.error ? ` · ${row.operation.error}` : ''}</p>}
      <div className="mt-2 flex flex-wrap gap-2">
        <button className="desk-btn" disabled={!!busy} onClick={() => void run('check', row)}>检查来源</button><button className="desk-btn" disabled={!!busy} onClick={() => void run('refresh', row)}>{row.indexed ? '刷新此索引' : '加入索引'}</button>
        <button className="desk-btn" disabled={!!busy} onClick={() => void run('read_current', row)}>查看当前原文</button>
        {row.index_version && <button className="desk-btn" disabled={!!busy} onClick={() => void run('read_version', row, { version: row.index_version })}>查看索引版本</button>}
        {row.kind === 'local' && <button className="desk-btn" disabled={!!busy} onClick={() => void choose('file', row)}>重新定位</button>}
        {row.indexed && <button className="desk-btn" disabled={!!busy} onClick={() => void run('remove_index', row)}>仅移除索引</button>}
        {row.operation?.state === 'running' && !busy && <button className="desk-btn" onClick={() => void call({ action: 'cancel', source_id: row.id, operation_id: row.operation!.id }).then(() => load()).catch(err => setError(String(err)))}>取消原操作</button>}
      </div>
    </article>)}</div>
    {preview && <section aria-label="资料版本预览" className="rounded border border-border p-3"><div className="flex justify-between gap-2"><h3 className="text-sm">{preview.label} · {preview.version.slice(0, 10)}</h3><button className="desk-btn" onClick={() => setPreview(null)}>关闭预览</button></div><pre className="my-2 max-h-64 overflow-auto whitespace-pre-wrap text-xs">{preview.text}</pre>{preview.next_offset !== null && <button className="desk-btn" onClick={() => void more()}>继续读取此版本</button>}</section>}
    <details><summary className="cursor-pointer text-xs">检索当前范围</summary><div className="my-2 flex gap-2"><input aria-label="资料检索问题" value={question} onChange={e => setQuestion(e.target.value)} className="min-w-0 flex-1 rounded border border-border bg-card p-2 text-sm"/><button className="desk-btn" disabled={!!busy || !question.trim()} onClick={() => void run('retrieve', undefined, { question })}>检索片段</button></div><p className="text-xs text-muted-foreground">使用本机知识模型，只检索当前范围，不生成模型答复。</p>{hits.map((hit, i) => <div key={i} className="my-2 rounded border border-border p-2 text-xs"><p>{hit.doc} · {hit.source_version?.slice(0, 10) || '历史版本未知'}</p><p className="whitespace-pre-wrap">{hit.text}</p>{hit.source_id && <button className="desk-btn mt-1" disabled={!!busy} onClick={() => void run('read_current', { id: hit.source_id!, project_id: scope } as Source)}>核对当前来源</button>}</div>)}</details>
  </section>;
}
