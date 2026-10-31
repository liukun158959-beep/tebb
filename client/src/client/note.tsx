import { useRef, useState, type PointerEvent as ReactPointerEvent, type Ref, type ReactNode } from "react";
import { motion } from "framer-motion";
import { Markdown } from "./Markdown";
import { previewQuote } from "./side-workspace";
import { LoadingText } from "./loading-text";
import { WorkspaceDialog } from "./workspace-dialog";

export type NoteCite = {
  source_id?: string; source_version?: string; project_id?: string; indexed_at?: string;
  n: number;
  doc: string;
  title: string;
  text: string;
  doc_id: string;
  context?: string;
};

export type NoteTurn = {
  role: string;
  text: string;
  cites?: NoteCite[];
};

export type NoteFile = {
  name: string;
  path: string;
  saved: string;
};

export type NoteItem = {
  id: string;
  question: string;
  answer: string;
  cites: NoteCite[];
  saved: string;
  files: NoteFile[];
};

export type NoteDoc = {
  id: string;
  title: string;
  chars: number;
  chunks: number;
};

export type NoteSession = {
  id: string;
  title: string;
  updated: string;
  turns: NoteTurn[];
  notes: NoteItem[];
};

export type NotebookPage = {
  change_state?: string;
  ok?: boolean;
  error?: string;
  docs?: NoteDoc[];
  sessions?: NoteSession[];
  session_id?: string;
  url?: string;
  path?: string;
  message?: string;
};

const columnEase = [0.22, 1, 0.36, 1] as const;
function savedTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function NoteMode(props: {
  children?: ReactNode;
  onAddSources?: () => void;
  sessionId: string;
  book: { docs: NoteDoc[]; turns: NoteTurn[]; notes: NoteItem[] } | null;
  error: string;
  busy: boolean;
  status: string;
  checked: string[];
  onCheck: (id: string, on: boolean) => void;
  onSave: (index: number) => void;
  onDelete: (id: string) => void;
  onMarkdown: (id: string) => void;
  onFeishu: (id: string) => void;
  onOpenFile: (id: string, name: string) => void;
  onRevealFile: (id: string, name: string) => void;
  onDeleteFile: (id: string, name: string) => void;
  onSummarize: (noteIds: string[]) => void;
  onCollaborate?: (noteId: string) => void;
  saveStatus: string;
  saveBad: boolean;
  docUrl: string;
  threadRef?: Ref<HTMLDivElement>;
}) {
  const book = props.book;
  const docs = book?.docs || [];
  const turns = book?.turns || [];
  const notes = book?.notes || [];
  const boardRef = useRef<HTMLDivElement>(null);
  const [sourceW, setSourceW] = useState(224);
  const [notesW, setNotesW] = useState(256);
  const [readerId, setReaderId] = useState<string | null>(null);
  const [batch, setBatch] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [confirmFile, setConfirmFile] = useState<string | null>(null);
  const [pickedAnswers, setPickedAnswers] = useState<string[]>([]);
  const picked = notes.filter((note) => pickedAnswers.includes(note.id));
  const reader = notes.find(note => note.id === readerId) || null;

  function dragColumn(side: "source" | "notes", ev: ReactPointerEvent<HTMLButtonElement>) {
    ev.preventDefault();
    const board = boardRef.current;
    if (!board) return;
    try {
      ev.currentTarget.setPointerCapture(ev.pointerId);
    } catch {
      // 抓不到指针时仍靠 window 上的移动事件收尾。
    }
    const origin = ev.clientX;
    const startSource = sourceW;
    const startNotes = notesW;
    const move = (e: PointerEvent) => {
      const total = board.getBoundingClientRect().width;
      const dx = e.clientX - origin;
      const handles = 12;
      const centerMin = 220;
      if (side === "source") {
        const max = Math.max(160, total - startNotes - centerMin - handles);
        setSourceW(Math.round(Math.min(Math.max(160, startSource + dx), max)));
        return;
      }
      const max = Math.max(180, total - startSource - centerMin - handles);
      setNotesW(Math.round(Math.min(Math.max(180, startNotes - dx), max)));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  return (
    <><div ref={boardRef} className="note-board min-h-0 flex-1" data-note-board="" data-note-session={props.sessionId}
      style={{ gridTemplateColumns: `${sourceW}px 6px minmax(0,1fr) 6px ${notesW}px` }}>
      <motion.aside
        className="shrink-0 overflow-y-auto border-r border-border px-3 py-4"
        style={{ width: sourceW }}
        data-note-sources=""
        data-note-source-width={sourceW}
        initial={{ opacity: 0, x: -18 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.38, ease: columnEase }}
      >
        <div className="flex items-center justify-between gap-2"><h2 className="text-sm font-semibold">来源</h2>{props.onAddSources&&<button type="button" className="desk-menu-item" disabled={props.busy} onClick={props.onAddSources}>＋ 添加资料</button>}</div>
        {docs.length === 0 ? <p className="mt-3 text-xs text-muted-foreground">尚无来源，点击添加资料，可选择已有知识库、飞书文档或本地文件。</p> : null}
        <div className="mt-3 space-y-2">
          {docs.map((doc) => {
            const on = props.checked.includes(doc.id);
            return (
              <label key={doc.id} data-note-source={doc.id} data-checked={on ? "1" : "0"} className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={on}
                  disabled={props.busy}
                  onChange={(ev) => props.onCheck(doc.id, ev.target.checked)}
                />
                <span>
                  {doc.title}
                  <span className="mt-1 block text-xs text-muted-foreground">{doc.chars} 字 · {doc.chunks} 块</span>
                </span>
              </label>
            );
          })}
        </div>
      </motion.aside>
      <button
        type="button"
        aria-orientation="vertical"
        data-note-resize="sources"
        aria-label="拖来源栏"
        className="w-1.5 shrink-0 cursor-col-resize border-0 bg-border p-0 hover:bg-primary/50"
        onPointerDown={(ev) => dragColumn("source", ev)}
      />
      <motion.div
        className="flex min-w-0 flex-1 flex-col"
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.38, delay: 0.05, ease: columnEase }}
      >
        <div ref={props.threadRef} className="flex-1 space-y-3 overflow-y-auto px-6 py-4" data-note-thread="">

          {turns.length === 0 ? <p className="text-sm text-muted-foreground">勾选来源后，在下面提问。回答只用来源里的原文。</p> : null}
          {turns.map((turn, index) => (
            <article key={`${turn.role}-${index}`} data-note-turn="" data-role={turn.role} className="border border-border px-3 py-2 text-sm">
              <p className="text-xs text-muted-foreground">{turn.role === "user" ? "问题" : "回答"}</p>
              {turn.role === "pet" && turn.text ? <CitedText text={turn.text} cites={turn.cites || []} /> : null}
              {turn.role === "pet" && !turn.text && props.status ? (
                <p data-note-status="" className="mt-1 text-sm text-muted-foreground" role="status"><LoadingText text={props.status} active={props.busy} /></p>
              ) : null}
              {turn.role === "user" ? <p className="mt-1 whitespace-pre-wrap">{turn.text}</p> : null}
              {turn.role === "pet" && turn.text && turn.cites && turn.cites.length ? (
                <button
                  type="button"
                  data-note-save=""
                  disabled={props.busy}
                  className="mt-2 desk-btn desk-btn-solid"
                  onClick={() => props.onSave(index)}
                >
                  存成笔记
                </button>
              ) : null}
            </article>
          ))}
          {props.error ? <p data-note-error="" className="text-sm text-destructive">{props.error}</p> : null}
        </div>
        {props.children}
      </motion.div>
      <button
        type="button"
        aria-orientation="vertical"
        data-note-resize="notes"
        aria-label="拖笔记栏"
        className="w-1.5 shrink-0 cursor-col-resize border-0 bg-border p-0 hover:bg-primary/50"
        onPointerDown={(ev) => dragColumn("notes", ev)}
      />
      <motion.aside
        className="shrink-0 overflow-y-auto border-l border-border px-3 py-4"
        style={{ width: notesW }}
        data-note-notes=""
        data-note-notes-width={notesW}
        initial={{ opacity: 0, x: 18 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.38, delay: 0.08, ease: columnEase }}
      >
        <div className="flex items-center justify-between gap-2"><h2 className="text-sm">已存笔记 <span className="text-xs text-muted-foreground">{notes.length}</span></h2><button className="desk-menu-item" aria-pressed={batch} onClick={() => { setBatch(value => !value); setPickedAnswers([]); }}>{batch ? "完成" : "多选"}</button></div>
        {batch && <div className="note-batch"><p className="text-xs text-muted-foreground">已选择 {picked.length} 条笔记</p><button
          type="button"
          data-note-summarize=""
          data-note-summarize-count={picked.length}
          disabled={props.busy || picked.length === 0}
          className="desk-menu-item"
          onClick={() => props.onSummarize(picked.map((note) => note.id))}
        >
          总结成一份文档
        </button></div>}
        {notes.length === 0 ? <p className="mt-3 text-xs text-muted-foreground">还没有笔记。回答下面可以存成笔记。</p> : null}
        <div className="mt-3 space-y-2">
          {notes.map((note) => {
            const open = note.id === readerId;
            return (
            <article key={note.id} data-note-item={note.id} data-note-answer={note.id} data-open={open ? "1" : "0"} className={`note-directory-row ${open ? "active" : ""}`}>
              <div className="mt-1 flex items-start gap-2">
                {batch && <input
                  type="checkbox"
                  aria-label={note.question}
                  data-note-answer-pick={note.id}
                  checked={pickedAnswers.includes(note.id)}
                  disabled={props.busy}
                  onChange={(ev) => {
                    const on = ev.target.checked;
                    setPickedAnswers((cur) => on ? [...cur, note.id] : cur.filter((item) => item !== note.id));
                  }}
                />}
                <a
                  href={`#note-${note.id}`}
                  data-note-answer-toggle={note.id}
                  aria-current={open ? "page" : undefined}
                  className="note-directory-title"
                  onClick={event => { event.preventDefault(); setReaderId(note.id); setConfirmDelete(null); }}
                >
                  <h3 data-note-theme="">{note.question}</h3>
                </a>
              </div>
              <p className="mt-1 text-xs text-muted-foreground" title={note.saved}>{savedTime(note.saved)} · {note.cites.length} 条引用{note.files?.length ? ` · ${note.files.length} 个文件` : ""}</p>
            </article>
            );
          })}
        </div>
        {props.saveStatus ? (
          <p data-note-save-status="" className={`mt-3 text-xs ${props.saveBad ? "text-destructive" : "text-foreground"}`}>{props.saveStatus}</p>
        ) : null}
        {props.docUrl ? <p data-note-doc-url={props.docUrl} className="mt-2 break-all text-xs text-primary">{props.docUrl}</p> : null}
      </motion.aside>
    </div>{reader && <WorkspaceDialog title="阅读笔记" onClose={() => setReaderId(null)}><article className="note-reader" data-note-reader={reader.id}>
            <div className="flex flex-wrap items-start justify-between gap-2"><div><p className="text-xs text-muted-foreground" title={reader.saved}>已存笔记 · {savedTime(reader.saved)}</p><h1 className="mt-2 text-xl">{reader.question}</h1></div>
              <details className="note-more"><summary>更多 ⌄</summary><div><button className="desk-menu-item" disabled={props.busy} onClick={() => props.onMarkdown(reader.id)}>导出 Markdown</button><button className="desk-menu-item" disabled={props.busy} onClick={() => props.onFeishu(reader.id)}>保存到飞书文档</button><button className="desk-menu-item text-destructive" disabled={props.busy} onClick={() => setConfirmDelete(reader.id)}>删除笔记</button></div></details></div>
            <button className="desk-menu-item my-3" onClick={() => setReaderId(null)}>关闭阅读，返回问答</button>
            {props.onCollaborate && <button className="desk-menu-item" disabled={props.busy} onClick={() => props.onCollaborate?.(reader.id)}>作为资料交给伙伴</button>}
            {props.saveStatus && <p className={`mb-3 text-xs ${props.saveBad ? "text-destructive" : "text-muted-foreground"}`} role="status"><LoadingText text={props.saveStatus} active={props.busy} /></p>}
            {confirmDelete === reader.id && <div className="note-delete-confirm" role="alert"><p>删除这条笔记？原始来源不会删除。</p><button className="desk-menu-item text-destructive" disabled={props.busy} onClick={() => { props.onDelete(reader.id); setConfirmDelete(null); }}>删除</button><button className="desk-menu-item" onClick={() => setConfirmDelete(null)}>取消</button></div>}
            <CitedText text={reader.answer} cites={reader.cites} onPreview={()=>setReaderId(null)} />
            {!!reader.files?.length && <details className="note-files"><summary>关联文件 · {reader.files.length}</summary>{reader.files.map(file => <div key={file.name} data-note-file={file.name} data-note-file-path={file.path} className="py-2">
              <p className="text-sm">{file.name}</p><p className="break-all text-xs text-muted-foreground">{file.path}</p>
              <div className="flex flex-wrap gap-2"><button className="desk-menu-item" disabled={props.busy} onClick={() => {props.onOpenFile(reader.id, file.name);setReaderId(null);}}>侧栏预览</button><button className="desk-menu-item" disabled={props.busy} onClick={() => props.onRevealFile(reader.id, file.name)}>所在文件夹</button><button className="desk-menu-item text-destructive" disabled={props.busy} onClick={() => setConfirmFile(`${reader.id}:${file.name}`)}>删除文件</button></div>
              {confirmFile === `${reader.id}:${file.name}` && <div role="alert" className="note-delete-confirm"><p>删除关联文件 {file.name}？笔记正文保留。</p><button className="desk-menu-item text-destructive" disabled={props.busy} onClick={() => { props.onDeleteFile(reader.id, file.name); setConfirmFile(null); }}>确认删除文件</button><button className="desk-menu-item" onClick={() => setConfirmFile(null)}>取消</button></div>}
            </div>)}</details>}
          </article></WorkspaceDialog>}</>
  );
}

function CitedText(props: { text: string; cites: NoteCite[]; onPreview?:()=>void }) {
  return (
    <div className="mt-1" data-note-markdown-body="">
      <Markdown text={props.text} cites={props.cites.map((item) => item.n)} onCite={n => { const quote = props.cites.find(item => item.n === n); if (quote) {props.onPreview?.();previewQuote(quote);} }} />
    </div>
  );
}
