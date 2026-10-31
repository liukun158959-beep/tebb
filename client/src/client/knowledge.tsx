import { useEffect, useState, type ReactNode } from "react";

export type KnowledgeHit = {
  doc: string;
  title: string;
  score: number;
  text: string;
  context?: string;
};

export type KnowledgeTrace = {
  subagent: string;
  question: string;
  candidates: KnowledgeHit[];
  kept: KnowledgeHit[];
  answer?: string;
};

export type KnowledgeDoc = {
  id: string;
  title: string;
  url: string;
  chars: number;
  chunks: number;
  source?: string; space_id?: string; space_name?: string; folder_name?: string; local_path?: string;
};

export type KnowledgeChunk = {
  doc_id: string;
  doc: string;
  title: string;
  text: string;
  level?: string;
  context?: string;
};

export type KnowledgeModel = {
  role: "embed" | "rerank" | string;
  repo: string;
  blurb: string;
  ready: boolean;
  present: boolean;
  selected: boolean;
};

export type KnowledgeStrategy = {
  id: string;
  label: string;
  blurb: string;
};

export type KnowledgeSettings = {
  chunk_size: number;
  retrieve_k: number;
  rerank_n: number;
  embed_repo: string;
  rerank_repo: string;
  chunk_strategy: string;
  chunk_unit: string;
  overlap: number;
};

export type KnowledgeDownload = {
  active: boolean;
  repo: string;
  phase: string;
  bytes: number;
  total: number;
  percent: number;
  endpoint: string;
  error: string;
  seq: number;
};

export type KnowledgePayload = {
  ok?: boolean;
  error?: string;
  subagent?: string;
  embed_repo?: string;
  rerank_repo?: string;
  embed_ready?: boolean;
  rerank_ready?: boolean;
  model_dir?: string;
  chunk_size?: number;
  retrieve_k?: number;
  rerank_n?: number;
  indexed_chunk_size?: number;
  indexed_strategy?: string;
  indexed_unit?: string;
  indexed_overlap?: number;
  indexed_embed?: string;
  chunk_strategy?: string;
  chunk_unit?: string;
  overlap?: number;
  models?: KnowledgeModel[];
  strategies?: KnowledgeStrategy[];
  endpoint?: string;
  docs?: KnowledgeDoc[];
  chunks?: KnowledgeChunk[];
  trace?: KnowledgeTrace | null;
  downloads?: KnowledgeDownload[];
  download_seq?: number;
};

function formatMb(n: number): string {
  const mb = n / (1024 * 1024);
  if (mb >= 10) return `${Math.round(mb)} MB`;
  return `${mb.toFixed(1)} MB`;
}

export function mergeKnowledgePage(cur: KnowledgePayload | null, page: KnowledgePayload): KnowledgePayload {
  const prev = cur?.download_seq ?? 0;
  const next = page.download_seq ?? 0;
  if (next < prev && cur) {
    return { ...cur, trace: page.trace ?? cur.trace ?? null };
  }
  return { ...page, trace: page.trace ?? cur?.trace ?? null };
}

export function fuzzyHit(query: string, title: string): boolean {
  const q = query.trim().toLowerCase();
  const t = title.toLowerCase();
  if (!q) return true;
  if (t.includes(q)) return true;
  if (isSubseq(q, t)) return true;
  if (q.length >= 2) {
    for (let i = 0; i < q.length; i += 1) {
      const dropped = q.slice(0, i) + q.slice(i + 1);
      if (dropped && (t.includes(dropped) || isSubseq(dropped, t))) return true;
    }
  }
  return false;
}

function isSubseq(query: string, title: string): boolean {
  let index = 0;
  for (const ch of title) {
    if (ch === query[index]) index += 1;
    if (index >= query.length) return true;
  }
  return false;
}

function downloadLabel(row: KnowledgeDownload): string {
  if (row.phase === "listing") return `正在连接 ${row.endpoint}`;
  if (row.phase === "error") return row.error;
  if (row.phase === "done") return "下载完成";
  if (!row.total) return `正在下载 ${row.repo}`;
  return `${row.percent}% · ${formatMb(row.bytes)} / ${formatMb(row.total)}`;
}

export type FeishuDoc = { title: string; url: string; token: string; source?: string; space_id?: string; space_name?: string };

export function groupSources<T extends { source?: string; space_id?: string; space_name?: string; url?: string }>(items: T[]) {
  const groups = new Map<string, { id: string; name: string; items: T[] }>();
  for (const item of items) {
    const id = item.source === "local" ? `local:${item.space_name || "files"}` : item.space_id ? `wiki:${item.space_id}` : item.source === "wiki" || item.url?.includes("/wiki/") ? "wiki:unknown" : "drive";
    const name = item.space_name || (item.source === "local" ? "本地文件" : item.space_id ? `飞书知识库 · ${item.space_id}` : id === "wiki:unknown" ? "飞书知识库（归属待识别）" : "飞书云文档");
    const group = groups.get(id) || { id, name, items: [] };
    group.items.push(item); groups.set(id, group);
  }
  return [...groups.values()];
}

export function KnowledgeTraceView(props: { trace: KnowledgeTrace; showAnswer?: boolean }) {
  const trace = props.trace;
  return (
    <div data-knowledge-trace="" data-subagent={trace.subagent} className="mb-3 text-sm">
      <p className="font-semibold">{trace.subagent}</p>
      <TraceStep step="question" label="问题">
        <p data-knowledge-question={trace.question}>{trace.question}</p>
      </TraceStep>
      <TraceStep step="candidate" label={`向量检索 ${trace.candidates.length} 条`}>
        <HitList mark="candidate" rows={trace.candidates} />
      </TraceStep>
      <TraceStep step="kept" label={`重排留下 ${trace.kept.length} 条`}>
        <HitList mark="kept" rows={trace.kept} />
      </TraceStep>
      {props.showAnswer ? (
        <div className="mt-3">
          <p className="text-xs text-muted-foreground">回答</p>
          <p data-knowledge-answer="" className="mt-1 whitespace-pre-wrap">{trace.answer}</p>
        </div>
      ) : null}
    </div>
  );
}

function TraceStep(props: { step: string; label: string; children: ReactNode }) {
  return (
    <details className="knowledge-step" data-knowledge-step={props.step} data-open="0" onToggle={(ev) => {
      ev.currentTarget.dataset.open = ev.currentTarget.open ? "1" : "0";
    }}>
      <summary>{props.label}</summary>
      <div className="knowledge-step-body">{props.children}</div>
    </details>
  );
}

function HitList(props: { mark: "candidate" | "kept"; rows: KnowledgeHit[] }) {
  if (props.rows.length === 0) {
    return <p className="text-xs text-muted-foreground">没有片段。</p>;
  }
  return (
    <div className="space-y-2">
      {props.rows.map((row, idx) => (
        <div
          key={`${props.mark}-${idx}`}
          data-knowledge-hit={props.mark}
          data-doc={row.doc}
          data-title={row.title}
          data-score={row.score}
          className="border border-border px-2 py-1"
        >
          <p className="text-xs text-muted-foreground">
            {row.doc} · {row.title} · {row.score}
          </p>
          <p className="mt-1 whitespace-pre-wrap text-xs">{row.text}</p>
          {row.context ? <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">交给模型：{row.context}</p> : null}
        </div>
      ))}
    </div>
  );
}

export function KnowledgePane(props: {
  settings?: ReactNode;
  materials?: ReactNode;
  data: KnowledgePayload | null;
  catalog: FeishuDoc[];
  catalogError: string;
  busy: boolean;
  onDownload: (repo: string) => Promise<boolean>;
  onDeleteModel: (repo: string) => Promise<boolean>;
  onSave: (payload: KnowledgeSettings) => Promise<boolean>;
  onAdd: (id: string, label: string, source?: FeishuDoc) => Promise<{ ok: boolean; error: string }>;
  onLocal?: (kind: "file" | "folder") => void;
  onRefresh?: () => void;
  onDelete: (id: string) => Promise<{ ok: boolean; error: string }>;
  onRebuild: () => Promise<boolean>;
  onAsk: (text: string) => Promise<boolean>;
}) {
  const data = props.data;
  const failed = !data || data.ok === false;
  const [section, setSection] = useState(props.materials ? 'materials' : 'docs');
  const [query, setQuery] = useState("");
  const [catalogQuery, setCatalogQuery] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [addNote, setAddNote] = useState("");
  const [addBad, setAddBad] = useState(false);
  const [removeNote, setRemoveNote] = useState("");
  const [removeBad, setRemoveBad] = useState(false);
  const [removeWhere, setRemoveWhere] = useState<"library" | "catalog">("library");
  const [chunk, setChunk] = useState(String(data?.chunk_size ?? 800));
  const [retrieve, setRetrieve] = useState(String(data?.retrieve_k ?? 20));
  const [rerank, setRerank] = useState(String(data?.rerank_n ?? 4));
  const [strategy, setStrategy] = useState(data?.chunk_strategy || "structure");
  const [unit, setUnit] = useState(data?.chunk_unit || "chars");
  const [overlap, setOverlap] = useState(String(data?.overlap ?? 200));
  useEffect(() => {
    if (typeof data?.chunk_size === "number") setChunk(String(data.chunk_size));
    if (typeof data?.retrieve_k === "number") setRetrieve(String(data.retrieve_k));
    if (typeof data?.rerank_n === "number") setRerank(String(data.rerank_n));
    if (data?.chunk_strategy) setStrategy(data.chunk_strategy);
    if (data?.chunk_unit) setUnit(data.chunk_unit);
    if (typeof data?.overlap === "number") setOverlap(String(data.overlap));
  }, [data?.chunk_size, data?.retrieve_k, data?.rerank_n, data?.chunk_strategy, data?.chunk_unit, data?.overlap]);
  const [askText, setAskText] = useState("");
  const docs = failed ? [] : data.docs || [];
  const enriched = docs.map(doc => {
    const found = props.catalog.find(c => c.url === doc.id || c.token === doc.id || !!c.url && !!doc.url && c.url === doc.url);
    return found?.space_id && doc.source !== "local" ? { ...doc, source: found.source, space_id: found.space_id, space_name: found.space_name } : doc;
  });
  const shown = enriched.filter((doc) => fuzzyHit(query, doc.title) || !!doc.space_name && fuzzyHit(query, doc.space_name));
  const chunks = failed ? [] : data.chunks || [];
  async function removeDoc(id: string, title: string, where: "library" | "catalog") {
    setRemoveWhere(where);
    setRemoveBad(false);
    setRemoveNote(`正在移除《${title}》`);
    const result = await props.onDelete(id);
    if (!result.ok) {
      setRemoveBad(true);
      setRemoveNote(result.error || `《${title}》没有移除。`);
      return;
    }
    setRemoveBad(false);
    setRemoveNote(`已移除《${title}》`);
  }
  const models = failed ? [] : data.models || [];
  const strategies = failed ? [] : data.strategies || [];
  const strategyBlurb = strategies.find((item) => item.id === strategy)?.blurb || "";
  const stale = Boolean(
    data && data.ok !== false && data.indexed_chunk_size && (
      data.indexed_chunk_size !== data.chunk_size
      || data.indexed_strategy !== data.chunk_strategy
      || data.indexed_embed !== data.embed_repo
      || (data.chunk_strategy === "fixed" && data.indexed_unit !== data.chunk_unit)
      || (data.chunk_strategy === "overlap" && data.indexed_overlap !== data.overlap)
    ),
  );

  function settingsPayload(embedRepo: string, rerankRepo: string): KnowledgeSettings {
    return {
      chunk_size: Number(chunk),
      retrieve_k: Number(retrieve),
      rerank_n: Number(rerank),
      embed_repo: embedRepo,
      rerank_repo: rerankRepo,
      chunk_strategy: strategy,
      chunk_unit: unit,
      overlap: Number(overlap),
    };
  }

  function pickModel(model: KnowledgeModel) {
    const embedRepo = model.role === "embed" ? model.repo : (data?.embed_repo || "");
    const rerankRepo = model.role === "rerank" ? model.repo : (data?.rerank_repo || "");
    void props.onSave(settingsPayload(embedRepo, rerankRepo));
  }
  return (
    <div data-knowledge="" data-knowledge-ok={data ? (failed ? "0" : "1") : ""}>
      <h1 className="text-xl font-semibold">知识库</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        管理飞书与本地资料，在笔记中基于来源检索；笔记可以作为资料带回伙伴聊天，伙伴回答也能收藏为笔记。
      </p>
      <nav className="workspace-tabs mt-5" aria-label="知识库功能">{[...(props.materials ? [['materials','资料工作区']] : []),['docs','已索引资料'],['check','检索检查'],['index','索引设置']].map(([id,label])=><button type="button" key={id} aria-pressed={section===id} onClick={()=>setSection(id)}>{label}</button>)}</nav>
      {props.materials && <div hidden={section !== 'materials'}>{props.materials}</div>}
      {data?.error ? <p data-knowledge-error="" className="mt-3 text-sm text-destructive">{data.error}</p> : null}
      {data && data.ok !== false ? (
        <section hidden={section!=="index"} data-knowledge-settings="" className="mt-6 border border-border px-3 py-3 text-sm">
          <h2 className="font-semibold">索引与检索设置</h2>
          <p className="mt-2 text-xs text-muted-foreground">设置作用于全部资料范围；重建索引会逐项核对所有已索引来源的授权，任一失败则保留原索引。</p>
          <p className="mt-2">子代理 {data.subagent}</p>
          {data.endpoint ? (
            <p data-knowledge-endpoint="" className="mt-1 break-all text-muted-foreground">下载地址 {data.endpoint}</p>
          ) : null}
          <p data-knowledge-dir="" className="mt-1 break-all text-muted-foreground">{data.model_dir}</p>
          <ModelGroup
            title="向量模型"
            role="embed"
            models={models}
            downloads={data.downloads || []}
            busy={props.busy}
            onPick={pickModel}
            onDownload={props.onDownload}
            onDelete={props.onDeleteModel}
          />
          <ModelGroup
            title="重排模型"
            role="rerank"
            models={models}
            downloads={data.downloads || []}
            busy={props.busy}
            onPick={pickModel}
            onDownload={props.onDownload}
            onDelete={props.onDeleteModel}
          />
          <form
            className="mt-4 flex flex-wrap items-end gap-3"
            onSubmit={(ev) => {
              ev.preventDefault();
              void props.onSave(settingsPayload(data.embed_repo || "", data.rerank_repo || ""));
            }}
          >
            <label className="text-xs">
              切块策略
              <select data-knowledge-strategy="" value={strategy} onChange={(ev) => setStrategy(ev.target.value)} className="mt-1 block border border-border bg-card px-2 py-1">
                {strategies.map((item) => (
                  <option key={item.id} value={item.id}>{item.label}</option>
                ))}
              </select>
            </label>
            {strategyBlurb ? <p data-knowledge-strategy-blurb="" className="w-full text-xs text-muted-foreground">{strategyBlurb}</p> : null}
            {strategy === "fixed" ? (
              <div className="flex gap-3 text-xs">
                <label className="flex items-center gap-1">
                  <input data-knowledge-unit="chars" type="radio" name="chunk-unit" checked={unit === "chars"} onChange={() => setUnit("chars")} />
                  按字数
                </label>
                <label className="flex items-center gap-1">
                  <input data-knowledge-unit="tokens" type="radio" name="chunk-unit" checked={unit === "tokens"} onChange={() => setUnit("tokens")} />
                  按 token
                </label>
              </div>
            ) : null}
            {strategy === "overlap" ? (
              <label className="text-xs">
                重叠长度
                <input data-knowledge-overlap="" value={overlap} onChange={(ev) => setOverlap(ev.target.value)} className="mt-1 block w-24 border border-border bg-card px-2 py-1" />
              </label>
            ) : null}
            <label className="text-xs">
              切块大小
              <input data-knowledge-size="" value={chunk} onChange={(ev) => setChunk(ev.target.value)} className="mt-1 block w-24 border border-border bg-card px-2 py-1" />
            </label>
            <label className="text-xs">
              检索条数
              <input data-knowledge-retrieve="" value={retrieve} onChange={(ev) => setRetrieve(ev.target.value)} className="mt-1 block w-24 border border-border bg-card px-2 py-1" />
            </label>
            <label className="text-xs">
              重排保留
              <input data-knowledge-rerank="" value={rerank} onChange={(ev) => setRerank(ev.target.value)} className="mt-1 block w-24 border border-border bg-card px-2 py-1" />
            </label>
            <button type="submit" data-knowledge-save="" disabled={props.busy} className="desk-btn desk-btn-solid">
              保存设置
            </button>
            <button type="button" data-knowledge-rebuild="" disabled={props.busy} className="desk-btn" onClick={() => void props.onRebuild()}>
              重建索引
            </button>
          </form>
          {stale ? (
            <p className="mt-2 text-destructive">索引和当前切块或向量模型不一致。先重建索引再问。</p>
          ) : null}
        </section>
      ) : null}
      <div hidden={section!=="index"}>{props.settings}</div>
      <section hidden={section!=="docs"} className="mt-6">
        <div className="flex items-center gap-2"><h2 className="mr-auto text-sm font-semibold">文档库 · {docs.length} 篇</h2>
          <button className="desk-btn" disabled={props.busy} onClick={() => props.onLocal?.("file")}>本地文件</button>
          <button className="desk-btn" disabled={props.busy} onClick={() => props.onLocal?.("folder")}>本地文件夹</button></div>
        <input
          data-knowledge-query=""
          value={query}
          onChange={(ev) => setQuery(ev.target.value)}
          placeholder="按标题模糊查找已入库的文档"
          className="mt-2 w-full border border-border bg-card px-3 py-2 text-sm"
        />
        <div className="mt-3 space-y-2">
          {shown.length === 0 ? <p className="text-sm text-muted-foreground">库里没有匹配的文档。</p> : null}
          {groupSources(shown).map(group => <details key={group.id} data-knowledge-group={group.id} open className="rounded-lg border border-border p-3">
            <summary className="mb-2 cursor-pointer text-sm font-medium">{group.name} · {group.items.length} 篇</summary>
            {group.items.map((doc) => (
            <div key={doc.id} data-knowledge-doc="" data-id={doc.id} data-title={doc.title} className="flex items-start justify-between gap-3 border border-border px-3 py-2 text-sm">
              <div>
                <p>{doc.title}</p>
                <p className="text-xs text-muted-foreground">{doc.chars} 字 · {doc.chunks} 块</p>
              </div>
              <button type="button" data-knowledge-remove="" data-knowledge-remove-where="library" disabled={props.busy} className="shrink-0 desk-btn desk-btn-danger" onClick={() => void removeDoc(doc.id, doc.title, "library")}>
                移除
              </button>
            </div>
          ))}</details>)}
          {removeWhere === "library" && removeNote ? (
            <p data-knowledge-remove-status="" className={`text-xs ${removeBad ? "text-destructive" : "text-foreground"}`}>{removeNote}</p>
          ) : null}
        </div>
      </section>
      <section hidden={section!=="docs"} className="mt-6">
        <div className="flex items-center justify-between"><h2 className="text-sm font-semibold">从飞书添加</h2>
          <button className="desk-btn" disabled={props.busy} onClick={props.onRefresh}>刷新目录</button></div>
        {props.catalogError ? <p className="mt-2 text-sm text-destructive">{props.catalogError}</p> : null}
        <input
          data-knowledge-catalog-query=""
          value={catalogQuery}
          onChange={(ev) => setCatalogQuery(ev.target.value)}
          placeholder="按标题模糊查找可加入的文档"
          className="mt-2 w-full border border-border bg-card px-3 py-2 text-sm"
        />
        <div className="mt-3 space-y-2">
          {groupSources(props.catalog.filter((doc) => fuzzyHit(catalogQuery, doc.title) || !!doc.space_name && fuzzyHit(catalogQuery, doc.space_name))).map(group => <details key={group.id} data-knowledge-catalog-group={group.id} open className="rounded-lg border border-border p-3">
            <summary className="mb-2 cursor-pointer text-sm font-medium">{group.name} · {group.items.length} 篇
              <span className="ml-2 text-xs text-muted-foreground">已入库 {group.items.filter(doc => docs.some(item => item.id === doc.url || item.id === doc.token || !!doc.url && item.url === doc.url)).length}</span></summary>
            {group.items.map((doc) => {
            const id = doc.url || doc.token;
            const stored = docs.find((item) => item.id === id || item.id === doc.token || (doc.url && item.url === doc.url));
            if (stored) {
              return (
                <div key={id} data-knowledge-catalog="" data-id={stored.id} className="flex items-center justify-between gap-2 text-sm">
                  <span>{doc.title}<span className="ml-2 text-xs text-muted-foreground">已在库里</span></span>
                  <button type="button" data-knowledge-remove="" data-knowledge-remove-where="catalog" disabled={props.busy} className="shrink-0 desk-btn desk-btn-danger" onClick={() => void removeDoc(stored.id, doc.title, "catalog")}>
                    移除
                  </button>
                </div>
              );
            }
            return (
              <label key={id} data-knowledge-catalog="" data-id={id} className="flex items-center gap-2 text-sm">
                <input
                  data-knowledge-pick=""
                  type="checkbox"
                  checked={picked.includes(id)}
                  disabled={props.busy}
                  onChange={(ev) => {
                    setPicked((cur) => ev.target.checked ? [...cur, id] : cur.filter((item) => item !== id));
                  }}
                />
                <span>{doc.title}</span>
              </label>
            );
          })}</details>)}
        </div>
        <button
          type="button"
          data-knowledge-add=""
          disabled={props.busy || picked.length === 0}
          className="mt-3 desk-btn desk-btn-solid"
          onClick={() => {
            const chosen = props.catalog.filter((doc) => picked.includes(doc.url || doc.token));
            if (!chosen.length) {
              setAddBad(true);
              setAddNote("勾选的文档不在列表里。恢复：重新勾选后再加入。");
              return;
            }
            void (async () => {
              const added: string[] = [];
              for (let i = 0; i < chosen.length; i += 1) {
                const doc = chosen[i];
                const title = doc.title || "（无标题）";
                setAddBad(false);
                setAddNote(`正在加入 ${i + 1}/${chosen.length}《${title}》`);
                const result = await props.onAdd(doc.url || doc.token, doc.title, doc);
                if (!result.ok) {
                  setAddBad(true);
                  setAddNote(result.error || `《${title}》没有加入。`);
                  return;
                }
                added.push(title);
              }
              setPicked([]);
              setAddBad(false);
              setAddNote(added.length === 1 ? `已加入《${added[0]}》` : `已加入 ${added.length} 篇`);
            })();
          }}
        >
          {addNote.startsWith("正在加入") ? "正在加入" : "加入所选"}
        </button>
        {addNote ? (
          <p data-knowledge-add-status="" className={`mt-2 text-xs ${addBad ? "text-destructive" : "text-foreground"}`}>{addNote}</p>
        ) : null}
        {removeWhere === "catalog" && removeNote ? (
          <p data-knowledge-remove-status="" className={`mt-2 text-xs ${removeBad ? "text-destructive" : "text-foreground"}`}>{removeNote}</p>
        ) : null}
      </section>
      <section hidden={section!=="check"} className="mt-6">
        <h2 className="text-sm font-semibold">切块</h2>
        <div className="mt-3 max-h-64 space-y-2 overflow-y-auto">
          {chunks.length === 0 ? <p className="text-sm text-muted-foreground">还没有切块。</p> : null}
          {chunks.map((row, idx) => (
            <div key={`${row.doc_id}-${idx}`} data-knowledge-chunk="" data-doc={row.doc} data-title={row.title} data-level={row.level || "chunk"} className="border border-border px-3 py-2 text-sm">
              <p className="text-xs text-muted-foreground">{row.doc} · {row.title}{row.level && row.level !== "chunk" ? ` · ${row.level}` : ""}</p>
              <p className="mt-1 whitespace-pre-wrap">{row.text}</p>
            </div>
          ))}
        </div>
      </section>
      <section hidden={section!=="check"} className="mt-6">
        <h2 className="text-sm font-semibold">检索检查</h2><p className="mt-1 text-xs text-muted-foreground">检查来源召回与回答，不写入普通聊天或笔记历史。</p>
        <form
          className="mt-2 flex gap-2"
          onSubmit={(ev) => {
            ev.preventDefault();
            void props.onAsk(askText.trim());
          }}
        >
          <input
            data-knowledge-ask=""
            value={askText}
            onChange={(ev) => setAskText(ev.target.value)}
            placeholder="输入与你的资料有关的问题"
            className="flex-1 border border-border bg-card px-3 py-2 text-sm"
          />
          <button type="submit" data-knowledge-send="" disabled={props.busy} className="desk-btn desk-btn-lg desk-btn-solid">
            检索
          </button>
        </form>
        {data?.trace ? (
          <div className="mt-3">
            <KnowledgeTraceView trace={data.trace} showAnswer />
          </div>
        ) : null}
      </section>
    </div>
  );
}

function ModelGroup(props: {
  title: string;
  role: string;
  models: KnowledgeModel[];
  downloads: KnowledgeDownload[];
  busy: boolean;
  onPick: (model: KnowledgeModel) => void;
  onDownload: (repo: string) => Promise<boolean>;
  onDelete: (repo: string) => Promise<boolean>;
}) {
  const rows = props.models.filter((model) => model.role === props.role);
  if (!rows.length) return null;
  return (
    <div className="mt-4">
      <p className="font-semibold">{props.title}</p>
      <div className="mt-2 space-y-3">
        {rows.map((model) => {
          const job = props.downloads.find((item) => item.repo === model.repo && item.phase !== "idle");
          const active = job?.active === true;
          return (
            <div key={model.repo} data-knowledge-model={model.repo} data-selected={model.selected ? "1" : "0"} data-ready={model.ready ? "1" : "0"}>
              <label className="flex items-start gap-2">
                <input
                  data-knowledge-model-pick={model.repo}
                  type="radio"
                  name={`knowledge-${props.role}`}
                  checked={model.selected}
                  disabled={props.busy}
                  onChange={() => props.onPick(model)}
                />
                <span>
                  <span className="block">{model.repo}</span>
                  <span data-knowledge-model-blurb="" className="block text-xs text-muted-foreground">{model.blurb}</span>
                </span>
              </label>
              <div className="mt-1 flex items-center gap-2 pl-6">
                <button type="button" data-knowledge-download={model.repo} disabled={props.busy || active} className="desk-btn desk-btn-solid" onClick={() => void props.onDownload(model.repo)}>
                  {active ? "正在下载" : "下载"}
                </button>
                {model.present ? (
                  <button type="button" data-knowledge-model-delete={model.repo} disabled={props.busy || active} className="desk-btn desk-btn-danger" onClick={() => void props.onDelete(model.repo)}>
                    删除
                  </button>
                ) : null}
                <span className="text-xs text-muted-foreground">{model.ready ? "已下载" : "未下载"}</span>
              </div>
              {job ? <DownloadMeter download={job} /> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function DownloadMeter(props: { download: KnowledgeDownload }) {
  const download = props.download;
  return (
    <div className="mt-2 pl-6">
      <div
        data-knowledge-download-progress=""
        data-knowledge-phase={download.phase}
        data-knowledge-percent={download.percent}
        data-repo={download.repo}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={download.percent}
        className="relative h-1 overflow-hidden"
        style={{ background: "hsl(var(--border))" }}
      >
        <div
          className={download.phase === "listing" ? "knowledge-download-listing h-full" : "h-full"}
          style={
            download.phase === "listing"
              ? { background: "hsl(var(--primary))" }
              : { width: `${Math.min(100, download.percent)}%`, background: "hsl(var(--primary))" }
          }
        />
      </div>
      <p data-knowledge-download-label="" className={`mt-1 text-xs ${download.phase === "error" ? "text-destructive" : "text-foreground"}`}>
        {downloadLabel(download)}
      </p>
    </div>
  );
}
