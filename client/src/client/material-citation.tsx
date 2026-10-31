import { useEffect, useRef, useState } from 'react';
import { rpc, type BackendInfo } from './api';
import type { NoteCite } from './note';

export function MaterialCitation({ quote, info, debug }: { quote: NoteCite; info: BackendInfo | null; debug?: boolean }) {
  const [current, setCurrent] = useState<{ text: string; version: string; next_offset: number | null } | null>(null);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const generation = useRef(0), alive = useRef(true);
  useEffect(() => { generation.current++; setCurrent(null); setError(''); setBusy(false); }, [quote.source_id, quote.source_version, quote.doc_id]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; generation.current++; }; }, []);
  async function read(more = false) {
    if (!info || debug || !quote.source_id) { setError('请在已连接客户端的资料工作区核对来源。'); return; }
    const seq = ++generation.current; setBusy(true); setError('');
    try {
      const result = await rpc<{ ok: boolean; error?: string; source: { text: string; version: string; next_offset: number | null } }>(info, 'material_operation', {
        payload: { action: more ? 'read_version' : 'read_current', source_id: quote.source_id, project_id: quote.project_id || '',
          ...(more ? { version: current?.version, offset: current?.next_offset } : {}) },
      });
      if (!alive.current || seq !== generation.current) return;
      if (!result.ok) throw Error(result.error || '当前来源读取失败，引用快照保留。');
      setCurrent(previous => ({ ...result.source, text: more ? (previous?.text || '') + result.source.text : result.source.text }));
    } catch (err) { if (alive.current && seq === generation.current) setError(String(err)); }
    finally { if (alive.current && seq === generation.current) setBusy(false); }
  }
  return <section className="border-t border-border px-3 py-2 text-xs" aria-label="引用版本信息">
    <p>当前展示为保存的引用快照 · {quote.source_version ? `版本 ${quote.source_version.slice(0, 10)}` : '历史版本未知'}</p>
    {quote.source_id ? <button className="desk-btn my-2" disabled={busy} onClick={() => void read()}>核对当前来源</button> : <p className="my-1 text-muted-foreground">旧引用未记录来源编号，可在资料工作区手动核对；不会补造历史版本。</p>}
    {busy && <p role="status">正在核对来源…</p>}{error && <p role="alert" className="text-destructive">{error}</p>}
    {current && <div className="rounded border border-border p-2"><p>{current.version === quote.source_version ? '当前核对与引用版本一致' : '当前核对版本与引用不同或原版本未知'} · {current.version.slice(0, 10)}</p><pre className="my-2 max-h-48 overflow-auto whitespace-pre-wrap">{current.text}</pre>{current.next_offset !== null && <button className="desk-btn" disabled={busy} onClick={() => void read(true)}>继续读取当前核对版本</button>}</div>}
  </section>;
}
