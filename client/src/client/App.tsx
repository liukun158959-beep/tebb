import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { Fragment, lazy, Suspense, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { Button, Card, CardBody } from "reend-components";
import { useBoardActions } from "./board-actions";
import { shouldReloadBoard, type BoardPayload } from "./board";
import {
  BOARD_FIXTURE,
  BOARD_NOW,
  COMPOSER_FIXTURE,
  DEBUG_CONTEXT,
  DEBUG_FIXTURE,
  KNOWLEDGE_CATALOG,
  KNOWLEDGE_FIXTURE,
  NOTE_FIXTURE,
  MEMORY_FIXTURE,
  FEISHU_LOGGED_OUT,
  FEISHU_WAITING,
  STATUS_FIXTURE,
  debugPane,
  debugRequested,
  installDeskDebug,
} from "./debug";
import { ContextCard, ContextSplit, MemoryPane, modelVerbatim, splitIndex, type ContextView, type MemoryPayload } from "./memory";
import { KnowledgePane, KnowledgeTraceView, mergeKnowledgePage, type FeishuDoc, type KnowledgePayload } from "./knowledge";
import { NoteMode, type NotebookPage, type NoteDoc, type NoteSession } from "./note";
import { FeishuPane, feishuLoggedIn, feishuWaiting, type FeishuSnap } from "./feishu";
import { FeishuAgentPane } from "./feishu-agent";
import { AgentMonitor } from "./agent-monitor";
import { AgentDebugDialog } from "./agent-debug";
import { WorkspaceRuns } from "./workspace-runs";
import { NotebookSources } from "./notebook-sources";
import { CalendarWorkspace } from "./calendar-workspace";
import type { ScheduleTarget } from "./schedule";
import { WorkBoardPane } from "./work-board";
import { navigateWorkspace } from "./workspace-navigation";
import { WorkspaceRail } from "./workspace-shell";
import { PageStack } from "./page-stack";
import { PetSettings } from "./pet-settings";
import { ModelNetworkSettings } from './model-network';
import { RailButton, readDeveloperMode, saveDeveloperMode } from "./companion-ui";
import { TopToolbar } from "./top-toolbar";
import "./workspace-navigation.css";
import { LocalSourcesDialog, type LocalSource } from "./local-sources";
import { SideWorkspace, previewSource, openTerminal, openProject, openProjectFolder, togglePreview } from "./side-workspace";
import { LoadingText } from "./loading-text";
import { AutomationPane } from "./automation";
import { VideoPane, WikiConnectionSettings } from "./video";
import { StatusGrid, loadingStatuses, statusError, statusFromPayload, STATUS_KINDS, type StatusKind, type StatusView } from "./status";
import { Markdown, MdLink, openableHref, PlainLinks } from "./Markdown";
import { DEFAULT_SAMPLING, samplingFromInputs, EFFORTS, type Sampling } from "./sampling";
import { startupFailed, startupReady, startupStatus } from "./startup";
import { SetupGuide, type SetupStatus } from "./onboarding";
import { ReleaseIntro, ReleaseSettings, useReleaseIntro } from "./release";
import {
  backendInfo,
  rpc,
  streamChat,
  streamNotebook,
  type BackendInfo,
  type ChatItem,
  type SessionItem,
} from "./api";
import {
  filterSlash,
  NEED_REPO,
  MCP_SEP,
  SlashMenu,
  SLASH_PARENTS,
  slashQuery,
  stripSlash,
  type ComposerOptions,
  type SlashItem,
  type SlashParent,
  type SlashSkill,
  type SlashTool,
} from "./slash-menu";

type SideList = { ok: boolean; error?: string; items: SlashItem[] } | null;
import {
  clearStoredBackground,
  BackgroundCrop,
  readBackgroundFile,
  readStoredBackground,
  MODELS_FIXTURE,
  PERSONA_FIXTURE,
  SettingsPane,
  storeBackground,
  type ModelEntry,
  type ModelList,
  type ModelPublic,
  type PersonaPublic,
} from "./settings";

const LiveCollaboration = lazy(() => import("./live-collaboration").then(module => ({ default: module.LiveCollaboration })));
type Pane = "work" | "chat" | "board" | "feishu" | "settings" | "automation" | "monitor" | "video" | "collaboration";

const debugKind = debugPane();
const boardDebug = debugKind === "board" || debugKind === "feishu" || debugKind === "automation" || debugKind === "monitor" || debugKind === "video";

type Thread = {
  sessionId: string;
  sessions: SessionItem[];
  items: ChatItem[];
  context: ContextView | null;
};

export function App() {
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [showGuide, setShowGuide] = useState(false);
  const [dark, setDark] = useState(() => { try { return localStorage.getItem("tebb.theme") !== "light"; } catch { return true; } });
  const [sidebarState, setSidebarState] = useState({ open: false, available: false });
  const [settingsSection,setSettingsSection] = useState("assistant");
  const [workspaceModalOpen,setWorkspaceModalOpen] = useState(false);
  useEffect(()=>{const dialogs=new Set<string>();const change=(event:Event)=>{const {id,open}=(event as CustomEvent<{id:string;open:boolean}>).detail;if(open)dialogs.add(id);else dialogs.delete(id);setWorkspaceModalOpen(dialogs.size>0);};window.addEventListener('tebb-workspace-dialog',change);return()=>window.removeEventListener('tebb-workspace-dialog',change);},[]);
  const [noteSourcesOpen, setNoteSourcesOpen] = useState(false);
  const [notebookCreating,setNotebookCreating] = useState(false);
  const [notebookNeedsCheck,setNotebookNeedsCheck] = useState(false);
  const notebookCreatingRef=useRef(false);
  const [workTarget, setWorkTarget] = useState<{id:string;nonce:number}|null>(null);
  const [calendarTarget, setCalendarTarget] = useState<(ScheduleTarget&{view?:'today'|'week'|'day'})|null>(null);
  const [composeRequest, setComposeRequest] = useState<{ id: number; text: string }>();
  const [developerMode, setDeveloperMode] = useState(readDeveloperMode);
  const [agentDebugOpen, setAgentDebugOpen] = useState(false);
  const [storedBg] = useState(() => readStoredBackground());
  const [bgUrl, setBgUrl] = useState(storedBg.url);
  const [bgError, setBgError] = useState(storedBg.error);
  const [cropSrc, setCropSrc] = useState("");
  const [cropError, setCropError] = useState("");
  const releaseIntro = useReleaseIntro(!!setupStatus && !setupStatus.show && !showGuide && !cropSrc && !debugKind);
  const [modelItems, setModelItems] = useState<ModelEntry[]>(() => (debugKind ? MODELS_FIXTURE.items : []));
  const [activeModelId, setActiveModelId] = useState(() => (debugKind ? MODELS_FIXTURE.active : ""));
  const [editingModelId, setEditingModelId] = useState(() => (debugKind ? MODELS_FIXTURE.active : ""));
  const [modelForm, setModelForm] = useState<ModelPublic | null>(() => {
    if (!debugKind) return null;
    const item = MODELS_FIXTURE.items.find((row) => row.id === MODELS_FIXTURE.active);
    return item ? { ok: true, base_url: item.base_url, model: item.model, has_key: item.has_key } : null;
  });
  const [personaForm, setPersonaForm] = useState<PersonaPublic | null>(null);
  const [settingsError, setSettingsError] = useState("");
  const [settingsNotice, setSettingsNotice] = useState("");
  const [personaError, setPersonaError] = useState("");
  const [personaNotice, setPersonaNotice] = useState("");
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [keyEpoch, setKeyEpoch] = useState(0);
  const applyBgRef = useRef<(url: string) => void>(() => {});
  const clearBgRef = useRef<() => void>(() => {});
  const [pane, setPane] = useState<Pane>(
    debugKind === "work" || debugKind === "automation" || debugKind === "monitor" || debugKind === "video"
      ? debugKind
      : debugKind === "feishu"
        ? "feishu"
        : debugKind === "board"
          ? "board"
          : debugKind === "settings"
            ? "settings"
            : debugKind ? "chat" : "collaboration",
  );
  const [info, setInfo] = useState<BackendInfo | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [draft, setDraft] = useState("");
  const draftRef = useRef("");
  const historyAt = useRef<number | null>(null);
  const draftStash = useRef<string | null>(null);
  const userLinesRef = useRef<string[]>([]);
  const fillDraftRef = useRef<(text: string) => void>(() => {});
  const [sampling, setSampling] = useState<Sampling>(DEFAULT_SAMPLING);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [board, setBoard] = useState<BoardPayload | null>(boardDebug ? BOARD_FIXTURE : null);
  const [boardClock, setBoardClock] = useState<string | null>(debugKind ? BOARD_NOW : null);
  const [statuses, setStatuses] = useState<StatusView[]>(boardDebug ? STATUS_FIXTURE : []);
  const [chatFace, setChatFace] = useState<"thread" | "memory" | "knowledge" | "notebooks">(
    debugKind === "memory" ? "memory" : debugKind === "knowledge" ? "knowledge" : "thread",
  );
  const [memory, setMemory] = useState<MemoryPayload | null>(debugKind === "memory" ? MEMORY_FIXTURE : null);
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [memoryBusy, setMemoryBusy] = useState(false);
  const [memoryFormError, setMemoryFormError] = useState("");
  const [knowledge, setKnowledge] = useState<KnowledgePayload | null>(debugKind === "knowledge" ? KNOWLEDGE_FIXTURE : null);
  const [knowledgeCatalog, setKnowledgeCatalog] = useState<FeishuDoc[]>(debugKind === "knowledge" ? KNOWLEDGE_CATALOG : []);
  const [knowledgeCatalogError, setKnowledgeCatalogError] = useState("");
  const [knowledgeBusy, setKnowledgeBusy] = useState(false);
  const [useKnowledge, setUseKnowledge] = useState(false);
  const [noteSessionId, setNoteSessionId] = useState<string | null>(debugKind === "note" ? "note-1" : null);
  const [noteSessions, setNoteSessions] = useState<NoteSession[]>(debugKind === "note" ? NOTE_FIXTURE.sessions : []);
  const [noteDocs, setNoteDocs] = useState<NoteDoc[]>(debugKind === "note" ? NOTE_FIXTURE.docs : []);
  const [noteChecked, setNoteChecked] = useState<string[]>(debugKind === "note" ? NOTE_FIXTURE.docs.map((doc) => doc.id) : []);
  const [noteBusy, setNoteBusy] = useState(false);
  const [noteStatus, setNoteStatus] = useState("");
  const [noteError, setNoteError] = useState("");
  const [noteListError, setNoteListError] = useState("");
  const [noteSave, setNoteSave] = useState("");
  const [noteSaveBad, setNoteSaveBad] = useState(false);
  const [noteDocUrl, setNoteDocUrl] = useState("");

  useEffect(() => {
    if (debugPane()) return;
    if (!info || !(knowledge?.downloads || []).some((item) => item.active)) return;
    let stopped = false;
    const id = window.setInterval(() => {
      void rpc<KnowledgePayload>(info, "load_knowledge")
        .then((page) => {
          if (stopped) return;
          if (page.ok === false) {
            setKnowledge((cur) => ({ ...(cur || {}), error: page.error || "知识库状态没有读出来。" }));
            return;
          }
          setKnowledge((cur) => mergeKnowledgePage(cur, page));
        })
        .catch((err) => {
          if (stopped) return;
          setKnowledge((cur) => ({ ...(cur || {}), error: String(err) }));
        });
    }, 500);
    return () => {
      stopped = true;
      window.clearInterval(id);
    };
  }, [info, knowledge?.downloads]);
  const [feishu, setFeishu] = useState<FeishuSnap | null>(debugKind === "feishu" ? FEISHU_LOGGED_OUT : null);
  const [feishuBusy, setFeishuBusy] = useState(false);
  const feishuBusyRef = useRef(false);
  const openFeishuRef = useRef<() => void>(() => {});
  const rememberFeishuRef = useRef<(snap: FeishuSnap) => void>(() => {});
  const statusGen = useRef(0);
  const boardSeen = useRef(false);
  const { loading: boardLoading, loadError: boardLoadError, rowDeleting, agendaErrors, taskErrors,
    loadBoardSnapshot, deleteAgenda, deleteTask } =
    useBoardActions({ info, boardClock, setBoard, onLoaded: () => setBoardClock(null) });
  const openBoardRef = useRef<(refresh?: boolean) => void>(() => {});
  const [preview, setPreview] = useState<ChatItem[] | null>(debugRequested() ? DEBUG_FIXTURE : null);
  const scroller = useRef<HTMLDivElement>(null);
  const notesRef = useRef<string[]>([]);
  const thinkingRef = useRef("");
  const [thinkPulse, setThinkPulse] = useState(false);
  const [slashParent, setSlashParent] = useState<SlashParent>("all");
  const [composerNotice, setComposerNotice] = useState("");
  const sessionSwitchBusy = useRef(false);
  const [slashIndex, setSlashIndex] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState<string | null>(null);
  const [repoPicking, setRepoPicking] = useState(false);
  const [pendingRepoTool, setPendingRepoTool] = useState<SlashTool | null>(null);
  const [pickedSkills, setPickedSkills] = useState<SlashSkill[]>([]);
  const [pickedTools, setPickedTools] = useState<SlashTool[]>([]);
  const [pickedDocs, setPickedDocs] = useState<SlashItem[]>([]);
  const [pickedAttachments, setPickedAttachments] = useState<LocalSource[]>([]);
  const [localDialog, setLocalDialog] = useState<"file" | "folder" | null>(null);
  const [localDialogMinimized, setLocalDialogMinimized] = useState(false);
  const [pickedMcp, setPickedMcp] = useState<SlashItem[]>([]);
  const [pickedRepo, setPickedRepo] = useState("");
  const [composer, setComposer] = useState<ComposerOptions | null>(null);
  const [composerError, setComposerError] = useState("");
  const [docCatalog, setDocCatalog] = useState<SideList>(null);
  const [mcpCatalog, setMcpCatalog] = useState<SideList>(null);
  const debugMode = preview !== null;
  const shownItems = preview || thread?.items || [];
  const shownContext = debugMode ? DEBUG_CONTEXT : thread?.context || null;
  const shownCovered = shownContext?.covered || [];
  const compressSplit = splitIndex(shownItems, shownCovered);
  const shownVerbatim = modelVerbatim(shownItems, shownCovered);
  const slashNeedle = slashQuery(draft);
  const menuOpen = (slashNeedle !== null && slashDismissed !== draft) || repoPicking;
  const menuKeyRef = useRef<(ev: KeyboardEvent) => void>(() => {});
  const slashIndexRef = useRef(0);
  const catalogGen = useRef(0);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    document.documentElement.classList.toggle("light", !dark);
    try { localStorage.setItem("tebb.theme", dark ? "dark" : "light"); } catch { /* theme stays active */ }
  }, [dark]);

  useEffect(() => {
    slashIndexRef.current = 0;
    setSlashIndex(0);
  }, [slashNeedle, slashParent, repoPicking]);

  useEffect(() => {
    if (menuOpen) return;
    catalogGen.current++;
    setDocCatalog(null);
    setMcpCatalog(null);
  }, [menuOpen]);

  useEffect(() => {
    if (!menuOpen || !["all", "doc", "mcp"].includes(slashParent)) return;
    if (debugMode) {
      if (!docCatalog) {
        setDocCatalog({
          ok: true,
          items: [
            { id: "https://example.feishu.cn/docx/doc-token", label: "本周复盘" },
            { id: "https://example.feishu.cn/docx/doc-token-2", label: "今日总结" },
          ],
        });
      }
      if (!mcpCatalog) {
        setMcpCatalog({
          ok: true,
          items: [{ id: `demo${MCP_SEP}ping`, label: "demo / ping", description: "看服务是否活着" }],
        });
      }
      return;
    }
    if (slashParent === "doc" && docCatalog || slashParent === "mcp" && mcpCatalog || slashParent === "all" && docCatalog && mcpCatalog) return;
    if (!info) {
      const failed = { ok: false, error: "还没有连上本地后端。恢复：重启客户端。", items: [] };
      if (slashParent === "doc" || slashParent === "all") setDocCatalog(failed);
      if (slashParent === "mcp" || slashParent === "all") setMcpCatalog(failed);
      return;
    }
    const parent = slashParent === "all" ? !docCatalog ? "doc" : "mcp" : slashParent;
    const gen = catalogGen.current + 1;
    catalogGen.current = gen;
    const method = parent === "doc" ? "list_feishu_docs" : "list_mcp_tools";
    rpc<{ ok: boolean; error?: string; items: { id?: string; label?: string; title?: string; url?: string; token?: string; description?: string }[] }>(
      info,
      method,
      {},
      { timeoutMs: 90000 },
    )
      .then((data) => {
        if (catalogGen.current !== gen) return;
        if (!data.ok) {
          const failed = { ok: false, error: data.error || "读取失败。", items: [] as SlashItem[] };
          if (parent === "doc") setDocCatalog(failed);
          else setMcpCatalog(failed);
          return;
        }
        if (parent === "doc") {
          setDocCatalog({
            ok: true,
            error: data.error,
            items: (data.items || []).map((row) => ({
              id: row.url || row.token || "",
              label: row.title || row.label || "（无标题）",
            })),
          });
          return;
        }
        setMcpCatalog({
          ok: true,
          error: data.error,
          items: (data.items || []).map((row) => ({
            id: row.id || "",
            label: row.label || "",
            description: row.description,
          })),
        });
      })
      .catch((err: unknown) => {
        if (catalogGen.current !== gen) return;
        const failed = { ok: false, error: String(err), items: [] as SlashItem[] };
        if (parent === "doc") setDocCatalog(failed);
        else setMcpCatalog(failed);
      });
  }, [menuOpen, slashParent, debugMode, docCatalog, mcpCatalog, info]);

  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => menuKeyRef.current(ev);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!menuOpen || composer || composerError) return;
    if (debugMode) {
      setComposer(COMPOSER_FIXTURE);
      return;
    }
    if (!info) {
      setComposerError("还没有连上本地后端。恢复：重启客户端。");
      return;
    }
    let canceled = false;
    rpc<ComposerOptions>(info, "load_composer_options", {}, { timeoutMs: 90000 })
      .then((data) => {
        if (canceled) return;
        if (!data.ok) throw new Error(data.error || "菜单读取失败。");
        setComposer(data);
      })
      .catch((err: unknown) => { if (!canceled) setComposerError(String(err)); });
    return () => { canceled = true; };
  }, [menuOpen, composer, composerError, debugMode, info]);

  useEffect(() => {
    if (!isTauri()) return;
    let stop = () => {};
    void listen("open-today", () => {
      setCalendarTarget({view:'today'});
      openBoardRef.current(false);
    }).then((unlisten) => {
      stop = unlisten;
    });
    return () => stop();
  }, []);

  async function loadThread(backend: BackendInfo) {
    const data = await rpc<{
      ok?: boolean;
      error?: string;
      session_id: string;
      items: ChatItem[];
      sessions: SessionItem[];
      context?: ContextView;
    }>(backend, "load_chat_log", {}, { timeoutMs: 90000 });
    if (data.ok === false) throw new Error(data.error || "会话读取失败。");
    setThread({
      sessionId: data.session_id,
      sessions: data.sessions,
      items: data.items,
      context: data.context || null,
    });
  }

  function formFromEntry(item: ModelEntry): ModelPublic {
    return { ok: true, base_url: item.base_url, model: item.model, has_key: item.has_key };
  }

  function rememberModels(data: ModelList, editId = "") {
    setModelItems(data.items);
    setActiveModelId(data.active);
    const nextEdit = data.items.some((item) => item.id === editId) ? editId : data.active;
    setEditingModelId(nextEdit);
    const item = data.items.find((row) => row.id === nextEdit);
    setModelForm(item ? formFromEntry(item) : null);
    setKeyEpoch((n) => n + 1);
  }

  useEffect(() => {
    installDeskDebug({
      seed: setPreview,
      seedBoard: (payload, nowIso) => {
        setPane("board");
        setBoard(payload);
        setBoardClock(nowIso);
      },
      seedStatus: (raw) => {
        setPane("board");
        setStatuses(STATUS_KINDS.map((kind) => statusFromPayload(kind, raw[kind])));
      },
      openBoard: (refresh = false) => openBoardRef.current(refresh),
      requestToday: () => {setCalendarTarget({view:'today'});openBoardRef.current(false);},
      openFeishu: () => openFeishuRef.current(),
      seedFeishu: (snap) => rememberFeishuRef.current(snap),
      seedMemory: (payload) => {
        setPane("chat");
        setChatFace("memory");
        setMemory(payload);
        setMemoryFormError("");
      },
      shouldReloadBoard,
      setSampling: (effort, temperature, topP) => {
        setSampling(samplingFromInputs(effort, temperature, topP));
      },
      pulseThinking: setThinkPulse,
      setGenerating: setBusy,
      fillDraft: (text) => fillDraftRef.current(text),
      openSettings: () => setPane("settings"),
      setBackground: (url) => applyBgRef.current(url),
      clearBackground: () => clearBgRef.current(),
      openBackgroundCrop: (url) => openCropRef.current(url),
    });
    if (debugPane()) {
      if (debugPane() === "chat" || debugPane() === "note") {
        setThread({
          sessionId: "s-current",
          items: [],
          context: DEBUG_CONTEXT,
          sessions: [
            { id: "s-current", title: "今天干什么", updated: "", count: 2 },
            { id: "s-other", title: "另一段", updated: "", count: 1 },
          ],
        });
      }
      startupReady();
      return;
    }
    startupStatus("正在连接本地助手…");
    backendInfo()
      .then(async (backend) => {
        setInfo(backend);
        startupStatus("正在读取会话…");
        await loadThread(backend);
        await loadNotebooks(backend);
        const models = await rpc<ModelList>(backend, "load_models");
        if (!models.ok) throw new Error(models.error || "模型列表读取失败。");
        setModelItems(models.items);
        setActiveModelId(models.active);
        const setup = await rpc<SetupStatus>(backend, "load_onboarding");
        setSetupStatus(setup);
        setShowGuide(setup.show);
        startupReady();
      })
      .catch((err: unknown) => {
        setError(String(err));
        startupFailed(err);
      });
  }, []);

  useEffect(() => {
    if (pane !== "settings") return;
    if (debugKind) {
      setPersonaForm(PERSONA_FIXTURE);
      setSettingsError("");
      setPersonaError("");
      return;
    }
    if (!info) {
      setSettingsError("还没有连上本地后端。恢复：重启客户端。");
      return;
    }
    let gone = false;
    void (async () => {
      try {
        const data = await rpc<ModelList>(info, "load_models");
        if (gone) return;
        if (!data.ok) throw new Error(data.error || "模型列表读取失败。");
        rememberModels(data);
        setSettingsError("");
      } catch (err) {
        if (!gone) setSettingsError(String(err));
      }
      try {
        const data = await rpc<PersonaPublic>(info, "load_persona");
        if (gone) return;
        if (!data.ok) throw new Error(data.error || "系统提示词读取失败。");
        setPersonaForm(data);
        setPersonaError("");
      } catch (err) {
        if (!gone) setPersonaError(String(err));
      }
    })();
    return () => {
      gone = true;
    };
  }, [pane, info]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight });
  }, [thread?.items, noteSessions, noteSessionId, noteStatus, status]);

  async function newSession() {
    if (busy || noteBusy || sessionSwitchBusy.current) { setComposerNotice("当前任务还在进行，请完成或停止后再创建会话。"); return; }
    if (!info || debugPane()) { setComposerNotice(debugPane() ? "演示页不创建真实会话。" : "后端尚未连接，请稍后重试。"); return; }
    sessionSwitchBusy.current = true; setError(""); setComposerNotice("正在创建会话…");
    try {
      const created = await rpc<{ session_id: string }>(info, "new_chat_session", {}, { timeoutMs: 60000 });
      if (!created.session_id) throw new Error("未收到新会话编号，请检查会话列表后再试。");
      await loadThread(info); setNoteSessionId(null); setChatFace("thread"); setComposerNotice("新会话已创建，输入草稿保留。");
    } catch (err) { setError(String(err)); setComposerNotice(String(err)); }
    finally { sessionSwitchBusy.current = false; }
  }

  async function openSession(sessionId: string) {
    if (busy || noteBusy || sessionSwitchBusy.current) { setComposerNotice("当前任务还在进行，请完成或停止后再切换会话。"); return; }
    if (debugPane() || !info) { setComposerNotice(debugPane() ? "演示页不切换真实会话。" : "后端尚未连接，请稍后重试。"); return; }
    if (sessionId === thread?.sessionId) { setNoteSessionId(null); setChatFace("thread"); setComposerNotice("已打开当前会话。"); return; }
    sessionSwitchBusy.current = true; setError(""); setComposerNotice("正在切换会话…");
    try {
      await rpc(info, "switch_chat_session", { session_id: sessionId }, { timeoutMs: 60000 });
      await loadThread(info); setNoteSessionId(null); setChatFace("thread"); setComposerNotice("会话已切换，输入草稿保留。");
    } catch (err) { setError(String(err)); setComposerNotice(String(err)); }
    finally { sessionSwitchBusy.current = false; }
  }

  function send() {
    if (sessionSwitchBusy.current) { setComposerNotice("正在切换会话，请稍后发送。"); return; }
    if (noteSessionId) {
      sendNote();
      return;
    }
    const text = stripSlash(draft);
    const skills = pickedSkills.map((item) => item.id);
    const cli = pickedTools.map((item) => item.id);
    const docs = pickedDocs.map((item) => ({ id: item.id, label: item.label }));
    const attachments = pickedAttachments.map(item => item.id);
    const mcp = pickedMcp.map((item) => {
      const cut = item.id.indexOf(MCP_SEP);
      return {
        server: cut < 0 ? "" : item.id.slice(0, cut),
        tool: cut < 0 ? "" : item.id.slice(cut + MCP_SEP.length),
        label: item.label,
      };
    });
    const needsRepo = pickedTools.some((item) => NEED_REPO.has(item.id));
    if (busy) return;
    if (!text && skills.length === 0 && cli.length === 0 && docs.length === 0 && mcp.length === 0 && !attachments.length) return;
    if (needsRepo && !pickedRepo) {
      setError("选了 GitHub 近况或路线图时必须同时选一个已列出的仓库。");
      return;
    }
    if (!activeModelId) {
      setError("还没有可选模型。打开设置添加一个。");
      return;
    }
    if (!info || !thread) return;
    if (useKnowledge && attachments.length) { setError("本地附件请先加入知识库，或关闭知识库后直接提问。"); return; }
    const chips = { skills, cli, github: needsRepo ? pickedRepo : "", docs, mcp, attachments };
    historyAt.current = null;
    draftStash.current = null;
    setDraft("");
    setPickedSkills([]);
    setPickedTools([]);
    setPickedDocs([]);
    setPickedAttachments([]);
    setPickedMcp([]);
    setPickedRepo("");
    setRepoPicking(false);
    setPendingRepoTool(null);
    setSlashDismissed(null);
    setBusy(true);
    setError("");
    setStatus("");
    notesRef.current = [];
    thinkingRef.current = "";
    const sent = sampling;
    const shown = userShown(text, docs);
    setThread({
      ...thread,
      items: [...thread.items, { role: "user", text: shown }, { role: "pet", text: "" }],
    });
    streamChat(info, text, sent, chips, activeModelId, {
      onStatus: (line) => {
        if (!line) return;
        setStatus("");
        notesRef.current = [...notesRef.current, line];
        const notes = notesRef.current;
        setThread((cur) => {
          if (!cur) return cur;
          const items = cur.items.slice();
          const last = items[items.length - 1];
          if (!last || last.role !== "pet") return cur;
          items[items.length - 1] = { ...last, notes };
          return { ...cur, items };
        });
      },
      onThink: (piece) => {
        setStatus("");
        thinkingRef.current += piece;
        const thinking = thinkingRef.current;
        setThread((cur) => {
          if (!cur) return cur;
          const items = cur.items.slice();
          const last = items[items.length - 1];
          if (!last || last.role !== "pet") return cur;
          items[items.length - 1] = { ...last, thinking };
          return { ...cur, items };
        });
      },
      onKnowledge: (trace) => {
        if (!trace) return;
        setThread((cur) => {
          if (!cur) return cur;
          const items = cur.items.slice();
          const last = items[items.length - 1];
          if (!last || last.role !== "pet") return cur;
          items[items.length - 1] = { ...last, knowledge: trace };
          return { ...cur, items };
        });
      },
      onToken: (piece) => {
        setStatus("");
        setThread((cur) => {
          if (!cur) return cur;
          const items = cur.items.slice();
          const last = items[items.length - 1];
          if (last && last.role === "pet") {
            items[items.length - 1] = { ...last, text: last.text + piece };
          }
          return { ...cur, items };
        });
      },
      onDone: async () => {
        const notes = notesRef.current.slice();
        const thinking = thinkingRef.current;
        setStatus("");
        setBusy(false);
        await loadThread(info);
        if (notes.some((line) => line.includes("在创建日程")) && boardSeen.current && boardClock === null) {
          await loadBoardSnapshot(false);
        }
        if (!notes.length && !thinking) return;
        setThread((cur) => {
          if (!cur) return cur;
          const items = cur.items.slice();
          for (let i = items.length - 1; i >= 0; i -= 1) {
            if (items[i].role === "pet") {
              items[i] = {
                ...items[i],
                notes: notes.length ? notes : items[i].notes,
                thinking: thinking || items[i].thinking,
              };
              break;
            }
          }
          return { ...cur, items };
        });
      },
      onError: (message) => {
        setStatus("");
        setBusy(false);
        setError(message);
      },
    }, useKnowledge);
  }

  function takeNotebook(page: NotebookPage): boolean {
    if (page.ok === false) {
      setNoteListError(page.error || "笔记没有打开。");
      return false;
    }
    if (!Array.isArray(page.docs) || !Array.isArray(page.sessions)) {
      setNoteListError("笔记列表缺字段。恢复：重启客户端后再开笔记。");
      return false;
    }
    setNoteDocs(page.docs);
    setNoteSessions(page.sessions);
    setNoteListError("");
    return true;
  }

  async function loadNotebooks(backend: BackendInfo) {
    try {
      const page = await rpc<NotebookPage>(backend, "load_notebook");
      return takeNotebook(page);
    } catch (err) {
      setNoteListError(String(err));
      return false;
    }
  }

  useEffect(() => {
    if (!info) return;
    const refresh = () => { void loadNotebooks(info); };
    window.addEventListener('tebb-notebooks-changed', refresh);
    return () => window.removeEventListener('tebb-notebooks-changed', refresh);
  }, [info]);

  async function shareNoteWithCompanions(id: string) {
    if (!info || noteBusy || !noteSessionId) return;
    setNoteBusy(true); setNoteSaveBad(false);
    try {
      const result = await rpc<{ok:boolean; sources:LocalSource[]; errors?:unknown[]}>(info, 'prepare_notebook_source', {session_id:noteSessionId, note_id:id});
      if (!result.ok || !result.sources?.length || result.errors?.length) throw new Error('笔记资料准备失败，请重试。');
      setPickedAttachments(old => [...new Map([...old,...result.sources].map(source => [source.id,source])).values()]);
      setNoteSessionId(null); setPane('collaboration');
      setComposeRequest({id:Date.now(),text:'请根据所附笔记继续处理：'});
    } catch (error) { setNoteSaveBad(true); setNoteSave(String(error)); }
    finally { setNoteBusy(false); }
  }

  function openNoteSession(id: string) {
    setChatFace("thread");
    setPane("chat");
    setNoteSessionId(id);
    setNoteListError("");
    setNoteError("");
    setNoteSave("");
    setNoteSaveBad(false);
    setNoteDocUrl("");
    setNoteStatus("");
    setNoteChecked(noteDocs.map((doc) => doc.id));
  }

  async function newNotebook() {
    if(notebookCreatingRef.current)return;
    if(notebookNeedsCheck)return;
    if (busy || noteBusy) {
      setNoteListError("当前内容正在处理，完成后再新建笔记。");
      return;
    }
    if (debugPane()) {
      setNoteListError("调试页只看样本，不开新笔记。");
      return;
    }
    if (!info) {
      setNoteListError("还没有连上本地后端。恢复：重启客户端。");
      return;
    }
    setNoteListError("");
    notebookCreatingRef.current=true;setNotebookCreating(true);
    try {
      const page = await rpc<NotebookPage>(info, "new_notebook");
      if (!takeNotebook(page) || !page.session_id) {
        setNotebookNeedsCheck(page.ok!==false||page.change_state==='unconfirmed'||page.change_state==='committed');
        if (!page.session_id && page.ok !== false) setNoteListError("新笔记没有 id。恢复：再点一次新笔记。");
        return;
      }
      setChatFace("thread");
      setPane("chat");
      setNoteSessionId(page.session_id);
      setNoteError("");
      setNoteSave("");
      setNoteDocUrl("");
      setNoteChecked((page.docs || []).map((doc) => doc.id));
    } catch (err) {
      setNoteListError(String(err));
      setNotebookNeedsCheck(true);
    } finally {notebookCreatingRef.current=false;setNotebookCreating(false);}
  }

  function patchNote(sessionId: string, mapTurns: (turns: NoteSession["turns"]) => NoteSession["turns"], title?: string) {
    setNoteSessions((cur) => cur.map((session) => {
      if (session.id !== sessionId) return session;
      return { ...session, title: title || session.title, turns: mapTurns(session.turns) };
    }));
  }

  function sendNote() {
    const text = draft.trim();
    const sessionId = noteSessionId;
    if (!sessionId || busy || noteBusy || !text) return;
    if (!noteChecked.length) {
      setNoteError("先勾选来源再问。");
      return;
    }
    if (debugPane()) {
      setDraft("");
      setNoteError("");
      patchNote(sessionId, (turns) => [...turns, { role: "user", text }]);
      setNoteError("调试页只看样本，不检索。");
      return;
    }
    if (!info) {
      setNoteError("还没有连上本地后端。恢复：重启客户端。");
      return;
    }
    if (!activeModelId) {
      setNoteError("还没有可选模型。打开设置添加一个。");
      return;
    }
    setDraft("");
    setNoteError("");
    setNoteSave("");
    setNoteDocUrl("");
    setNoteStatus("正在检索来源");
    setNoteBusy(true);
    const current = noteSessions.find((item) => item.id === sessionId);
    patchNote(
      sessionId,
      (turns) => [...turns, { role: "user", text }, { role: "pet", text: "" }],
      current?.title === "新笔记" ? text.slice(0, 24) : undefined,
    );
    let tokens = 0;
    streamNotebook(info, sessionId, text, noteChecked, sampling, {
      onStatus: (line) => {
        if (line) setNoteStatus(line);
      },
      onToken: (piece) => {
        tokens += piece.length;
        setNoteStatus("");
        patchNote(sessionId, (turns) => {
          const next = turns.slice();
          const last = next[next.length - 1];
          if (last && last.role === "pet") next[next.length - 1] = { ...last, text: last.text + piece };
          return next;
        });
      },
      onDone: (payload) => {
        setNoteBusy(false);
        setNoteStatus("");
        patchNote(sessionId, (turns) => {
          const next = turns.slice();
          const last = next[next.length - 1];
          if (last && last.role === "pet") {
            next[next.length - 1] = { ...last, text: payload.answer || last.text, cites: payload.cites || [] };
          }
          return next;
        });
      },
      onError: (message, result) => {
        setNoteBusy(false);
        setNoteStatus("");
        setNoteError(message);
        if (typeof result?.answer === 'string' && result.answer) {
          patchNote(sessionId, (turns) => {
            const next = turns.slice();
            const last = next[next.length - 1];
            if (last?.role === 'pet') next[next.length - 1] = { ...last, text: result.answer!,
              cites: result.answer_complete && Array.isArray(result.cites) ? result.cites : [] };
            return next;
          });
          return;
        }
        if (tokens) return;
        patchNote(sessionId, (turns) => {
          const next = turns.slice();
          const last = next[next.length - 1];
          if (last && last.role === "pet" && !last.text) next.pop();
          return next;
        });
      },
    });
  }

  async function saveNote(index: number) {
    const sessionId = noteSessionId;
    const session = noteSessions.find((item) => item.id === sessionId);
    const turns = session?.turns || [];
    const turn = turns[index];
    const question = [...turns.slice(0, index)].reverse().find((item) => item.role === "user")?.text || "";
    if (!sessionId || !turn || turn.role !== "pet" || !question) {
      setNoteSaveBad(true);
      setNoteSave("这条回答对不上问题，没有存成笔记。");
      return;
    }
    if (debugPane()) {
      setNoteSaveBad(true);
      setNoteSave("调试页只看样本，不存笔记。");
      return;
    }
    if (!info) {
      setNoteSaveBad(true);
      setNoteSave("还没有连上本地后端。恢复：重启客户端。");
      return;
    }
    setNoteBusy(true);
    setNoteSaveBad(false);
    setNoteSave("正在存成笔记");
    try {
      const page = await rpc<NotebookPage>(info, "save_notebook_note", {
        session_id: sessionId,
        question,
        answer: turn.text,
        cites: turn.cites || [],
      });
      if (!takeNotebook(page)) {
        setNoteSaveBad(true);
        setNoteSave(page.error || "笔记没有存成。");
        return;
      }
      setNoteSaveBad(false);
      setNoteSave("已存成笔记");
    } catch (err) {
      setNoteSaveBad(true);
      setNoteSave(String(err));
    } finally {
      setNoteBusy(false);
    }
  }

  async function summarizeNotes(noteIds: string[]) {
    const sessionId = noteSessionId;
    if (!sessionId) {
      setNoteSaveBad(true);
      setNoteSave("没有打开笔记会话。恢复：从侧边栏重新打开。");
      return;
    }
    if (!noteIds.length) {
      setNoteSaveBad(true);
      setNoteSave("先勾选要总结的回答。");
      return;
    }
    if (debugPane()) {
      setNoteSaveBad(true);
      setNoteSave("调试页只看样本，不总结。");
      return;
    }
    if (!info) {
      setNoteSaveBad(true);
      setNoteSave("还没有连上本地后端。恢复：重启客户端。");
      return;
    }
    setNoteBusy(true);
    setNoteSaveBad(false);
    setNoteDocUrl("");
    setNoteSave("正在总结勾选的回答");
    try {
      const page = await rpc<NotebookPage>(info, "summarize_notebook", {
        session_id: sessionId,
        note_ids: noteIds,
        sampling,
      });
      if (Array.isArray(page.docs) && Array.isArray(page.sessions)) {
        setNoteDocs(page.docs);
        setNoteSessions(page.sessions);
        setNoteListError("");
      }
      if (page.ok === false) {
        setNoteSaveBad(true);
        setNoteSave(page.error || "回答没有总结成文档。");
        return;
      }
      if (!page.path) {
        setNoteSaveBad(true);
        setNoteSave("总结没有返回文件路径。恢复：再总结一次。");
        return;
      }
      setNoteDocUrl("");
      setNoteSaveBad(false);
      setNoteSave(page.message || "已把勾选的回答总结成一份文档。");
    } catch (err) {
      setNoteSaveBad(true);
      setNoteSave(String(err));
    } finally {
      setNoteBusy(false);
    }
  }

  async function deleteNote(id: string) {
    const sessionId = noteSessionId;
    if (!sessionId) {
      setNoteSaveBad(true);
      setNoteSave("没有打开笔记会话。恢复：从侧边栏重新打开。");
      return;
    }
    if (debugPane()) {
      setNoteSaveBad(true);
      setNoteSave("调试页只看样本，不删笔记。");
      return;
    }
    if (!info) {
      setNoteSaveBad(true);
      setNoteSave("还没有连上本地后端。恢复：重启客户端。");
      return;
    }
    setNoteBusy(true);
    try {
      const page = await rpc<NotebookPage>(info, "delete_notebook_note", { session_id: sessionId, note_id: id });
      if (!takeNotebook(page)) {
        setNoteSaveBad(true);
        setNoteSave(page.error || "笔记没有删掉。");
        return;
      }
      setNoteSaveBad(false);
      setNoteSave("已删除笔记");
    } catch (err) {
      setNoteSaveBad(true);
      setNoteSave(String(err));
    } finally {
      setNoteBusy(false);
    }
  }

  async function exportNote(kind: "markdown" | "feishu", id: string) {
    const sessionId = noteSessionId;
    if (!sessionId) {
      setNoteSaveBad(true);
      setNoteSave("没有打开笔记会话。恢复：从侧边栏重新打开。");
      return;
    }
    if (debugPane()) {
      setNoteSaveBad(true);
      setNoteSave(kind === "markdown" ? "调试页只看样本，不生成 Markdown。" : "调试页只看样本，不生成飞书文档。");
      return;
    }
    if (!info) {
      setNoteSaveBad(true);
      setNoteSave("还没有连上本地后端。恢复：重启客户端。");
      return;
    }
    setNoteBusy(true);
    setNoteSaveBad(false);
    setNoteDocUrl("");
    setNoteSave(kind === "markdown" ? "正在生成 Markdown" : "正在生成飞书文档");
    try {
      const page = await rpc<NotebookPage>(
        info,
        kind === "markdown" ? "export_notebook_markdown" : "export_notebook_feishu",
        { session_id: sessionId, note_id: id },
      );
      if (kind === "markdown" && Array.isArray(page.docs) && Array.isArray(page.sessions)) {
        setNoteDocs(page.docs);
        setNoteSessions(page.sessions);
        setNoteListError("");
      }
      if (page.ok === false) {
        setNoteSaveBad(true);
        setNoteSave(page.error || "文档没有生成。");
        return;
      }
      if (kind === "feishu" && !page.url) {
        setNoteSaveBad(true);
        setNoteSave("飞书文档没有返回链接。恢复：再生成一次。");
        return;
      }
      if (kind === "markdown" && !page.path) {
        setNoteSaveBad(true);
        setNoteSave("Markdown 没有返回路径。恢复：再生成一次。");
        return;
      }
      setNoteDocUrl(page.url || "");
      setNoteSaveBad(false);
      setNoteSave(page.message || (kind === "markdown" ? "已生成 Markdown，并打开了文件。" : "已生成飞书文档，并打开了链接。"));
    } catch (err) {
      setNoteSaveBad(true);
      setNoteSave(String(err));
    } finally {
      setNoteBusy(false);
    }
  }

  async function manageNoteFile(action: "open" | "reveal" | "delete", id: string, name: string) {
    const sessionId = noteSessionId;
    if (!sessionId) {
      setNoteSaveBad(true);
      setNoteSave("没有打开笔记会话。恢复：从侧边栏重新打开。");
      return;
    }
    if (debugPane()) {
      const line = action === "open"
        ? "调试页只看样本，不打开文件。"
        : action === "reveal"
          ? "调试页只看样本，不打开文件夹。"
          : "调试页只看样本，不删除文件。";
      setNoteSaveBad(true);
      setNoteSave(line);
      return;
    }
    if (!info) {
      setNoteSaveBad(true);
      setNoteSave("还没有连上本地后端。恢复：重启客户端。");
      return;
    }
    const method = action === "open"
      ? "open_notebook_file"
      : action === "reveal"
        ? "reveal_notebook_file"
        : "delete_notebook_file";
    setNoteBusy(true);
    setNoteSaveBad(false);
    setNoteSave(action === "delete" ? "正在删除文件" : "正在打开");
    try {
      if (action === "open") {
        const file = noteSessions.find(s => s.id === sessionId)?.notes.find(n => n.id === id)?.files.find(f => f.name === name);
        if (!file) throw new Error("关联文件记录不存在，请刷新笔记后重试。");
        const staged = await rpc<{ ok: boolean; sources: LocalSource[]; errors: { error: string }[]; error?: string }>(info, "stage_local_sources", { kind: "file", paths: [file.path] }, { timeoutMs: 90000 });
        if (!staged.ok || !staged.sources?.length) throw new Error(staged.error || staged.errors?.[0]?.error || "文件无法预览。");
        previewSource(staged.sources[0]); setNoteSave("已在侧栏打开关联文件，尚未加入模型上下文。"); return;
      }
      const page = await rpc<NotebookPage>(info, method, { session_id: sessionId, note_id: id, name });
      if (action === "delete" && Array.isArray(page.docs) && Array.isArray(page.sessions)) {
        setNoteDocs(page.docs);
        setNoteSessions(page.sessions);
      }
      if (page.ok === false) {
        setNoteSaveBad(true);
        setNoteSave(page.error || "文件没有处理成。");
        return;
      }
      setNoteSaveBad(false);
      setNoteSave(page.message || "已处理这个文件。");
    } catch (err) {
      setNoteSaveBad(true);
      setNoteSave(String(err));
    } finally {
      setNoteBusy(false);
    }
  }

  function loadStatuses(backend: BackendInfo) {
    const mine = statusGen.current + 1;
    statusGen.current = mine;
    setStatuses(loadingStatuses());
    const methods: Record<StatusKind, string> = {
      feishu: "load_feishu",
      github: "load_github",
    };
    for (const kind of STATUS_KINDS) {
      rpc<unknown>(backend, methods[kind])
        .then((data) => {
          if (statusGen.current !== mine) return;
          setStatuses((cur) => cur.map((card) => (card.kind === kind ? statusFromPayload(kind, data) : card)));
        })
        .catch((err: unknown) => {
          if (statusGen.current !== mine) return;
          setStatuses((cur) => cur.map((card) => (card.kind === kind ? statusError(kind, String(err)) : card)));
        });
    }
  }

  function openBoard(refresh = false) {
    setPane("board");
    if (!shouldReloadBoard({ refresh, seen: boardSeen.current, debug: boardClock !== null })) return;
    boardSeen.current = true;
    if (!info) {
      setBoard({ ok: false, error: "还没有连上本地后端。恢复：重启客户端。" });
      setStatuses(STATUS_KINDS.map((kind) => statusError(kind, "还没有连上本地后端。恢复：重启客户端。")));
      return;
    }
    loadStatuses(info);
    void loadBoardSnapshot(refresh);
  }

  async function openMemory() {
    setNoteSessionId(null);
    setPane("chat");
    setChatFace("memory");
    setMemoryFormError("");
    if (debugPane()) {
      setMemory((cur) => cur || (debugPane() === "memory" ? MEMORY_FIXTURE : { ok: false, error: "调试页只看样本，不读取事实。", facts: [], items: [] }));
      return;
    }
    if (!info) {
      setMemory({ ok: false, error: "还没有连上本地后端。恢复：重启客户端。", facts: [], items: [] });
      return;
    }
    setMemoryLoading(true);
    try {
      setMemory(await rpc<MemoryPayload>(info, "load_memory"));
    } catch (err) {
      setMemory({ ok: false, error: String(err), facts: [], items: [] });
    } finally {
      setMemoryLoading(false);
    }
  }

  async function openKnowledge() {
    setNoteSessionId(null);
    setPane("chat");
    setChatFace("knowledge");
    if (debugPane()) {
      setKnowledge(KNOWLEDGE_FIXTURE);
      setKnowledgeCatalog(KNOWLEDGE_CATALOG);
      setKnowledgeCatalogError("");
      return;
    }
    if (!info) {
      setKnowledge({ ok: false, error: "还没有连上本地后端。恢复：重启客户端。" });
      return;
    }
    setKnowledgeBusy(true);
    try {
      const [page, docs] = await Promise.all([
        rpc<KnowledgePayload>(info, "load_knowledge").then(page => { setKnowledge(page); return page; }),
        rpc<{ ok?: boolean; error?: string; items?: FeishuDoc[] }>(info, "list_feishu_docs"),
      ]);
      setKnowledge(page);
      if (docs.ok === false) {
        setKnowledgeCatalog([]);
        setKnowledgeCatalogError(docs.error || "飞书文档没有列出来。");
      } else {
        setKnowledgeCatalog(docs.items || []);
        setKnowledgeCatalogError(docs.error || "");
      }
    } catch (err) {
      setKnowledge({ ok: false, error: String(err) });
    } finally {
      setKnowledgeBusy(false);
    }
  }

  async function knowledgeCall(method: string, args: Record<string, unknown> = {}): Promise<{ ok: boolean; error: string }> {
    if (debugPane()) {
      const error = "调试页只看样本，不下载、不入库、不检索。";
      setKnowledge((cur) => ({ ...(cur || KNOWLEDGE_FIXTURE), error }));
      return { ok: false, error };
    }
    if (!info) {
      const error = "还没有连上本地后端。恢复：重启客户端。";
      setKnowledge({ ok: false, error });
      return { ok: false, error };
    }
    setKnowledgeBusy(true);
    try {
      const page = await rpc<KnowledgePayload>(info, method, args);
      if (page.ok === false) {
        const error = page.error || "知识库操作失败。";
        setKnowledge((cur) => ({ ...(cur || {}), error }));
        return { ok: false, error };
      }
      setKnowledge((cur) => mergeKnowledgePage(cur, { ...page, error: "" }));
      if(['add_knowledge','delete_knowledge','rebuild_knowledge'].includes(method)){
        try{const notebook=await rpc<NotebookPage>(info,'load_notebook');
          if(notebook.ok===false||!Array.isArray(notebook.docs))throw Error(notebook.error||'笔记来源缺失');
          const valid=new Set(notebook.docs.map(doc=>doc.id)),old=new Set(noteDocs.map(doc=>doc.id));
          setNoteDocs(notebook.docs);setNoteChecked(cur=>[...new Set([...cur.filter(id=>valid.has(id)),...(method==='add_knowledge'?notebook.docs!.filter(doc=>!old.has(doc.id)).map(doc=>doc.id):[])])]);
        }catch(error){const message='知识库操作已完成，但笔记来源刷新失败，请重新读取核对：'+String(error);setNoteError(message);return {ok:false,error:message};}
      }
      return { ok: true, error: "" };
    } catch (err) {
      const error = String(err);
      setKnowledge((cur) => ({ ...(cur || {}), ok: false, error }));
      return { ok: false, error };
    } finally {
      setKnowledgeBusy(false);
    }
  }

  async function compressContext(): Promise<boolean> {
    if (debugPane()) {
      setMemoryFormError("调试页只看样本，不压缩。");
      return false;
    }
    if (!info) {
      setError("还没有连上本地后端。恢复：重启客户端。");
      return false;
    }
    setMemoryBusy(true);
    setError("");
    try {
      const data = await rpc<MemoryPayload>(info, "compress_context");
      if (data.ok === false) {
        setError(data.error || "压缩没有完成。");
        return false;
      }
      setMemory(data);
      await loadThread(info);
      return true;
    } catch (err) {
      setError(String(err));
      return false;
    } finally {
      setMemoryBusy(false);
    }
  }

  async function changeFact(
    method: "add_fact" | "update_fact" | "delete_fact" | "delete_memory_turn",
    args: Record<string, unknown>,
  ): Promise<boolean> {
    if (debugPane()) {
      setMemoryFormError("调试页只看样本，不改记忆。");
      return false;
    }
    if (!info) {
      setMemoryFormError("还没有连上本地后端。恢复：重启客户端。");
      return false;
    }
    setMemoryBusy(true);
    try {
      const data = await rpc<MemoryPayload>(info, method, args);
      if (data.ok === false && !Array.isArray(data.facts)) {
        setMemoryFormError(data.error || "事实没有改成。");
        return false;
      }
      setMemoryFormError("");
      setMemory(data);
      if (method === "delete_memory_turn" && info) {
        await loadThread(info);
      }
      return data.ok !== false;
    } catch (err) {
      setMemoryFormError(String(err));
      return false;
    } finally {
      setMemoryBusy(false);
    }
  }

  openFeishuRef.current = () => {
    void openFeishu();
  };
  openBoardRef.current = openBoard;

  function rememberFeishu(snap: FeishuSnap) {
    setFeishu(snap);
    setStatuses((cur) => cur.map((card) => (card.kind === "feishu" ? statusFromPayload("feishu", snap) : card)));
  }
  rememberFeishuRef.current = rememberFeishu;

  async function pullFeishu(backend: BackendInfo) {
    const snap = await rpc<FeishuSnap>(backend, "load_feishu");
    rememberFeishu(snap);
  }

  async function openFeishu() {
    setPane("feishu");
    if (boardClock !== null) {
      setFeishu((cur) => cur || FEISHU_LOGGED_OUT);
      return;
    }
    if (!info) {
      setFeishu({ ok: false, logged_in: false, error: "还没有连上本地后端。恢复：重启客户端。" });
      return;
    }
    try {
      await pullFeishu(info);
    } catch (err) {
      setFeishu({ ok: false, logged_in: false, error: String(err) });
    }
  }

  async function startFeishuLogin() {
    if (feishuBusyRef.current || feishuWaiting(feishu)) return;
    feishuBusyRef.current = true;
    setFeishuBusy(true);
    try {
      if (boardClock !== null) {
        rememberFeishu(FEISHU_WAITING);
        return;
      }
      if (!info) {
      setFeishu({ ok: false, logged_in: false, error: "还没有连上本地后端。恢复：重启客户端。" });
        return;
      }
      await rpc(info, "feishu_login");
      await pullFeishu(info);
    } catch (err) {
      setFeishu({ ...(feishu || {}), ok: false, logged_in: false, login_busy: false, error: String(err) });
    } finally {
      feishuBusyRef.current = false;
      setFeishuBusy(false);
    }
  }

  async function logoutFeishu() {
    if (feishuBusyRef.current || !feishuLoggedIn(feishu)) return;
    feishuBusyRef.current = true;
    setFeishuBusy(true);
    try {
      if (boardClock !== null) {
        rememberFeishu(FEISHU_LOGGED_OUT);
        return;
      }
      if (!info) {
        setFeishu({ ...(feishu || {}), ok: false, error: "还没有连上本地后端。恢复：重启客户端。" });
        return;
      }
      await rpc(info, "feishu_logout");
      await pullFeishu(info);
    } catch (err) {
      setFeishu({ ...(feishu || {}), ok: false, error: String(err) });
    } finally {
      feishuBusyRef.current = false;
      setFeishuBusy(false);
    }
  }

  useEffect(() => {
    if (pane !== "feishu" || boardClock !== null || !info || !feishuWaiting(feishu)) return;
    const timer = window.setInterval(() => {
      if (feishuBusyRef.current) return;
      void pullFeishu(info).catch((err: unknown) => {
        setFeishu({ ok: false, logged_in: false, error: String(err) });
      });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [pane, boardClock, info, feishu]);

  draftRef.current = draft;
  userLinesRef.current = (preview || thread?.items || [])
    .filter((item) => item.role === "user" && item.text.trim())
    .map((item) => item.text);
  applyBgRef.current = (url) => {
    const saved = storeBackground(url);
    if (saved.error) {
      setBgError(saved.error);
      return;
    }
    setBgUrl(saved.url);
    setBgError("");
  };
  clearBgRef.current = () => {
    clearStoredBackground();
    setBgUrl("");
    setBgError("");
  };

  const openCropRef = useRef<(url: string) => void>(() => {});
  openCropRef.current = (url) => {
    setCropError("");
    setCropSrc(url);
  };

  async function pickBackground(file: File) {
    const read = await readBackgroundFile(file);
    if (read.error || !read.url) {
      setBgError(read.error || "这张图打不开。恢复：换一张 png、jpg 或 webp。");
      return;
    }
    setBgError("");
    openCropRef.current(read.url);
  }

  function confirmCrop(url: string) {
    const saved = storeBackground(url);
    if (saved.error) {
      setCropError(saved.error);
      return;
    }
    setBgUrl(saved.url);
    setBgError("");
    setCropError("");
    setCropSrc("");
  }

  function settingsPayload(draft: { base_url: string; model: string; api_key: string }) {
    const base_url = draft.base_url.trim();
    const model = draft.model.trim();
    const api_key = draft.api_key.trim();
    return { base_url, model, api_key };
  }

  function editModel(id: string) {
    const item = modelItems.find((row) => row.id === id);
    if (!item) return;
    setEditingModelId(id);
    setModelForm(formFromEntry(item));
    setKeyEpoch((n) => n + 1);
    setSettingsError("");
    setSettingsNotice("");
  }

  async function useModel(id: string) {
    const previous = activeModelId;
    setActiveModelId(id);
    if (debugKind) {
      setSettingsError("");
      setSettingsNotice("调试样本，未切换磁盘上的模型。");
      return;
    }
    if (!info) {
      setActiveModelId(previous);
      setSettingsError("还没有连上本地后端。恢复：重启客户端。");
      return;
    }
    try {
      const data = await rpc<ModelList>(info, "use_model", { model_id: id });
      if (!data.ok) throw new Error(data.error || "切换模型失败。");
      setModelItems(data.items);
      setActiveModelId(data.active);
      setSettingsError("");
      setSettingsNotice(data.message || "已切换模型。");
      setError("");
    } catch (err) {
      setActiveModelId(previous);
      setSettingsError(String(err));
      setError(String(err));
    }
  }

  async function deleteModel(id: string) {
    if (modelItems.length <= 1) {
      setSettingsNotice("");
      setSettingsError("至少留一个模型。要换的话直接改这一条。");
      return;
    }
    if (debugKind) {
      const items = modelItems.filter((row) => row.id !== id);
      const active = activeModelId === id ? items[0].id : activeModelId;
      const edit = editingModelId === id ? active : editingModelId;
      const next = items.find((row) => row.id === edit) || items[0];
      setModelItems(items);
      setActiveModelId(active);
      setEditingModelId(next.id);
      setModelForm(formFromEntry(next));
      setKeyEpoch((n) => n + 1);
      setSettingsError("");
      setSettingsNotice("调试样本，未删除磁盘上的模型。");
      return;
    }
    if (!info) {
      setSettingsError("还没有连上本地后端。恢复：重启客户端。");
      return;
    }
    setSettingsBusy(true);
    setSettingsError("");
    setSettingsNotice("");
    try {
      const data = await rpc<ModelList>(info, "delete_model_entry", { model_id: id });
      if (!data.ok) throw new Error(data.error || "删除模型失败。");
      rememberModels(data, data.active);
      setSettingsNotice(data.message || "模型已删除。");
    } catch (err) {
      setSettingsError(String(err));
    } finally {
      setSettingsBusy(false);
    }
  }

  async function saveSettings(draft: { id: string; base_url: string; model: string; api_key: string }) {
    const { base_url, model, api_key } = settingsPayload(draft);
    const sourceId = draft.id || editingModelId;
    const editing = modelItems.find((row) => row.id === sourceId);
    if (!base_url) {
      setSettingsNotice("");
      setSettingsError("API 地址不能为空。");
      return;
    }
    if (!model) {
      setSettingsNotice("");
      setSettingsError("模型名不能为空。");
      return;
    }
    if (!api_key && !editing?.has_key) {
      setSettingsNotice("");
      setSettingsError("API Key 不能为空。要沿用已有 Key，先在列表里点一条。");
      return;
    }
    if (debugKind) {
      const id = draft.id || `local-${Date.now()}`;
      const nextItem = { id, base_url, model, has_key: true };
      const items = draft.id
        ? modelItems.map((row) => (row.id === draft.id ? nextItem : row))
        : [...modelItems, nextItem];
      const active = activeModelId || id;
      setModelItems(items);
      setActiveModelId(active);
      setEditingModelId(id);
      setModelForm({ ok: true, base_url, model, has_key: true });
      setSettingsError("");
      setSettingsNotice("调试样本，未写入模型。");
      setKeyEpoch((n) => n + 1);
      return;
    }
    if (!info) {
      setSettingsError("还没有连上本地后端。恢复：重启客户端。");
      return;
    }
    setSettingsBusy(true);
    setSettingsError("");
    setSettingsNotice("");
    try {
      const data = await rpc<ModelList>(info, "save_model_entry", {
        payload: { id: draft.id, base_url, model, api_key, copy_key_from: draft.id ? "" : sourceId },
      });
      if (!data.ok) throw new Error(data.error || "模型保存失败。");
      const created = data.items.find((row) => !modelItems.some((old) => old.id === row.id));
      rememberModels(data, draft.id || created?.id || data.active);
      setSettingsNotice(data.message || "模型已保存。");
    } catch (err) {
      setSettingsError(String(err));
    } finally {
      setSettingsBusy(false);
    }
  }

  async function savePersona(text: string) {
    const persona = text.trim();
    if (!persona) {
      setPersonaNotice("");
      setPersonaError("系统提示词不能为空。");
      return;
    }
    if (!personaForm) {
      setPersonaError("系统提示词还没读到。恢复：重新打开设置。");
      return;
    }
    if (debugKind) {
      setPersonaForm({ ...personaForm, persona });
      setPersonaError("");
      setPersonaNotice("调试样本，未写入人设。");
      return;
    }
    if (!info) {
      setPersonaError("还没有连上本地后端。恢复：重启客户端。");
      return;
    }
    setSettingsBusy(true);
    setPersonaError("");
    setPersonaNotice("");
    try {
      const data = await rpc<PersonaPublic>(info, "save_persona", {
        payload: {
          persona,
          max_steps: personaForm.max_steps,
          nudge_enabled: personaForm.nudge_enabled,
        },
      });
      if (!data.ok) throw new Error(data.error || "系统提示词保存失败。");
      setPersonaForm({ ...personaForm, persona });
      setPersonaNotice(data.message || "人设已保存。");
    } catch (err) {
      setPersonaError(String(err));
    } finally {
      setSettingsBusy(false);
    }
  }

  async function testSettings(draft: { base_url: string; model: string; api_key: string }) {
    const { base_url, model, api_key } = settingsPayload(draft);
    const editing = modelItems.find((row) => row.id === editingModelId);
    if (!base_url || !model || (!api_key && !editing?.has_key)) {
      setSettingsNotice("");
      setSettingsError("测试前先填 API 地址、模型名和 Key。");
      return;
    }
    if (debugKind) {
      setSettingsError("");
      setSettingsNotice("调试样本，未测试连通。");
      return;
    }
    if (!info) {
      setSettingsError("还没有连上本地后端。恢复：重启客户端。");
      return;
    }
    setSettingsBusy(true);
    setSettingsError("");
    setSettingsNotice("");
    try {
      const data = await rpc<ModelPublic>(info, "test_model", {
        payload: { id: editingModelId, base_url, model, api_key },
      });
      if (!data.ok) throw new Error(data.error || "连通测试失败。");
      setSettingsNotice(data.message || "连通正常。");
    } catch (err) {
      setSettingsError(String(err));
    } finally {
      setSettingsBusy(false);
    }
  }

  fillDraftRef.current = (text) => {
    historyAt.current = null;
    draftStash.current = null;
    setDraft(text);
  };

  function recallHistory(key: "ArrowUp" | "ArrowDown"): boolean {
    if (busy) return false;
    const lines = userLinesRef.current;
    if (key === "ArrowUp") {
      if (lines.length === 0) return false;
      if (historyAt.current === null) {
        draftStash.current = draftRef.current;
        historyAt.current = lines.length;
      }
      if (historyAt.current <= 0) return true;
      historyAt.current -= 1;
      setDraft(lines[historyAt.current]);
      return true;
    }
    if (historyAt.current === null) return false;
    if (historyAt.current >= lines.length - 1) {
      historyAt.current = null;
      setDraft(draftStash.current ?? "");
      draftStash.current = null;
      return true;
    }
    historyAt.current += 1;
    setDraft(lines[historyAt.current]);
    return true;
  }

  function slashItems(): SlashItem[] {
    if (repoPicking) return composer?.github.ok ? composer.github.items : [];
    const groups: { parent: SlashParent; items: SlashItem[] }[] = [
      { parent: "skill", items: composer?.skills || [] }, { parent: "tool", items: composer?.cli || [] },
      { parent: "doc", items: docCatalog?.ok ? docCatalog.items : [] }, { parent: "mcp", items: mcpCatalog?.ok ? mcpCatalog.items : [] },
      { parent: "attachment", items: [{ id: "local-file", label: "选择文件", description: "选择本地文件，预览后加入资料" }, { id: "local-folder", label: "选择文件夹", description: "查看目录，按需选择文件" }] },
    ];
    return filterSlash(groups.filter(g => slashParent === "all" || slashParent === g.parent).flatMap(g => g.items.map(item => ({ ...item, parent: g.parent,
      source: SLASH_PARENTS.find(p => p.id === g.parent)?.label,
      picked: (g.parent === "skill" ? pickedSkills : g.parent === "tool" ? pickedTools : g.parent === "doc" ? pickedDocs : g.parent === "mcp" ? pickedMcp : []).some(p => p.id === item.id),
    }))), slashNeedle || "");
  }

  function pickSlash(id: string, selectedParent: SlashParent = slashParent) {
    if (selectedParent === "attachment") { setLocalDialog(id === "local-folder" ? "folder" : "file"); setDraft(stripSlash(draft)); setSlashDismissed(stripSlash(draft)); setComposerNotice("已打开资料选择，可预览后再加入对话。"); return; }
    if (!composer) return;
    if (repoPicking) {
      const repo = composer.github.items.find((item) => item.id === id);
      if (!repo || !pendingRepoTool) return;
      setPickedTools((prev) => (prev.some((item) => item.id === pendingRepoTool.id) ? prev : [...prev, pendingRepoTool]));
      setPickedRepo(repo.id);
      setPendingRepoTool(null);
      setRepoPicking(false);
      setDraft(stripSlash(draft));
      setComposerNotice(`已加入工具与仓库：${repo.label}`);
      return;
    }
    if (selectedParent === "skill") {
      const skill = composer.skills.find((item) => item.id === id);
      if (!skill) return;
      setPickedSkills((prev) => (prev.some((item) => item.id === id) ? prev : [...prev, skill]));
    } else if (selectedParent === "doc") {
      const doc = docCatalog?.items.find((item) => item.id === id);
      if (!doc) return;
      setPickedDocs((prev) => (prev.some((item) => item.id === id) ? prev : [...prev, doc]));
    } else if (selectedParent === "mcp") {
      const tool = mcpCatalog?.items.find((item) => item.id === id);
      if (!tool) return;
      setPickedMcp((prev) => (prev.some((item) => item.id === id) ? prev : [...prev, tool]));
    } else {
      const tool = composer.cli.find((item) => item.id === id);
      if (!tool) return;
      if (NEED_REPO.has(tool.id)) {
        setPendingRepoTool(tool);
        setRepoPicking(true);
        setDraft(stripSlash(draft));
        setComposerNotice("先选择仓库，完成后工具才会加入本轮。");
        return;
      }
      setPickedTools((prev) => (prev.some((item) => item.id === id) ? prev : [...prev, tool]));
    }
    setDraft(stripSlash(draft));
    setComposerNotice("已加入本轮；可以继续选择其他项目。");
  }

  function removeTool(id: string) {
    const next = pickedTools.filter((item) => item.id !== id);
    setPickedTools(next);
    if (!next.some((item) => NEED_REPO.has(item.id))) setPickedRepo("");
  }

  const menuItems = slashItems();
  const menuActive = menuItems.length ? Math.min(slashIndex, menuItems.length - 1) : 0;
  const sideCatalog = slashParent === "doc" ? docCatalog : slashParent === "mcp" ? mcpCatalog : null;
  const menuError =
    (slashParent === "attachment" ? "" : composerError) ||
    (repoPicking && composer && !composer.github.ok ? composer.github.error || "仓库列表读取失败。" : "") ||
    (!repoPicking && sideCatalog && !sideCatalog.ok ? sideCatalog.error || "读取失败。" : "") ||
    (!repoPicking && sideCatalog?.ok && sideCatalog.error ? sideCatalog.error : "") ||
    (slashParent === "all" ? [docCatalog?.error, mcpCatalog?.error].filter(Boolean).join("；") : "");
  const menuLoading =
    (slashParent !== "attachment" && !composer && !composerError) ||
    (!repoPicking && (slashParent === "doc" || slashParent === "mcp") && !sideCatalog) ||
    (!repoPicking && slashParent === "all" && (!docCatalog || !mcpCatalog));

  menuKeyRef.current = (ev: KeyboardEvent) => {
    if (ev.isComposing) return;
    if (ev.defaultPrevented) return;
    if (!menuOpen) return;
    const target = ev.target as HTMLElement | null;
    if (!target) return;
    const draftForm = document.querySelector("[data-chat-draft]");
    const inDraft = !!draftForm && draftForm.contains(target);
    const inMenu = !!target.closest("[data-slash-menu]");
    if (!inDraft && !inMenu) return;
    if (ev.key === "Escape") {
      ev.preventDefault();
      if (repoPicking) { setRepoPicking(false); setPendingRepoTool(null); setDraft("/"); setSlashParent("tool"); return; }
      setRepoPicking(false);
      setPendingRepoTool(null);
      setSlashDismissed(draft);
      return;
    }
    if (ev.altKey && (ev.key === "ArrowLeft" || ev.key === "ArrowRight")) {
      ev.preventDefault();
      setRepoPicking(false);
      setPendingRepoTool(null);
      const ids = SLASH_PARENTS.map((item) => item.id);
      const at = Math.max(0, ids.indexOf(slashParent));
      const step = ev.key === "ArrowLeft" ? -1 : 1;
      setSlashParent(ids[(at + step + ids.length) % ids.length]);
      return;
    }
    if (ev.key === "ArrowUp" || ev.key === "ArrowDown") {
      ev.preventDefault();
      if (!menuItems.length) return;
      const last = menuItems.length - 1;
      const cur = Math.min(slashIndexRef.current, last);
      const next = ev.key === "ArrowUp" ? (cur <= 0 ? 0 : cur - 1) : cur >= last ? cur : cur + 1;
      slashIndexRef.current = next;
      setSlashIndex(next);
      return;
    }
    if (ev.key === "Enter") {
      if (ev.shiftKey) return;
      ev.preventDefault();
      if (!menuItems.length) return;
      const item = menuItems[Math.min(slashIndexRef.current, menuItems.length - 1)]; pickSlash(item.id, item.parent);
    }
  };

  useEffect(() => { if (!debugKind && pane === 'chat' && chatFace === 'thread' && !noteSessionId) setPane('collaboration'); }, [pane, chatFace, noteSessionId]);
  const coreChat = pane === "collaboration" || (!debugKind && pane === "chat" && chatFace === "thread" && !noteSessionId);
  const changeDeveloperMode = (value: boolean) => {
    setDeveloperMode(value); setComposerNotice(value ? "开发者模式已开启" : "开发者模式已关闭"); const notice = saveDeveloperMode(value); if (notice) setError(notice);
    if (!value) { setAgentDebugOpen(false); if (chatFace === "memory") { setChatFace("thread"); setPane("collaboration"); } }
  };
  async function openNotes(){
    setPane('chat');setChatFace('notebooks');
    if(noteSessionId){setChatFace('thread');return;}
    if(debugKind){setNoteSessions(NOTE_FIXTURE.sessions);setNoteDocs(NOTE_FIXTURE.docs);setNoteChecked(NOTE_FIXTURE.docs.map(doc=>doc.id));setNoteSessionId(NOTE_FIXTURE.sessions[0].id);setChatFace('thread');return;}
    if(noteSessions.length){openNoteSession(noteSessions[0].id);return;}
    await newNotebook();
  }
  async function refreshNotebooks(){if(!noteBusy&&info&&await loadNotebooks(info))setNotebookNeedsCheck(false);}
  function openCalendar(target:ScheduleTarget={}){setCalendarTarget({...target});void openBoard();}
  function openWorkItem(id:string){setWorkTarget({id,nonce:Date.now()});setPane('work');}
  const workspaceNavigation = <>
    <span className="tebb-nav-group">协作</span>
    <RailButton icon="chat" label="伙伴聊天" active={pane === "collaboration"} onClick={() => navigateWorkspace(()=>setPane("collaboration"))} />
    <RailButton icon="tasks" label="AI 执行" active={pane === "monitor"} onClick={() => navigateWorkspace(()=>setPane("monitor"))} />
    <RailButton icon="automation" label="自动化" active={pane === "automation"} onClick={() => navigateWorkspace(()=>setPane("automation"))} />
    <span className="tebb-nav-group">工作</span>
    <RailButton icon="board" label="事项" active={pane === "work"} onClick={() => navigateWorkspace(()=>setPane("work"))} />
    <RailButton icon="calendar" label="日程" active={pane === "board"} onClick={() => navigateWorkspace(()=>void openBoard())} />
    <span className="tebb-nav-group">内容</span>
    <RailButton icon="notes" label="笔记" active={pane === "chat" && (chatFace === "notebooks" || chatFace === "thread" && !!noteSessionId)} onClick={() => navigateWorkspace(()=>void openNotes())} />
    <RailButton icon="knowledge" label="知识库" active={pane === "chat" && chatFace === "knowledge"} onClick={() => navigateWorkspace(()=>void openKnowledge())} />
    <RailButton icon="video" label="视频学习" active={pane === "video"} onClick={() => navigateWorkspace(()=>setPane("video"))} />
    <RailButton icon="feishu" label="飞书" active={pane === "feishu"} onClick={() => navigateWorkspace(()=>void openFeishu())} />
    <span className="tebb-nav-group">偏好</span>
    {developerMode && <RailButton icon="memory" label="旧助手记忆调试" active={pane === "chat" && chatFace === "memory"} onClick={() => navigateWorkspace(()=>void openMemory())} />}
    <RailButton icon="settings" label="设置" active={pane === "settings"} onClick={() => navigateWorkspace(()=>setPane("settings"))} />
    <div className="tebb-nav-actions"><RailButton icon="pet" label="唤出桌宠 ↗" onClick={() => { void invoke("set_pet_visible", { visible: true }).catch(err => setError(`唤出桌宠失败：${String(err)}`)); }} /></div>
  </>;

  return (
    <div className="tebb-workspace relative flex h-full overflow-hidden bg-background text-foreground" data-pane={pane}>
      {showGuide && info && setupStatus ? <SetupGuide
        info={info} status={setupStatus} models={{ ok: true, active: activeModelId, items: modelItems }}
        onModels={(models) => rememberModels(models)} onClose={(completed) => {
          setShowGuide(false);
          if (completed) setSetupStatus(current => current ? { ...current, show: false } : current);
        }}
        onNavigate={(nextPane, sub) => {
          if (sub === "knowledge") void openKnowledge();
          else if (nextPane === "feishu") void openFeishu();
          else if (nextPane === "board") void openBoard();
          else setPane(nextPane);
        }}
      /> : null}
      {agentDebugOpen ? <AgentDebugDialog info={info} debug={!!debugKind} onClose={() => setAgentDebugOpen(false)} /> : null}
      {releaseIntro.open && !showGuide && !cropSrc && !agentDebugOpen ? <ReleaseIntro daily={releaseIntro.daily}
        onDaily={releaseIntro.setDaily} onClose={releaseIntro.close} error={releaseIntro.preferenceError} /> : null}
      <div
        className="desk-bg"
        data-bg-motion
        data-bg-photo={bgUrl ? "1" : "0"}
        style={bgUrl ? ({ "--desk-bg-image": `url("${bgUrl}")` } as CSSProperties) : undefined}
        aria-hidden="true"
      >
        {bgUrl ? <div className="desk-bg-scrim" data-bg-scrim /> : null}
      </div>
      <WorkspaceRail developer={developerMode} onToggle={() => changeDeveloperMode(!developerMode)}>{workspaceNavigation}</WorkspaceRail>
      <main className="main-workspace relative z-10 flex min-w-0 flex-1 flex-col">
        <TopToolbar title={pane === "chat" ? noteSessionId ? "笔记" : ({ thread: "对话", memory: "记忆", knowledge: "知识库", notebooks:"笔记" })[chatFace] :
          ({ work: "事项", board: "日程", feishu: "飞书", settings: "设置", automation: "自动化", monitor: "AI 执行", video: "视频学习", collaboration: "伙伴聊天" })[pane]}
          model={modelItems.find(m => m.id === activeModelId)?.model ? `默认：${modelItems.find(m => m.id === activeModelId)?.model}` : ""} connected={!!info || !!debugKind} busy={busy || noteBusy}
          onNewChat={coreChat ? () => { setPane("collaboration"); window.dispatchEvent(new CustomEvent("tebb-new-chat")); } : undefined}
          onFile={() => setLocalDialog("file")} onFolder={openProjectFolder} onTerminal={openTerminal} sidebarOpen={sidebarState.open} sidebarAvailable={sidebarState.available} onSidebarToggle={togglePreview} />
        {noteSourcesOpen&&<NotebookSources info={info} docs={noteDocs} checked={noteChecked} busy={noteBusy||knowledgeBusy} debug={!!debugKind} onCheck={(id,on)=>setNoteChecked(cur=>on?[...new Set([...cur,id])]:cur.filter(value=>value!==id))} onAdd={(doc_id,label,source)=>knowledgeCall('add_knowledge',{doc_id,label,source})} onLocal={()=>{setNoteSourcesOpen(false);setLocalDialog('file');}} onClose={()=>setNoteSourcesOpen(false)}/>}
        {localDialog && <LocalSourcesDialog info={info} initialKind={localDialog} debug={!!debugPane()} onClose={() => { setLocalDialog(null); setLocalDialogMinimized(false); }} onPreview={previewSource} onMinimizedChange={setLocalDialogMinimized}
          onAttach={sources => { setPickedAttachments(sources); setLocalDialog(null); setPane("collaboration"); setNoteSessionId(null); }}
          onIndex={source => knowledgeCall("add_knowledge", { doc_id: `local:${source.id}`, label: source.name })} />}
        <div className="tebb-page-body">
        <SideWorkspace onVisibilityChange={setSidebarState} info={info} session={noteSessionId || thread?.sessionId || ""} onTerminalDraft={text => { if (noteSessionId && pane === "chat" && chatFace === "thread") { setDraft(previous => `${previous}${previous ? "\n\n" : ""}${text}`); setComposerNotice("内容已加入笔记提问草稿，尚未发送。"); } else { setComposeRequest({ id: Date.now(), text }); setPane("collaboration"); } }} enabled={true} debug={!!debugKind}
          suspended={workspaceModalOpen || agentDebugOpen || showGuide || !!cropSrc || releaseIntro.open || (!!localDialog && !localDialogMinimized)}
          attachments={pickedAttachments}
          onAttach={sources => { setPickedAttachments(sources); setPane("collaboration"); setNoteSessionId(null); }}
          onDetach={id => setPickedAttachments(prev => prev.filter(s => s.id !== id))}
          onLink={url => { if (noteSessionId && pane === "chat" && chatFace === "thread") setDraft(text => `${text}${text ? "\n" : ""}${url}`); else { setComposeRequest({ id: Date.now(), text: url }); setPane("collaboration"); } }}
          onRequest={() => {}}>
        <div className="tebb-content-slot" data-content-slot>
        <div className="tebb-core-chat" hidden={!coreChat} style={{ display: coreChat ? "flex" : "none", flex: 1, minHeight: 0, minWidth: 0 }}>
          {!debugKind && <Suspense fallback={<p className="p-6">正在连接伙伴…</p>}><LiveCollaboration onCalendar={()=>openCalendar()} attachments={pickedAttachments} onAttach={() => setLocalDialog("file")} onDetach={id => setPickedAttachments(old => old.filter(a => a.id!==id))} onProject={openProject} onFile={() => setLocalDialog("file")} onTerminal={openTerminal} composeRequest={composeRequest} info={info} developerMode={developerMode} onDeveloperMode={changeDeveloperMode} /></Suspense>}
        </div>
        <div className="tebb-route-page" hidden={coreChat} style={{ display: coreChat ? "none" : "flex" }}>
        <PageStack active={coreChat ? "" : pane === "chat" ? chatFace : pane}>
          {coreChat ? null : pane === "chat" && chatFace === "knowledge" ? (
            <section
              key="knowledge"
              data-chat-face="knowledge"
              className="flex-1 overflow-y-auto px-8 py-6"
            >
              <KnowledgePane
                settings={<WikiConnectionSettings info={info} debug={!!debugPane()} onSaved={() => void openKnowledge()} />}
                data={knowledge}
                catalog={knowledgeCatalog}
                catalogError={knowledgeCatalogError}
                busy={knowledgeBusy}
                onDownload={(repo) => knowledgeCall("download_knowledge", { repo }).then((result) => result.ok)}
                onDeleteModel={(repo) => knowledgeCall("delete_model", { repo }).then((result) => result.ok)}
                onSave={(payload) => knowledgeCall("save_knowledge", { payload }).then((result) => result.ok)}
                onAdd={(doc_id, label, source) => knowledgeCall("add_knowledge", { doc_id, label, source })}
                onLocal={kind => setLocalDialog(kind)}
                onRefresh={() => void openKnowledge()}
                onDelete={(doc_id) => knowledgeCall("delete_knowledge", { doc_id })}
                onRebuild={() => knowledgeCall("rebuild_knowledge").then((result) => result.ok)}
                onAsk={async (text) => {
                  if (debugPane()) {
                    setKnowledge((cur) => ({ ...(cur || KNOWLEDGE_FIXTURE), error: "调试页只看样本，不检索。" }));
                    return false;
                  }
                  if (!info) return false;
                  setKnowledgeBusy(true);
                  try {
                    const page = await rpc<{ ok?: boolean; error?: string; trace?: KnowledgePayload["trace"] }>(info, "ask_knowledge", { text });
                    if (page.ok === false) {
                      setKnowledge((cur) => ({ ...(cur || {}), ok: true, error: page.error }));
                      return false;
                    }
                    setKnowledge((cur) => ({ ...(cur || {}), ok: true, error: "", trace: page.trace }));
                    return true;
                  } catch (err) {
                    setKnowledge((cur) => ({ ...(cur || {}), error: String(err) }));
                    return false;
                  } finally {
                    setKnowledgeBusy(false);
                  }
                }}
              />
            </section>
          ) : pane === "chat" && chatFace === "notebooks" ? (
            <section key="notebooks" data-notebook-empty className="flex-1 overflow-y-auto px-8 py-6"><h1 className="text-xl">笔记</h1><p className="my-4 text-sm text-muted-foreground">选择或新建笔记会话，基于来源提问并保存成果。</p>{noteListError&&<p role="alert" className="text-destructive">{noteListError}</p>}<button type="button" className="desk-menu-item" disabled={notebookCreating||notebookNeedsCheck||busy||noteBusy} onClick={()=>void newNotebook()}>{notebookCreating?'正在创建…':'＋ 新建笔记'}</button><button type="button" className="desk-menu-item" onClick={()=>void refreshNotebooks()}>读取笔记核对</button>{noteSessions.map(session=><button type="button" className="desk-menu-item" key={session.id} onClick={()=>openNoteSession(session.id)}>{session.title||'未命名笔记'}</button>)}</section>
          ) : pane === "chat" && chatFace === "memory" ? (
            <section
              key="memory"
              data-chat-face="memory"
              className="flex-1 overflow-y-auto px-8 py-6"
            >
              <MemoryPane
                data={memory}
                loading={memoryLoading}
                formError={memoryFormError}
                busy={memoryBusy}
                onAdd={(text) => changeFact("add_fact", { text })}
                onUpdate={(id, text) => changeFact("update_fact", { fact_id: id, text })}
                onDelete={(id) => changeFact("delete_fact", { fact_id: id })}
                onDrop={(role, text) => changeFact("delete_memory_turn", { role, text })}
                onCompress={compressContext}
              />
            </section>
          ) : pane === "chat" && noteSessionId ? (
            <section
              key={`note-${noteSessionId}`}
              data-chat-face="note"
              className="flex min-h-0 flex-1 flex-col"
            >
              <div className="notebook-toolbar"><label>笔记会话 <select aria-label="笔记会话" value={noteSessionId} disabled={noteBusy} onChange={e=>openNoteSession(e.target.value)}>{noteSessions.map(session=><option key={session.id} value={session.id}>{session.title||'未命名笔记'}</option>)}</select></label><button type="button" disabled={notebookCreating||notebookNeedsCheck||busy||noteBusy} onClick={()=>void newNotebook()}>＋ 新建笔记</button><button type="button" onClick={()=>void refreshNotebooks()}>读取核对</button></div>
              {noteListError&&<p role="alert" className="text-destructive px-4">{noteListError}</p>}
              <NoteMode
                sessionId={noteSessionId}
                book={{
                  docs: noteDocs,
                  turns: noteSessions.find((item) => item.id === noteSessionId)?.turns || [],
                  notes: noteSessions.find((item) => item.id === noteSessionId)?.notes || [],
                }}
                error={noteError}
                busy={noteBusy}
                status={noteStatus}
                checked={noteChecked}
                threadRef={scroller}
                onCheck={(id, on) => {
                  setNoteChecked((cur) => on ? [...cur, id] : cur.filter((item) => item !== id));
                }}
                onSave={(index) => void saveNote(index)}
                onDelete={(id) => void deleteNote(id)}
                onMarkdown={(id) => void exportNote("markdown", id)}
                onFeishu={(id) => void exportNote("feishu", id)}
                onOpenFile={(id, name) => void manageNoteFile("open", id, name)}
                onRevealFile={(id, name) => void manageNoteFile("reveal", id, name)}
                onDeleteFile={(id, name) => void manageNoteFile("delete", id, name)}
                onSummarize={(picks) => void summarizeNotes(picks)}
                onCollaborate={id => void shareNoteWithCompanions(id)}
                saveStatus={noteSave}
                saveBad={noteSaveBad}
                docUrl={noteDocUrl}
                onAddSources={()=>setNoteSourcesOpen(true)}
              >
              <div data-chat-bar="" className="border-t border-border px-6 py-4">
                <div className="composer-toolbar mb-2">
                  <ModelPick modelId={activeModelId} items={modelItems} busy={busy || noteBusy} onChange={(id) => void useModel(id)} />
                  {developerMode && <SamplingBar value={sampling} onChange={setSampling} />}
                </div>
                <form
                  className="composer-form"
                  onKeyDown={event => { if ((event.target as HTMLElement).tagName === "TEXTAREA" && event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); } }}
                  onSubmit={(ev) => {
                    ev.preventDefault();
                    send();
                  }}
                >
                  <textarea
                    value={draft}
                    onChange={(ev) => setDraft(ev.target.value)}
                    placeholder="根据勾选的来源提问"
                    className="composer-textarea"
                    rows={3}
                    aria-label="笔记提问"
                  />
                  <div className="composer-actions"><span className="text-xs text-muted-foreground">来源 · 已选 {noteChecked.length} 份</span><Button type="submit" variant="primary" disabled={busy || noteBusy}>{noteBusy ? <LoadingText text="处理中…" /> : "发送 ↑"}</Button></div>
                </form>
                {composerNotice && <p className="composer-feedback" role="status">{composerNotice}</p>}
              </div>
              </NoteMode>
            </section>
          ) : pane === "chat" ? (
            <section
              key="chat"
              data-chat-face="thread"
              className="flex min-h-0 flex-1 flex-col"
            >
              {debugKind&&<div className="notebook-toolbar"><button type="button" onClick={()=>void newSession()}>新建普通会话</button><select aria-label="普通调试会话" value={thread?.sessionId||''} onChange={e=>void openSession(e.target.value)}><option value="">选择普通会话</option>{thread?.sessions.map(session=><option key={session.id} value={session.id}>{session.title||'未命名对话'}</option>)}</select></div>}
              <div ref={scroller} className="flex-1 space-y-3 overflow-y-auto px-8 py-6">
                {debugMode ? (
                  <div className="text-xs text-muted-foreground">调试样本，未连接后端</div>
                ) : null}
                {shownItems.map((item, idx, all) => (
                  <Fragment key={`${item.ts || idx}-${idx}`}>
                    {idx === compressSplit ? <ContextSplit covered={shownCovered} /> : null}
                    <Bubble
                      {...item}
                      live={
                        idx === all.length - 1 &&
                        item.role === "pet" &&
                        (thinkPulse || (busy && !preview))
                      }
                    />
                  </Fragment>
                ))}
                {compressSplit === shownItems.length ? <ContextSplit covered={shownCovered} /> : null}
                {status ? <div className="text-sm text-muted-foreground" role="status"><LoadingText text={status} active={busy} /></div> : null}
                {error ? <div data-chat-error className="text-sm text-destructive">{error}</div> : null}
              </div>
              <div data-chat-bar="" className="border-t border-border px-6 py-4">
                {debugMode || thread ? (
                  <ContextCard
                    variant="bar"
                    context={shownContext}
                    verbatim={shownVerbatim}
                    busy={busy || memoryBusy}
                    onCompress={compressContext}
                    leading={
                      <>
                        <ModelPick modelId={activeModelId} items={modelItems} busy={busy} onChange={(id) => void useModel(id)} />
                        {developerMode && <SamplingBar value={sampling} onChange={setSampling} />}
                        <button
                          type="button"
                          data-knowledge-toggle=""
                          aria-pressed={useKnowledge}
                          className="desk-menu-item shrink-0"
                          onClick={() => setUseKnowledge((value) => !value)}
                        >
                          知识库 · {useKnowledge ? "开" : "关"}
                        </button>
                      </>
                    }
                  />
                ) : (
                  <div className="composer-toolbar"><ModelPick modelId={activeModelId} items={modelItems} busy={busy} onChange={(id) => void useModel(id)} />{developerMode && <SamplingBar value={sampling} onChange={setSampling} />}</div>
                )}
                {pickedAttachments.length > 0 && <div className="mb-2 flex flex-wrap gap-2">{pickedAttachments.map(source => <span key={source.id} className="desk-chip" data-chip-kind="attachment">
                  <button type="button" className="desk-menu-item" aria-label={`预览 ${source.name}`} onClick={() => previewSource(source)}>{source.name}{source.kind === "folder" ? ` · ${source.files?.length || 0} 个文件` : ` · ${source.chars} 字`}</button>
                  <button type="button" className="ml-2" aria-label={`移除附件 ${source.name}`} onClick={() => setPickedAttachments(prev => prev.filter(s => s.id !== source.id))}>×</button>
                </span>)}</div>}
                {pickedSkills.length || pickedTools.length || pickedDocs.length || pickedMcp.length || pickedRepo ? (
                  <div className="mb-2 flex flex-wrap gap-2">
                    {pickedSkills.map((item) => (
                      <span
                        key={item.id}
                        data-chip=""
                        data-chip-kind="skill"
                        data-chip-id={item.id}
                        className="desk-chip"
                      >
                        <span className="px-2 py-1">{item.label}</span><button type="button" className="desk-menu-item" aria-label={`移除技能 ${item.label}`} onClick={() => { setPickedSkills(prev => prev.filter(row => row.id !== item.id)); setComposerNotice(`已移除技能 ${item.label}`); }}>×</button>
                      </span>
                    ))}
                    {pickedDocs.map((item) => (
                      <span
                        key={item.id}
                        data-chip=""
                        data-chip-kind="doc"
                        data-chip-id={item.id}
                        className="desk-chip"
                      >
                        <DocChipName id={item.id} label={item.label} />
                        <button
                          type="button"
                          className="desk-btn desk-btn-danger ml-1"
                          aria-label={`移除文档 ${item.label}`}
                          onClick={() => setPickedDocs((prev) => prev.filter((row) => row.id !== item.id))}
                        >
                          ×
                        </button>
                      </span>
                    ))}
                    {pickedMcp.map((item) => (
                      <span
                        key={item.id}
                        data-chip=""
                        data-chip-kind="mcp"
                        data-chip-id={item.id}
                        className="desk-chip"
                      >
                        <span className="px-2 py-1">{item.label}</span><button type="button" className="desk-menu-item" aria-label={`移除 MCP ${item.label}`} onClick={() => { setPickedMcp(prev => prev.filter(row => row.id !== item.id)); setComposerNotice(`已移除 MCP ${item.label}`); }}>×</button>
                      </span>
                    ))}
                    {pickedTools.map((item) => (
                      <span
                        key={item.id}
                        data-chip=""
                        data-chip-kind="tool"
                        data-chip-id={item.id}
                        className="desk-chip"
                      >
                        <span className="px-2 py-1">{item.label}
                        {NEED_REPO.has(item.id) && pickedRepo ? ` · ${pickedRepo}` : ""}
                        </span><button type="button" className="desk-menu-item" aria-label={`移除工具 ${item.label}`} onClick={() => { removeTool(item.id); setComposerNotice(`已移除工具 ${item.label}`); }}>×</button>
                      </span>
                    ))}
                  </div>
                ) : null}
                <SlashMenu
                  open={menuOpen}
                  parent={slashParent}
                  repoPicking={repoPicking}
                  items={menuItems}
                  active={menuActive}
                  error={menuError}
                  loading={menuLoading}
                  onParent={(parent) => {
                    setRepoPicking(false);
                    setPendingRepoTool(null);
                    setSlashParent(parent);
                  }}
                  onPick={pickSlash}
                  onClose={() => { setRepoPicking(false); setPendingRepoTool(null); setSlashDismissed(draft); setComposerNotice("菜单已关闭，输入内容保留。"); }}
                  onBack={() => { setRepoPicking(false); setPendingRepoTool(null); setSlashParent("tool"); setDraft("/"); }}
                  onRetry={() => { catalogGen.current++; setComposerError(""); if (!composer?.ok) setComposer(null); setDocCatalog(null); setMcpCatalog(null); setComposerNotice("正在重新读取菜单。"); }}
                />
                <form
                  className="composer-form"
                  data-chat-draft={draft}
                  onSubmit={(ev) => {
                    ev.preventDefault();
                    send();
                  }}
                  onKeyDown={(ev) => {
                    if (menuOpen) {
                      menuKeyRef.current(ev.nativeEvent);
                      ev.stopPropagation();
                      return;
                    }
                    const target = ev.target as HTMLElement;
                    if (target.tagName === "TEXTAREA" && ev.key === "Enter" && !ev.shiftKey && !ev.nativeEvent.isComposing) { ev.preventDefault(); send(); return; }
                    if (target.tagName !== "INPUT" && target.tagName !== "TEXTAREA") return;
                    if (ev.key !== "ArrowUp" && ev.key !== "ArrowDown") return;
                    if (target instanceof HTMLTextAreaElement && (target.value.includes("\n") || target.selectionStart !== target.selectionEnd)) return;
                    if (recallHistory(ev.key)) ev.preventDefault();
                  }}
                >
                <textarea
                  value={draft}
                  onChange={(ev) => { setDraft(ev.target.value); setSlashDismissed(null); }}
                  placeholder="发送视频链接总结，或输入 / 选技能、工具、文档"
                  className="composer-textarea"
                  rows={3}
                  aria-label="消息输入"
                />
                <div className="composer-actions"><div><button type="button" className="desk-menu-item" onClick={() => setLocalDialog("file")}>＋ 附件</button><button type="button" className="desk-menu-item" onClick={() => { setDraft(text => `${text}${text && !/\s$/.test(text) ? " " : ""}/`); setSlashDismissed(null); }}>工具 /</button></div>
                <Button type="submit" variant="primary" disabled={busy || noteBusy}>
                  {busy || noteBusy ? <LoadingText text="处理中…" /> : "发送 ↑"}
                </Button>
                </div>
                </form>
                {composerNotice && <p className="composer-feedback" role="status">{composerNotice}</p>}
              </div>
            </section>
          ) : pane === "settings" ? (
            <section
              key="settings"
              className="flex-1 overflow-y-auto px-8 py-6"
            >
              <nav className="workspace-tabs" aria-label="设置分类">{[['assistant','模型与人设'],['connections','连接与网络'],['appearance','桌宠与外观'],['about','版本与诊断']].map(([id,label])=><button type="button" key={id} aria-pressed={settingsSection===id} onClick={()=>setSettingsSection(id)}>{label}</button>)}</nav>
              <div hidden={settingsSection!=='about'}><ReleaseSettings info={info} onShowIntro={releaseIntro.show}/>{developerMode&&<button type="button" className="desk-menu-item" onClick={()=>setAgentDebugOpen(true)}>调用调试</button>}</div>
              <div hidden={settingsSection!=='appearance'}><PetSettings info={info} debug={!!debugKind}/></div>
              <div hidden={settingsSection!=='connections'}><ModelNetworkSettings info={info} debug={!!debugKind}/><StatusGrid cards={statuses} onOpenFeishu={()=>void openFeishu()}/></div>
              <div hidden={!['assistant','appearance'].includes(settingsSection)}><SettingsPane section={settingsSection==='appearance'?'appearance':'assistant'}
                onOpenGuide={() => {
                  if (!info) return;
                  void rpc<SetupStatus>(info, "load_onboarding").then((setup) => {
                    setSetupStatus(setup); setShowGuide(true);
                  }).catch((err) => setSettingsError(String(err)));
                }}
                model={modelForm}
                models={modelItems}
                editingId={editingModelId}
                modelError={settingsError}
                notice={settingsNotice}
                persona={personaForm?.persona || ""}
                personaError={personaError}
                personaNotice={personaNotice}
                busy={settingsBusy}
                keyEpoch={keyEpoch}
                dark={dark}
                photo={!!bgUrl}
                bgError={bgError}
                onToggleTheme={() => setDark((v) => !v)}
                onPickFile={(file) => void pickBackground(file)}
                onClearBackground={() => clearBgRef.current()}
                onSave={(draft) => void saveSettings(draft)}
                onTest={(draft) => void testSettings(draft)}
                onEdit={editModel}
                onDelete={(id) => void deleteModel(id)}
                onSavePersona={(text) => void savePersona(text)}
              /></div>
            </section>
          ) : pane === "feishu" ? (
            <section
              key="feishu"
              className="flex-1 overflow-y-auto px-8 py-6"
            >
              <FeishuPane
                snap={feishu}
                busy={feishuBusy}
                debug={boardClock !== null}
                onLogin={() => void startFeishuLogin()}
                onLogout={() => void logoutFeishu()}
              />
              <FeishuAgentPane info={info} debug={boardClock !== null} />
            </section>
          ) : pane === "automation" ? (
            <section key="automation" data-page="automation" className="flex-1 overflow-y-auto px-8 py-6">
              <h1 className="text-2xl font-semibold">自动化</h1>
              <p className="mt-2 text-sm text-muted-foreground">管理每日资讯、运行计划和执行记录。</p>
              <AutomationPane info={info} debug={boardClock !== null} />
            </section>
          ) : pane === "video" ? (
            <section key="video" data-page="video" className="flex-1 overflow-y-auto px-8 py-6">
              <h1 className="text-2xl font-semibold">视频学习</h1>
              <p className="mt-2 text-sm text-muted-foreground">从实际视频资料中提炼要点，查看出处并整理笔记。</p>
              <VideoPane info={info} debug={boardClock !== null} />
            </section>
          ) : pane === "work" ? (
            <section key="work" data-page="work" className="flex min-h-0 flex-1 overflow-hidden">
              <WorkBoardPane info={info} debug={debugKind === "work"} target={workTarget} onCalendar={openCalendar} onOpen={async conversation => {
                if (!info) throw new Error("后端尚未连接。");
                await rpc(info, "collaboration_action", {action:{type:"select",id:conversation,conversation,request_id:crypto.randomUUID()}});
                setPane("collaboration"); window.dispatchEvent(new CustomEvent("tebb-show-tasks"));
              }} />
            </section>
          ) : pane === "monitor" ? (
            <section key="monitor" data-page="monitor" className="flex-1 overflow-y-auto px-8 py-6">
              <h1 className="text-2xl font-semibold">AI 执行</h1>
              <p className="mt-2 text-sm text-muted-foreground">查看桌面与飞书会话的执行过程，停止任务或继续聊天。</p>
              <WorkspaceRuns info={info} onOpen={conversation => { if (!info) return; void rpc(info, 'collaboration_action', { action: { type: 'select', id: conversation, conversation, request_id: crypto.randomUUID() } }).then(() => { setPane('collaboration'); window.dispatchEvent(new CustomEvent('tebb-show-tasks')); }).catch(e => setError(String(e))); }} />
              <details className="mt-6"><summary>其他渠道与旧任务记录</summary><AgentMonitor info={info} debug={boardClock !== null} /></details>
            </section>
          ) : (
            <section
              key="board"
              data-page="calendar"
              className="flex min-h-0 flex-1 overflow-hidden"
            >
              <CalendarWorkspace info={info} target={calendarTarget} onItem={openWorkItem}
                data={board}
                loading={boardLoading}
                loadError={boardLoadError}
                debug={boardClock !== null}
                onRefresh={() => { if(!debugKind)void loadBoardSnapshot(true); }}
                deleting={rowDeleting}
                deleteErrors={agendaErrors}
                onDeleteEvent={deleteAgenda}
                taskErrors={taskErrors}
                onDeleteTask={(guid) => void deleteTask(guid)}

              />
            </section>
          )}
        </PageStack>
        </div>
        </div>
        </SideWorkspace>
        </div>
      </main>

      {error && pane !== "chat" && <div className="tebb-global-error" role="alert"><span>{error}</span><button type="button" aria-label="关闭错误提示" onClick={() => setError("")}>×</button></div>}
      {cropSrc ? (
        <BackgroundCrop
          src={cropSrc}
          error={cropError}
          onConfirm={confirmCrop}
          onCancel={() => {
            setCropSrc("");
            setCropError("");
          }}
          onBroken={() => {
            setCropSrc("");
            setCropError("");
            setBgError("这张图打不开。恢复：换一张 png、jpg 或 webp。");
          }}
        />
      ) : null}
    </div>
  );
}

function ModelPick(props: {
  modelId: string;
  items: ModelEntry[];
  busy: boolean;
  onChange: (id: string) => void;
}) {
  return (
    <label className="flex shrink-0 items-center gap-2 text-xs text-muted-foreground">
      模型
      <select
        data-model-pick
        value={props.modelId}
        disabled={props.busy || props.items.length === 0}
        className="composer-select"
        onChange={(ev) => props.onChange(ev.target.value)}
      >
        {props.items.length === 0 ? <option value="">没有模型</option> : null}
        {props.items.map((item) => (
          <option key={item.id} value={item.id}>
            {item.model}
          </option>
        ))}
      </select>
    </label>
  );
}

function ThinkDots(props: { wait?: boolean }) {
  return (
    <span className="think-dots" data-thinking-dots="" data-think-wait={props.wait ? "1" : "0"} aria-hidden="true">
      <span data-thinking-dot="" />
      <span data-thinking-dot="" />
      <span data-thinking-dot="" />
    </span>
  );
}

function userShown(text: string, docs: { id: string; label: string }[]): string {
  const names = docs.map((item) => {
    const href = openableHref(item.id);
    const label = item.label.split("[").join("［").split("]").join("］");
    return href ? `[${label}](${href})` : label;
  });
  const head = [text, ...names].filter(Boolean).join("\n");
  return head || "按上面的指定执行。";
}

function DocChipName(props: { id: string; label: string }) {
  return (
    <span className="px-2 py-1">
      <MdLink href={props.id}>{props.label}</MdLink>
    </span>
  );
}

function Bubble(props: ChatItem & { live?: boolean }) {
  const mine = props.role === "user";
  const waiting = !mine && !!props.live && !props.text && !props.thinking;
  let body: ReactNode = "…";
  if (props.text) {
    body = mine ? <PlainLinks text={props.text} /> : <Markdown text={props.text} />;
  } else if (waiting) {
    body = <ThinkDots wait />;
  } else if (!mine && props.thinking) {
    body = null;
  }
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`} data-bubble data-role={props.role}>
      <Card className={`min-w-0 max-w-[70%] text-sm leading-6 ${mine ? "whitespace-pre-wrap" : ""}`} selected={mine}>
        <CardBody>
        {!mine && props.thinking ? <ThinkCard text={props.thinking} live={!!props.live} /> : null}
        {!mine && props.notes && props.notes.length > 0 ? (
          <ToolCard notes={props.notes} live={!!props.live} />
        ) : null}
        {props.knowledge ? <KnowledgeTraceView trace={props.knowledge} /> : null}
        {body}
        {!mine ? <TurnMeta item={props} /> : null}
        </CardBody>
      </Card>
    </div>
  );
}

function TurnMeta(props: { item: ChatItem }) {
  const item = props.item;
  if (typeof item.elapsed_s !== "number") return null;
  const temperature = typeof item.temperature === "number" ? item.temperature.toFixed(1) : "";
  const topP = typeof item.top_p === "number" ? item.top_p.toFixed(2) : "";
  return (
    <p data-turn-meta className="mt-2 text-xs text-muted-foreground">
      {`用时 ${item.elapsed_s.toFixed(1)} 秒 · 输入 ${item.input_tokens ?? 0} · 输出 ${item.output_tokens ?? 0} · 合计 ${item.total_tokens ?? 0}${typeof item.react_loops === "number" ? ` · 循环 ${item.react_loops}` : ""}`}
      <br />
      {`${item.model || ""} · ${item.reasoning_effort || ""} · 温度 ${temperature} · top_p ${topP}`}
    </p>
  );
}

function ThinkCard(props: { text: string; live: boolean }) {
  const [opened, setOpened] = useState(props.live);
  useEffect(() => {
    setOpened(props.live);
  }, [props.live]);
  return (
    <details
      className="tool-card"
      open={opened}
      data-thinking=""
      data-thinking-live={props.live ? "1" : "0"}
      data-thinking-body={props.text}
      onToggle={(ev) => {
        if (props.live) return;
        setOpened(ev.currentTarget.open);
      }}
    >
      <summary>
        思考
        {props.live ? <ThinkDots /> : null}
      </summary>
      <p
        className={`whitespace-pre-wrap think-body${props.live ? " think-body-live" : ""}`}
        data-thinking-edge={props.live ? "1" : "0"}
      >
        {props.text}
      </p>
    </details>
  );
}

function SamplingBar(props: { value: Sampling; onChange: (next: Sampling) => void }) {
  return (
    <div
      className="composer-sampling"
      data-sampling=""
      data-sampling-effort={props.value.reasoning_effort}
      data-sampling-temperature={props.value.temperature.toFixed(1)}
      data-sampling-top-p={props.value.top_p.toFixed(2)}
    >
      <label>思考 · <select className="composer-select" aria-label="思考强度" value={props.value.reasoning_effort} onChange={e => props.onChange({ ...props.value, reasoning_effort: e.target.value as Sampling["reasoning_effort"] })}>{EFFORTS.map(effort => <option key={effort} value={effort}>{effort}</option>)}</select></label>
      <details className="composer-advanced"><summary>参数 ⌄</summary><div className="composer-parameters">
      <label className="flex items-center gap-2">
        温度
        <input
          className="w-24"
          type="range"
          min={0}
          max={1}
          step={0.1}
          value={props.value.temperature}
          data-temperature=""
          onChange={(ev) => props.onChange({ ...props.value, temperature: Number(ev.target.value) })}
        />
        <span data-temperature-value="">{props.value.temperature.toFixed(1)}</span>
      </label>
      <label className="flex items-center gap-2">
        top_p
        <input
          className="w-24"
          type="range"
          min={0.01}
          max={1}
          step={0.01}
          value={props.value.top_p}
          data-top-p=""
          onChange={(ev) => props.onChange({ ...props.value, top_p: Number(ev.target.value) })}
        />
        <span data-top-p-value="">{props.value.top_p.toFixed(2)}</span>
      </label>
      </div></details>
    </div>
  );
}

function ToolCard(props: { notes: string[]; live: boolean }) {
  const title = props.live
    ? props.notes[props.notes.length - 1]
    : props.notes.length === 1
      ? props.notes[0]
      : `${props.notes.length} 次工具调用`;
  return (
    <details className="tool-card" open={props.live || undefined}>
      <summary>{title}</summary>
      <ul>
        {props.notes.map((line, idx) => (
          <li key={`${idx}-${line}`}>{line}</li>
        ))}
      </ul>
    </details>
  );
}
