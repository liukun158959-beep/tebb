import { ClarificationCard } from './clarification-card';
import { openProjectRuntime } from './project-runtime-events';
import { ContextDebug } from './context-debug';
import { DeliveryFeedback } from './delivery-feedback';
import { TaskBudgetView } from './task-budget';
import { pendingClarifications } from './clarification-state';
import { useEffect, useReducer, useRef, useState, type ReactNode } from "react";
import { DEMO_STORAGE, RUN_LABELS, STEP_LABELS, demoReducer, initialDemo, restoreDemo, runActive,
  type DemoAgent, type DemoConversation, type DemoProject, type DemoAction, type DemoRun, type DemoState } from "./chat-prototype-state";
import { WorkspaceRail } from "./workspace-shell";
import { rpc, type BackendInfo } from "./api";
import type { LocalSource } from "./local-sources";
import { startupReady } from "./startup";
import "./chat-prototype.css";

import { ComposerPicker } from './composer-popover';
import { StreamText } from "./stream-text";
import { useAutoGrowInput } from './auto-grow-input';
import { DraftNotice, ExecutionTree, executionMessage, visibleChatMessage, hasCalendarContext } from './run-progress';
import { Markdown } from "./Markdown";
import { ProjectMigration } from './project-migration';
import { ChatSidebar } from './chat-sidebar';
import { NewConversationSetup } from './new-conversation-setup';
import { useConversationDraft, draftConversation, type FirstMessage } from './conversation-draft';
import { CompanionAvatar as Avatar, RailButton, AVATAR_NAMES, AppIcon, readDeveloperMode, saveDeveloperMode } from "./companion-ui";

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prior = document.activeElement as HTMLElement | null;
    box.current?.querySelector<HTMLElement>("input, textarea, button")?.focus();
    return () => prior?.focus?.();
  }, []);
  return <div className="cp-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="cp-modal" ref={box} role="dialog" aria-modal="true" aria-label={title} onKeyDown={event => {
      if (event.key === "Escape") { event.stopPropagation(); onClose(); }
      if (event.key !== "Tab") return;
      const items = Array.from(box.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href]') || []);
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}><header><div><span className="cp-eyebrow">TEBB / YOUR AGENT CIRCLE</span><h2>{title}</h2></div><button className="cp-icon-button" onClick={onClose} aria-label="关闭弹窗">×</button></header>{children}</div>
  </div>;
}

function AgentForm({ agent, agents, onSave, onClose, real = false, models = [], actionError = "", busy = false, developer = false }: { agent: DemoAgent; agents: DemoAgent[]; onSave: (agent: DemoAgent) => void; onClose: () => void; real?: boolean; models?: { id: string; model: string }[]; actionError?: string; busy?: boolean; developer?: boolean }) {
  const [form, setForm] = useState(agent);
  const [error, setError] = useState("");
  return <Modal title={agent.id ? `${agent.name} 的设置` : "创建 Agent"} onClose={onClose}>
    <form className="cp-form" onSubmit={event => {
      event.preventDefault();
      if (!form.name.trim() || (/[\s@]/.test(form.name.trim()) && !(form.id==='tebb'&&form.name.trim()==='tebb · 通用助手')) || agents.some(a => a.id !== form.id && a.name === form.name.trim())) { setError("名称不能为空、重复，或包含空格和 @。"); return; }
      onSave(form);
    }}>
      <p className="cp-muted">{real ? "配置将用于之后的真实任务。此处不填写密钥；模型沿用设置页已有条目。" : "配置仅用于原型演示。请勿填写密钥或真实隐私信息。"}</p>
      <label>名称<input value={form.name} maxLength={24} onChange={event => setForm({ ...form, name: event.target.value })} /></label>
      <fieldset><legend>伙伴头像</legend><div className="cp-avatar-picker">{Object.entries(AVATAR_NAMES).map(([key, name]) => <button type="button" key={key} aria-label={name} aria-pressed={form.avatar === key} onClick={() => setForm({ ...form, avatar: key })}><Avatar agent={{ ...form, avatar: key }} /></button>)}</div></fieldset>
      <label>性格与说话方式<textarea rows={2} value={form.personality || ""} maxLength={160} placeholder="好奇、温柔，还是有点冷幽默？" onChange={event => setForm({ ...form, personality: event.target.value })} /></label>
      <label>擅长的事<textarea rows={2} value={form.role} maxLength={160} onChange={event => setForm({ ...form, role: event.target.value })} /></label>
      <label>{real ? "执行模型" : "模型标记"}{real ? <select value={form.model} onChange={event => setForm({ ...form, model: event.target.value })}><option value="">沿用当前激活模型</option>{models.map(model => <option key={model.id} value={model.id}>{model.model}</option>)}</select> : <input value={form.model} maxLength={80} onChange={event => setForm({ ...form, model: event.target.value })} />}<small>{real ? "提交任务时确定模型，执行中的任务保持原配置。" : "用于体验配置位置，尚未绑定真实模型。"}</small></label>
      <fieldset><legend>允许使用的工具{real ? "" : " · 演示配置"}</legend><div className="cp-checks">{(real ? ["联网搜索", "网页读取", "工作区读取", "文件编辑", "终端", "扩展只读工具", "构建图表", "本地日程读取", "项目环境"] : ["联网搜索", "文件", "终端", "飞书"]).map(tool => <label key={tool}><input type="checkbox" checked={form.tools.includes(tool)}
        onChange={event => setForm({ ...form, tools: event.target.checked ? [...form.tools, tool] : form.tools.filter(item => item !== tool) })} />{tool}</label>)}</div></fieldset>
      <p className="cp-muted">项目环境允许在独立Docker沙箱下载公开仓库依赖，不共享本机目录或产品密钥；需先安装并登录Docker Sandboxes。</p>
      {developer && <label>Agent 私有记忆<textarea rows={3} value={form.memory} maxLength={2000} onChange={event => setForm({ ...form, memory: event.target.value })} /><small>{real ? "仅注入该 Agent 的上下文，不自动分享给其他 Agent。" : "这段演示记忆仅在该 Agent 的设置中展示，不自动加入群消息。"}</small></label>}
      {(error || actionError) && <p role="alert" className="cp-error">{error || actionError}</p>}
      <footer><button type="button" className="cp-button" onClick={onClose}>取消</button><button className="cp-button cp-primary" type="submit" disabled={busy}>{real ? "保存配置" : "保存原型配置"}</button></footer>
    </form>
  </Modal>;
}

function GroupForm({ editing, agents, projects, projectId, onCreate, onClose, real = false, actionError = "", busy = false }: { agents: DemoAgent[]; projects: DemoProject[]; projectId: string; onCreate: (title: string, members: string[], goal: string, project: string) => void; onClose: () => void; real?: boolean; actionError?: string; busy?: boolean; editing?: DemoConversation | null }) {
  const [title, setTitle] = useState(editing?.title || "");
  const [goal, setGoal] = useState(editing?.goal || "");
  const [members, setMembers] = useState(editing?.members || agents.map(agent => agent.id));
  const [project, setProject] = useState(editing?.project_id || projectId);
  return <Modal title={editing ? "调整协作群" : "新建协作群"} onClose={onClose}><form className="cp-form" onSubmit={event => { event.preventDefault(); if (title.trim() && members.length >= 2) onCreate(title, members, goal, project); }}>
    <label>群名称<input value={title} maxLength={40} placeholder="例如：开源项目观察" onChange={event => setTitle(event.target.value)} required /></label>
    {!editing && <label>所属项目<select aria-label="协作群所属项目" value={project} onChange={event => setProject(event.target.value)}><option value="">未归类</option>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>}
    <label>共同目标<textarea rows={2} value={goal} maxLength={500} placeholder="这个群要一起完成什么？" onChange={event => setGoal(event.target.value)} /></label>
    <fieldset><legend>邀请 Agent · 至少两位</legend><div className="cp-member-options">{agents.map(agent => <label key={agent.id}><input type="checkbox" checked={members.includes(agent.id)}
      onChange={event => setMembers(event.target.checked ? [...members, agent.id] : members.filter(id => id !== agent.id))} /><Avatar agent={agent} /><span><strong>{agent.name}</strong><small>{agent.role}</small></span></label>)}</div></fieldset>
    <p className="cp-muted">你是群里唯一的真人。创建后会标明负责协调的伙伴，也可以直接 @ 其他成员。</p>
    {actionError && <p role="alert" className="cp-error">{actionError}</p>}
    {real && members.length > 6 && <p role="alert" className="cp-error">每群最多6位伙伴，请减少成员。</p>}
    <footer><button type="button" className="cp-button" onClick={onClose}>取消</button><button className="cp-button cp-primary" type="submit" disabled={busy || !title.trim() || members.length < 2 || (real && members.length > 6)}>创建协作群</button></footer>
  </form></Modal>;
}

export function ChatPrototype({ standalone = false, live, composeRequest, navigation, developerMode, onDeveloperMode, backend, onAttach, attachments: providedAttachments = [], onDetach, onProject, onFile, onTerminal, onCalendar }: { backend?: BackendInfo | null; onAttach?: () => void; attachments?: LocalSource[]; onDetach?: (id: string) => void; onProject?: () => void; onFile?: () => void; onTerminal?: () => void; onCalendar?: () => void; standalone?: boolean; composeRequest?: { id: number; text: string }; navigation?: ReactNode; developerMode?: boolean; onDeveloperMode?: (value: boolean) => void; live?: { state: DemoState; act: (action: DemoAction) => Promise<boolean>; confirmAction?: (id:string)=>void; busy: boolean; error: string } }) {
  const [demoState, demoDispatch] = useReducer(demoReducer, undefined, () => {
    if (live) return initialDemo();
    try { return restoreDemo(localStorage.getItem(DEMO_STORAGE)); } catch { return initialDemo(); }
  });
  const state = live?.state || demoState;
  const real = !!live;
  const sendAction = async (action: DemoAction) => { if (live) return live.act(action); const next=demoReducer(state,action);demoDispatch(action);return !next.notice; };
  const dispatch = (action: DemoAction) => { void sendAction(action); };
  const [storageError, setStorageError] = useState("");
  const [listView, setListView] = useState<"history" | "companions">("history");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const newConversation=useConversationDraft();
  const [discardDraft,setDiscardDraft]=useState(false);
  const [mode, setMode] = useState<"auto">("auto");
  const [selectedSkills, setSelectedSkills] = useState<Record<string, string[]>>({});
  const [picker, setPicker] = useState<"" | "members" | "skills">("");
  const [pickerQuery, setPickerQuery] = useState("");
  const [pickerIndex, setPickerIndex] = useState(0);
  const [cursor, setCursor] = useState(0);
  const [modelChoices, setModelChoices] = useState<Record<string,string>>({});
  const [sampling, setSampling] = useState({ reasoning_effort: "high", temperature: 0.7, top_p: 1 });
  const [target, setTarget] = useState("");
  const [updateRun, setUpdateRun] = useState("");
  const [focusedRun, setFocusedRun] = useState("");
  const [automatic, setAutomatic] = useState(false);
  const [tab, setTab] = useState<"tasks" | "members" | "inbox">("tasks");
  const [inspector, setInspector] = useState(false);
  const [localDeveloper, setLocalDeveloper] = useState(readDeveloperMode);
  const developer = developerMode ?? localDeveloper;
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const [deleting, setDeleting] = useState("");
  const [renaming, setRenaming] = useState("");
  const [renameText, setRenameText] = useState("");
  const menuBox = useRef<HTMLDivElement>(null);
  const menuTrigger = useRef<HTMLElement | null>(null);
  const [mobileList, setMobileList] = useState(false);
  const [editingGroup, setEditingGroup] = useState<DemoConversation | null>(null);
  const [groupOpen, setGroupOpen] = useState(false);
  const [groupProject, setGroupProject] = useState('');
  const [projectForm, setProjectForm] = useState<{ id?: string; name: string; instructions?:string } | null>(null);
  const [projectPicking,setProjectPicking]=useState(false),[migrationOpen,setMigrationOpen]=useState(false);
  const [deletingProject, setDeletingProject] = useState('');
  const [movingConversation, setMovingConversation] = useState('');
  const [moveProject, setMoveProject] = useState('');
  const [collapsedProjects, setCollapsedProjects] = useState<Record<string, boolean>>({});
  const [editingAgent, setEditingAgent] = useState<DemoAgent | null>(null);
  const [artifactId, setArtifactId] = useState("");
  const [resetOpen, setResetOpen] = useState(false);
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const end = useRef<HTMLDivElement>(null);
  const messageList = useRef<HTMLDivElement>(null), following = useRef(true);
  const input = useRef<HTMLTextAreaElement>(null);
  const editingDraft=newConversation.active?newConversation.draft:null;
  const draftLocked=!!editingDraft?.attempt;
  const conversation=editingDraft?draftConversation(editingDraft):state.conversations.find(item=>item.id===state.selected)!;
  const projects = state.projects || [];
  const currentProject = projects.find(p => p.id === conversation.project_id);
  const projectNameError = projectForm && projects.some(p => p.id !== projectForm.id && p.name.toLowerCase() === projectForm.name.trim().toLowerCase()) ? '项目名称已存在。' : '';
  const members = state.agents.filter(agent => conversation.members.includes(agent.id));
  const runs = state.runs.filter(run => run.conversation === conversation.id);
  const messages = state.messages.filter(message => message.conversation === conversation.id && visibleChatMessage(message, runs));
  const active = state.runs.filter(runActive);
  const visibleRuns = developer ? runs : runs.filter(run => !run.parent && (run.require_execution || run.state==='needs_input' || run.chain?.children.length || runs.some(child=>child.parent===run.id) || !["chat", "route", "compress"].includes(run.kind || "")));
  const pending = state.requests.filter(request => request.state === "pending");
  const waitingInputs=pendingClarifications(state.runs);
  const pendingCount=pending.length+waitingInputs.length;
  const selectedRun = state.runs.find(run => run.id === updateRun);
  const currentArtifact = state.runs.find(run => run.id === artifactId);
  const draft = drafts[conversation.id] || "";
  useAutoGrowInput(input, draft, conversation.id);
  const setDraft = (text: string) => setDrafts(previous => ({ ...previous, [conversation.id]: text }));
  const [attachmentMap, setAttachmentMap] = useState<Record<string, LocalSource[]>>({});
  const attachments = attachmentMap[conversation.id] || [];
  useEffect(() => { if (providedAttachments.length&&!draftLocked) setAttachmentMap(old => ({ ...old, [conversation.id]: [...new Map([...(old[conversation.id] || []), ...providedAttachments].map(a => [a.id,a])).values()] })); }, [providedAttachments]);
  const removeAttachment = (id: string) => { if(draftLocked)return;setAttachmentMap(old => ({ ...old, [conversation.id]: attachments.filter(a => a.id!==id) })); onDetach?.(id); };
  const modelPick = modelChoices[conversation.id] || "";
  const setModelPick = (id: string) => {if(!draftLocked)setModelChoices(old => ({ ...old, [conversation.id]: id }));};
  const skills = selectedSkills[conversation.id] || [];
  const options = picker === 'members' ? members.filter(a => a.name.toLowerCase().includes(pickerQuery.toLowerCase())).map(a => ({ id: a.id, name: a.name, detail: a.personality || a.role })) : (state.skills || []).filter(s => s.id.toLowerCase().includes(pickerQuery.toLowerCase())).map(s => ({ id: s.id, name: s.id, detail: s.description }));
  const member = (id: string) => state.agents.find(agent => agent.id === id);
  const mentioned = [...new Set((draft.match(/(?:^|\s)@[^\s@]+/g) || []).map(token => token.trim().slice(1)))];
  const recipient = mentioned.length === 1 ? members.find(a => (a.name === mentioned[0]||a.id===mentioned[0])) || member(conversation.coordinator) : member(conversation.coordinator);
  const recipientModel = state.models?.find(model => model.id === (recipient?.model || state.active_model))?.model || '默认模型';

  useEffect(()=>{
    const pending=newConversation.draft;
    if(!pending?.attempt||live?.busy||!state.conversations.some(c=>c.id===pending.id)||!state.messages.some(m=>m.conversation===pending.id&&m.sender==='user'))return;
    setDrafts(old=>({...old,[pending.id]:''}));
    setSelectedSkills(old=>({...old,[pending.id]:[]}));
    setAttachmentMap(old=>({...old,[pending.id]:[]}));
    pending.attempt.attachments?.forEach(id=>onDetach?.(id));
    if(newConversation.active&&state.selected!==pending.id)dispatch({type:'select',id:pending.id});
    live?.confirmAction?.(pending.id);
    setDiscardDraft(false);
    newConversation.finish();
  },[state.conversations,state.messages,state.selected,live?.busy,newConversation.draft?.attempt,newConversation.active]);
  useEffect(() => {
    if (real) return;
    try { localStorage.setItem(DEMO_STORAGE, JSON.stringify(state)); setStorageError(""); }
    catch { setStorageError("原型状态未能保存，刷新后可能丢失。可继续体验。原型中不要填写敏感资料。"); }
  }, [state, real]);
  useEffect(() => { if (standalone) startupReady(); }, [standalone]);
  useEffect(() => {
    if (!automatic || real) return;
    const timer = window.setInterval(() => dispatch({ type: "advance" }), 3500);
    return () => window.clearInterval(timer);
  }, [automatic, real]);
  useEffect(() => { if (!active.length) setAutomatic(false); }, [active.length]);
  useEffect(() => { following.current=true; }, [state.selected]);
  useEffect(() => { if(following.current && messageList.current)messageList.current.scrollTop=messageList.current.scrollHeight; }, [state.selected,state.revision,messages.length]);
  useEffect(() => { if (tab === "tasks" && inspector && focusedRun) document.getElementById(`cp-run-${focusedRun}`)?.scrollIntoView?.({ block: "nearest" }); }, [focusedRun, tab, inspector]);

  useEffect(() => {
    if (!menu) return;
    menuBox.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => menuTrigger.current?.focus?.();
  }, [menu]);
  useEffect(() => {
    if (!composeRequest) return;
    if(draftLocked){setStorageError('首条消息结果尚待核对，请确认后再加入引用。原草稿保持不变。');return;}
    setPicker("");
    setDrafts(old => {
      const previous = old[conversation.id] || "";
      return { ...old, [conversation.id]: `${previous}${previous ? "\n" : ""}${composeRequest.text}`.slice(0, 4000) };
    });
    setStorageError("内容已加入当前草稿，尚未发送（单条消息最多4000字）。");
  }, [composeRequest]);
  useEffect(() => { if (conversation.project_id) setCollapsedProjects(old => ({ ...old, [conversation.project_id!]: false })); }, [conversation.id, conversation.project_id]);
  useEffect(()=>{if(!editingDraft&&typeof CustomEvent!=='undefined'&&window.dispatchEvent){window.dispatchEvent(new CustomEvent('tebb-project-selected',{detail:{project_id:conversation.project_id||'',revision:state.workspace?.revision}}));if(state.workspace)window.dispatchEvent(new CustomEvent('tebb-workspace-changed',{detail:state.workspace}));}},[conversation.project_id,state.workspace?.revision,!!editingDraft]);
  useEffect(() => { if (!window.addEventListener) return;
    const create = () => beginNewConversation();
    const detail = () => { setTab('tasks'); setInspector(true); };
    window.addEventListener('tebb-new-chat', create); window.addEventListener('tebb-show-tasks', detail);
    return () => { window.removeEventListener('tebb-new-chat', create); window.removeEventListener('tebb-show-tasks', detail); };
  }, [state.selected, live,conversation.project_id]);
  function changeDraft(value: string, position = value.length) {
    if(draftLocked)return;
    setDraft(value); setCursor(position); setPickerIndex(0);
    const match = /(?:^|\s)([@/])([^\s@/]*)$/.exec(value.slice(0, position));
    setPicker(match ? match[1] === '@' ? 'members' : 'skills' : ''); setPickerQuery(match?.[2] || '');
  }
  function chooseOption(index: number) {
    if(draftLocked)return;
    const option = options[index]; if (!option) return;
    const head = draft.slice(0, cursor).replace(/(?:@|\/)[^\s@/]*$/, picker === 'members' ? '@'+option.name+' ' : '');
    setDraft(head + draft.slice(cursor));
    if (picker === 'skills') setSelectedSkills(old => ({ ...old, [conversation.id]: [...new Set([...skills, option.id])] }));
    setPicker(''); input.current?.focus();
  }
  function toggleDeveloper() {
    const next = !developer;
    setStorageError(saveDeveloperMode(next)); setLocalDeveloper(next); onDeveloperMode?.(next);
  }
  function openMenu(id: string, event: React.MouseEvent<HTMLElement> | React.KeyboardEvent<HTMLElement>) {
    event.preventDefault(); menuTrigger.current = event.currentTarget;
    const bounds = event.currentTarget.getBoundingClientRect();
    const origin = event.currentTarget.closest?.(".cp-root")?.getBoundingClientRect() || { left: 0, top: 0 };
    const x = ("clientX" in event ? event.clientX : bounds.right) - origin.left;
    const y = ("clientY" in event ? event.clientY : bounds.top) - origin.top;
    setMenu({ id, x: Math.max(8, Math.min(x, (window.innerWidth || 1000) - 220)), y: Math.max(8, Math.min(y, (window.innerHeight || 700) - 180)) });
  }
  function menuKeys(id: string, event: React.KeyboardEvent<HTMLElement>) {
    if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) openMenu(id, event);
  }
  function select(id: string) {
    newConversation.hide();
    dispatch({ type: "select", id }); setTarget(""); setUpdateRun(""); setFocusedRun(""); setMobileList(false); setPicker("");
  }
  async function supplement(run: DemoRun) {
    if(live){if(!await sendAction({type:'select',id:run.conversation}))return;newConversation.hide();}else select(run.conversation); setUpdateRun(run.id); setFocusedRun(run.id); setInspector(true); setTab("tasks");
    input.current?.focus();
  }
  async function submit() {
    if(live?.busy)return;
    if (!draft.trim()) return;
    const action:FirstMessage=editingDraft?.attempt||{type:'send',conversation:selectedRun?.conversation||conversation.id,
      ...(editingDraft?{new_chat:{agent_id:editingDraft.agent_id,project_id:editingDraft.project_id}}:{}),
      text:draft,target,mode,run:updateRun||undefined,skills,attachments:attachments.map(a=>a.id),model_id:modelPick,...(developer?{sampling}:{})};
    if(editingDraft){
      if(!editingDraft.attempt&&(!state.agents.some(a=>a.id===editingDraft.agent_id)||(editingDraft.project_id&&!projects.some(p=>p.id===editingDraft.project_id)))){setStorageError('伙伴或项目已移除，请重新选择后发送。');return;}
      newConversation.freeze(action);if(!await sendAction(action))newConversation.failed(editingDraft.id);
      return;
    }
    if(!await sendAction(action))return;
    following.current=true;
    setDraft(""); setUpdateRun(""); setPicker(""); setSelectedSkills(old => ({ ...old, [conversation.id]: [] })); setAttachmentMap(old => ({ ...old, [conversation.id]: [] })); attachments.forEach(a => onDetach?.(a.id));
  }
  function addAgent() {
    setEditingAgent({ id: "", name: "", role: "", mark: "A", tone: "sage", model: real ? "" : "自定义模型 · 演示", tools: [], memory: "", avatar: "owl", personality: "" });
  }
  function beginNewConversation(agent_id?:string){
    newConversation.begin(currentProject?.id||'',agent_id);setTarget('');setUpdateRun('');setPicker('');setFocusedRun('');setMobileList(false);setStorageError('');
  }
  function clearNewDraft(){
    const id=newConversation.draft?.id;
    if(id){setDrafts(old=>({...old,[id]:''}));setSelectedSkills(old=>({...old,[id]:[]}));setModelChoices(old=>({...old,[id]:''}));attachmentMap[id]?.forEach(a=>onDetach?.(a.id));setAttachmentMap(old=>({...old,[id]:[]}));}
    newConversation.finish();setDiscardDraft(false);
  }
  function newGroup(project = currentProject?.id || '') { setEditingGroup(null); setGroupProject(project); setGroupOpen(true); }
  async function chooseProjectFolder(bind_id='') {
    if(!real){setProjectForm(bind_id?projects.find(p=>p.id===bind_id)!:{name:''});return;}
    if(projectPicking||live?.busy)return;
    if(!backend||backend.scope==='collaboration'){setStorageError('请在桌面客户端打开本地文件夹。');return;}
    setProjectPicking(true);setStorageError('');
    try{const bound=projects.find(p=>p.id===bind_id)?.primary;if(bound){if(await sendAction({type:'open_folder_project',primary:bound,bind_id})){setProjectForm(null);onProject?.();}return;}const result=await rpc<{paths:string[]}>(backend,'project_workspace',{operation:'pick',request_id:crypto.randomUUID().replace(/-/g,'')},{timeoutMs:120000});if(result.paths?.[0]&&await sendAction({type:'open_folder_project',primary:result.paths[0],bind_id})){setProjectForm(null);setListView('history');}}
    catch(error){setStorageError(String(error));}finally{setProjectPicking(false);}
  }
  function runCard(run: DemoRun) {
    const owner = member(run.agent)!;
    const displayState=run.chain?.state || run.delivery_state || run.state;
    const chainLabel=run.chain ? ({running:'执行中',succeeded:'已完成',failed:'未完成',needs_input:'待补充',cancelled:'已停止'} as Record<string,string>)[run.chain.state] : run.delivery_state ? ({running:'协作进行中',succeeded:'协作已交付',failed:'协作未完成'} as const)[run.delivery_state] : '';
    return <article key={run.id} id={`cp-run-${run.id}`} className={`cp-task ${focusedRun === run.id ? "cp-task-focused" : ""}`} data-demo-run={run.id}>
      <div className="cp-task-meta"><span className={`cp-state cp-state-${displayState}`}><i />{real ? chainLabel || ({ running: "执行中", succeeded: "已完成", cancelled: "已停止" } as Record<string, string>)[run.state] || RUN_LABELS[run.state] : RUN_LABELS[run.state]}</span><span>{owner.name}</span></div>
      <h3>{run.title}</h3>
      {!real && <div className="cp-progress" aria-label={`演示进度 ${Math.min(100, Math.round(run.step / 3 * 100))}%`}><span style={{ width: `${run.step / 3 * 100}%` }} /></div>}
      <p className="cp-task-step">{real ? run.clarification || run.chain?.error || run.error || (run.state === "waiting" ? "前序成果就绪后接续执行" : run.status || "等待调度") : run.state === "waiting" ? "前序成果就绪后接续执行" : run.state === "cancelled" ? "进度已保留" : run.state === "succeeded" ? "成果结构样例已就绪" : STEP_LABELS[Math.min(run.step, 2)]}</p>
      {<p className="cp-run-usage">{run.model} · {run.chain?.calls ?? run.calls ?? 0} 次调用（整链）<br />{(run.input_tokens || run.output_tokens) ? `输入 ${run.input_tokens || 0} / 输出 ${run.output_tokens || 0} tokens` : "暂无用量回执"}</p>}
      {real && run.feedback_check && <DeliveryFeedback report={run.feedback_check}/>}
      {real && <TaskBudgetView budget={run.task_budget}/>}
      {real && run.delivery_check && !run.feedback_check && <p className={run.delivery_check.ok ? 'cp-muted' : 'cp-error'}>交付检查 · {run.delivery_check.ok ? '已提交成果，请审阅' : `尚未完成：${[...(run.delivery_check.missing || []),...(run.delivery_check.missing_sources || [])].join('、') || '待修订'}`}</p>}
      {real && run.citation_check && <details><summary>来源核验 · {run.citation_check.ok ? "链接匹配" : "需修订"} · 已读 {run.citation_check.pages_read} 页</summary>
        <p className="cp-muted">仅核对来源读取记录；事实与结论仍需审阅。</p>
        <ul>{run.citation_check.matched.map((source, index) => <li key={index}><a href={source.url} target="_blank" rel="noreferrer">{source.url}</a> · {source.level === "service_preview" ? "启动时预览已验证" : source.level === "checkout" ? "已核对拉取提交" : source.level === "body" ? source.truncated ? "正文节选" : "已读正文" : "仅搜索摘要"}{source.read_ranges?.length?` · 已读 ${source.read_ranges.map(([a,b])=>`${a}–${b}`).join("、")} / ${source.characters} 字符`:""}</li>)}</ul>
        {!!run.citation_check.unverified.length && <p role="alert" className="cp-error">未取得的引用：{run.citation_check.unverified.join("、")}</p>}
      </details>}
      {!!run.updates.length && <p className="cp-update-receipt">补充 {run.updates.length} 条 · 已应用 {run.applied} 条{run.applied < run.updates.length && runActive(run) ? " · 等待下一步" : ""}</p>}
      <div className="cp-task-actions">{(runActive(run)||run.state==='needs_input') ? <>{run.kind !== "chat" && <button onClick={() => supplement(run)} disabled={run.state === "cancelling"}>{run.state==='needs_input'?'补充目标':'补充要求'}</button>}<button onClick={() => dispatch({ type: "cancel", id: run.id })} disabled={run.state === "cancelling"}>停止</button></> :
        run.artifact ? <button onClick={() => setArtifactId(run.id)}>查看成果 ↗</button> : <span className="cp-muted">未生成成果</span>}</div>
      {real && run.kind === "plan" && state.runs.some(child => child.parent === run.id && runActive(child)) && <button className="cp-text-button" onClick={() => dispatch({ type: "cancel", id: run.id })}>停止整条协作链</button>}
      <ExecutionTree run={run} runs={runs} agents={state.agents} />
      {!!run.project_receipts?.length && <details><summary>项目操作回执 · {run.project_receipts.length}</summary>{run.project_receipts.map((receipt,index)=><div key={`${receipt.call_id}:${index}`}>
        <p>{receipt.operation.stage || receipt.tool} · {receipt.ok ? "操作已确认" : "未完成"}{receipt.error ? ` · ${receipt.error}` : ""}</p>
        {receipt.environment.id && <button className="cp-text-button" onClick={()=>openProjectRuntime(receipt.environment.id,receipt.log_id || receipt.service.log_id || receipt.operation.id)}>打开项目、预览与日志</button>}
      </div>)}</details>}
      {!!run.changes?.length && <details><summary>文件改动 · {run.changes.length}</summary>{run.changes.map((change,i) => <div key={i}><strong>{change.path}</strong><pre>{change.diff}</pre></div>)}</details>}
      {!!run.checks?.length && <details><summary>实际命令回执 · {run.checks.length}</summary>{run.checks.map((check,i) => <div key={i}><code>{check.command}</code><p>{check.state} · 退出码 {check.exit_code ?? "无"}{check.stale ? " · 此后文件有修改，需要重新验证" : ""}</p>{check.diff_limited && <p>文本差异采集达到上限，部分文件未覆盖。</p>}<pre>{check.output}</pre></div>)}</details>}
    </article>;
  }

  return <section className={`cp-root ${real ? "cp-live" : "cp-demo"} ${standalone ? "cp-standalone" : "cp-embedded"}`} aria-label={real ? "协作聊天" : "协作聊天原型"} data-chat-prototype>
    {!real && <div className="cp-preview-strip"><span><b>交互原型</b> 本地模拟 · 不调用模型或外部工具</span><span className="cp-preview-actions">
      {standalone && <button onClick={() => { const next = !dark; setDark(next); document.documentElement.classList.toggle("dark", next); document.documentElement.classList.toggle("light", !next); }} aria-label="切换原型主题">{dark ? "浅色" : "深色"}</button>}
      <a href="/client.html?preview=chat&live=1">连接真实伙伴 ↗</a><button onClick={() => setResetOpen(true)}>重置演示</button></span></div>}
    {standalone && <header className="tebb-standalone-toolbar"><span>伙伴聊天 · {real ? '本地连接' : '交互演示'}</span><div><button onClick={() => { const next = !dark; setDark(next); document.documentElement.classList.toggle('dark', next); document.documentElement.classList.toggle('light', !next); }}>切换主题</button></div></header>}
    <div className={`cp-layout ${inspector ? "has-inspector" : ""}`}>
      {standalone && <WorkspaceRail developer={developer} onToggle={toggleDeveloper}><RailButton icon="chat" label="伙伴聊天" active onClick={() => {}} /></WorkspaceRail>}
      <ChatSidebar state={state} selected={state.selected} draftActive={!!editingDraft} mobile={mobileList} listView={listView} onListView={setListView}
        collapsed={collapsedProjects} onToggle={id=>setCollapsedProjects(old=>({...old,[id]:!old[id]}))} onNew={()=>beginNewConversation()} onSelect={select}
        onMenu={openMenu} onKeys={menuKeys} onProject={setProjectForm} onFolder={()=>void chooseProjectFolder()} onMigrate={()=>setMigrationOpen(true)}
        onAgent={setEditingAgent} onPick={agent=>{beginNewConversation(agent.id);setListView('history');}} onAdd={addAgent}
        pendingCount={pendingCount} onInbox={()=>{setInspector(true);setTab('inbox');setMobileList(false);}} busy={projectPicking||!!live?.busy} real={real}/>

      <div className="cp-chat">
        <header className="cp-chat-header"><div className="cp-chat-title"><button className="cp-icon-button cp-mobile-menu" aria-label="切换会话列表" aria-expanded={mobileList} onClick={() => setMobileList(!mobileList)}>☰</button><Avatar group={conversation.kind === "group"} agent={member(conversation.coordinator)} />
          <div><h1>{conversation.title}</h1><span className="cp-chat-subtitle">{currentProject && <span className="cp-project-breadcrumb" title={currentProject.name}><AppIcon name="folder" />{currentProject.name}<span aria-hidden="true"> / </span></span>}<span>{conversation.kind === "group" ? `你与 ${members.length} 位 Agent · ${member(conversation.coordinator)?.name} 协调` : member(conversation.coordinator)?.role}</span></span></div></div>
          <div className="cp-context-actions">{real && !editingDraft && onProject && <button className="cp-button cp-project-button" aria-label="工作目录与权限" title={state.workspace?.primary || "打开项目文件夹与配置权限"} onClick={onProject}><AppIcon name="folder"/><span>{state.workspace?.primary?.split(/[\\/]/).pop() || "文件夹"}</span></button>}<button className="cp-button" aria-label="当前任务" aria-expanded={inspector && tab==='tasks'} onClick={() => { setInspector(!(inspector && tab==='tasks')); setTab('tasks'); }} title="任务"><AppIcon name="tasks" /></button><button className="cp-button" aria-label="聊天伙伴" onClick={() => { setInspector(!(inspector && tab==='members')); setTab('members'); }} aria-expanded={inspector && tab==='members'} title="伙伴"><AppIcon name="members" /></button><button className="cp-button" aria-label="待你处理" onClick={() => { setInspector(!(inspector && tab==='inbox')); setTab('inbox'); }} aria-expanded={inspector && tab==='inbox'} title="待办"><AppIcon name="inbox" />{pendingCount > 0 && <small className="cp-icon-count">{pendingCount}</small>}</button></div></header>
        {editingDraft&&<NewConversationSetup draft={editingDraft} agents={state.agents} projects={projects} busy={!!live?.busy}
          onChange={newConversation.change} onGroup={()=>newGroup(editingDraft.project_id)} onHide={()=>newConversation.hide()} onDiscard={()=>setDiscardDraft(true)}/>}
        {conversation.kind === "group" && <details className="cp-goal"><summary><span>共同目标</span><span>{conversation.goal || "为这个群确定一个目标，开始协作。"}</span></summary><p>{conversation.goal || "可在伙伴侧栏中调整协作群目标。"}</p></details>}
        <div className="cp-message-list" ref={messageList} onScroll={event=>{const el=event.currentTarget;following.current=el.scrollHeight-el.scrollTop-el.clientHeight<48;}} aria-label={real ? "协作聊天记录" : "原型聊天记录"}>
          {!messages.length && <div className="cp-empty-chat"><Avatar agent={member(conversation.coordinator)} /><h2>从一句话开始</h2><p>直接聊天，或者将目标交给 Agent。<br />私聊与协作群分别保留各自的消息。</p><button className="cp-button" disabled={draftLocked} onClick={() => { setDraft("帮我整理这周的研究方向"); setMode("auto"); input.current?.focus(); }}>试试交给 Agent ↗</button></div>}
          {messages.map(item => item.kind === "notice" ? <p key={item.id} className="cp-system-message">{item.text}</p> : <article key={item.id} className={`cp-message ${item.sender === "user" ? "cp-message-user" : ""}`}>
            {item.sender === "user" ? <span className="cp-user-avatar">你</span> : <Avatar agent={member(item.sender)} />}
            <div className="cp-message-content"><div className="cp-message-author"><strong>{item.sender === "user" ? "你" : member(item.sender)?.name}</strong>{item.kind === "result" && <span className="cp-result-tag">成果</span>}</div>
              {item.sender !== 'user' && <>{runs.find(r=>r.id===item.run)?.state==='needs_input'&&<ClarificationCard key={item.run+':'+runs.find(r=>r.id===item.run)?.clarification_version} run={runs.find(r=>r.id===item.run)!} busy={!!live?.busy} onAction={action=>sendAction(action)} onText={()=>void supplement(runs.find(r=>r.id===item.run)!)} />}{runs.find(r=>r.id===item.run)&&<ExecutionTree run={runs.find(r=>r.id===item.run)!} runs={runs} agents={state.agents}/>}{onCalendar&&hasCalendarContext(runs.find(r=>r.id===item.run))&&<button type="button" className="cp-message-reference" onClick={onCalendar}>查看相关日程 →</button>}</>}
              {item.sender!=='user' && <DraftNotice run={runs.find(r=>r.id===item.run)}/>}
              {(item.sender==='user'||runs.find(r=>r.id===item.run)?.state!=='needs_input') && (item.text || runs.find(r=>r.id===item.run)?.chain?.answer || !real) && <div className="cp-markdown"><StreamText text={executionMessage(item,runs)} active={!!item.run && runs.some(r=>r.id===item.run && runActive(r))} externalLinks={standalone} /></div>}
              {item.sender !== 'user' && item.run && <div className="cp-message-footer">{(developer || runs.find(run=>run.id===item.run)?.state==='needs_input' || !["chat","route","compress"].includes(runs.find(run => run.id===item.run)?.kind || "")) && <button className="cp-message-reference" onClick={() => { setFocusedRun(item.run!); setInspector(true); setTab("tasks"); }}>↗ 关联任务</button>}
              {real && <small className="cp-message-usage" title="本条消息的执行模型与输入／输出 token 用量">{runs.find(r => r.id===item.run)?.model} · ↑ {runs.find(r => r.id===item.run)?.input_tokens || 0} / ↓ {runs.find(r => r.id===item.run)?.output_tokens || 0}</small>}
              {real && backend && backend.scope!=='collaboration' && item.sender!=='user' && item.run && runs.some(r=>r.id===item.run && r.state==='succeeded' && ['task','chat','route'].includes(r.kind || '') && (r.kind==='task'||!runs.some(child=>child.parent===r.id))) && <button className="cp-message-reference" title={currentProject?'保存到知识库并共享为本项目资料':'保存到知识库'} onClick={async()=>{try { const result=await rpc<{ok:boolean;error?:string}>(backend,'save_collaboration_note',{run_id:item.run}); if(!result.ok) throw new Error(result.error || '收藏失败'); setStorageError(currentProject?`已收藏到知识库，并加入「${currentProject.name}」项目资料。`:'已收藏到知识库的「伙伴成果」笔记，可继续作为资料使用。'); window.dispatchEvent(new Event('tebb-notebooks-changed')); } catch(error){setStorageError(String(error));} }}>{currentProject?'收藏到项目笔记':'收藏到笔记'}</button>}
              {item.kind === "result" && runs.find(run => run.id === item.run)?.artifact && <button className="cp-artifact-link" onClick={() => setArtifactId(item.run!)} title="打开完整成果文档"><span>▤</span><strong>{real ? "成果文档" : "成果文档 · 演示"}</strong><span>↗</span></button>}</div>}
            </div></article>)}
          <div ref={end} />
        </div>
        <div className="cp-compose-area">
          {(active.length > 0 || !real) && <div className="cp-live-bar"><button type="button" className="cp-activity-link" aria-label="查看运行任务" onClick={() => { setInspector(true); setTab('tasks'); }}><i className={active.length ? "cp-live-dot" : "cp-idle-dot"} />{real ? `本会话 ${runs.filter(runActive).length} 项运行中${active.length > runs.filter(runActive).length ? ` · 其他会话 ${active.length - runs.filter(runActive).length} 项` : ''}` : active.length ? `${active.length} 项后台演示 · ${automatic ? "自动推进中" : "已暂停"}` : "没有进行中的后台演示"}</button>
            {!real && <div><button disabled={!active.length} onClick={() => setAutomatic(!automatic)}>{automatic ? "暂停" : "自动演示"}</button><button disabled={!active.length} onClick={() => dispatch({ type: "advance" })}>推进一步 →</button></div>}</div>}
          {selectedRun && <div className="cp-reply-context"><span>补充给：<strong>{selectedRun.title}</strong>{!runActive(selectedRun) && " · 已结束，请取消引用"}</span><button aria-label="取消补充要求" onClick={() => setUpdateRun("")}>×</button></div>}
          <form className="cp-composer" onSubmit={event => { event.preventDefault(); submit(); }}>
            <textarea ref={input} value={draft} readOnly={draftLocked} maxLength={4000} aria-label={real ? "协作消息" : "原型消息"} rows={2} placeholder={updateRun ? "补充这项任务的要求…" : "发消息，@ 伙伴，/ 选择技能…"}
              onChange={event => changeDraft(event.target.value, event.target.selectionStart ?? event.target.value.length)} onSelect={event => { const position = event.currentTarget.selectionStart; setCursor(position); const match = /(?:^|\s)([@/])([^\s@/]*)$/.exec(draft.slice(0,position)); if (!match) setPicker(""); }} aria-autocomplete="list" aria-controls={picker ? "cp-composer-options" : undefined} aria-expanded={!!picker} aria-activedescendant={picker && options.length ? `cp-option-${pickerIndex}` : undefined} onKeyDown={event => {
                if (event.nativeEvent.isComposing || event.keyCode === 229) return;
                if (picker && event.key === 'Escape') { event.preventDefault(); setPicker(''); return; }
                if (picker && ['ArrowDown','ArrowUp'].includes(event.key)) { event.preventDefault(); setPickerIndex(old => (old + (event.key==='ArrowDown' ? 1 : options.length-1)) % Math.max(1,options.length)); return; }
                if (picker && event.key === 'Enter') { event.preventDefault(); if (options.length) chooseOption(pickerIndex); return; }
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); submit(); } }} />
            {picker && <ComposerPicker anchor={input} className="cp-composer-picker" id="cp-composer-options" label={picker==='members' ? '选择成员' : '选择技能'} onClose={()=>setPicker('')}>{options.map((option,i) => <button type="button" role="option" id={`cp-option-${i}`} key={option.id} aria-selected={pickerIndex===i} onMouseDown={event => event.preventDefault()} onClick={() => chooseOption(i)}><strong>{option.name}</strong><small>{option.detail}</small></button>)}{!options.length && <p>没有匹配项{picker==='skills' ? '，技能在桌面客户端加载' : ''}。按 Esc 关闭。</p>}</ComposerPicker>}
            <div className="cp-selection-chips">{skills.map(skill => <button type="button" disabled={draftLocked} key={skill} onClick={() => setSelectedSkills(old => ({ ...old, [conversation.id]: skills.filter(s => s!==skill) }))}>/{skill} ×</button>)}{attachments.map(file => <button type="button" disabled={draftLocked} key={file.id} onClick={() => removeAttachment(file.id)}>{file.name} ×</button>)}</div>
            <div className="cp-composer-bottom"><div className="cp-composer-controls"><button type="button" aria-label="添加附件" disabled={!onAttach||draftLocked} onClick={onAttach}>＋ 附件</button><button type="button" disabled={draftLocked} onClick={() => recipient && setEditingAgent(recipient)}>工具</button><button type="button" aria-label="选择技能" disabled={draftLocked} onClick={() => { changeDraft(draft+(draft && !draft.endsWith(' ') ? ' ' : '')+'/'); input.current?.focus(); }}>/ Skill</button><button type="button" aria-label="选择成员" disabled={draftLocked} onClick={() => { changeDraft(draft+(draft && !draft.endsWith(' ') ? ' ' : '')+'@'); input.current?.focus(); }}>@</button>
              <label><select disabled={draftLocked} aria-label="本轮模型" title="本轮模型" value={modelPick} onChange={e => setModelPick(e.target.value)}><option value="">{recipientModel}（默认）</option>{state.models?.map(model => <option key={model.id} value={model.id}>{model.model}</option>)}</select></label></div>
              <button type="submit" className="cp-send" disabled={live?.busy || !draft.trim() || (!!selectedRun && !runActive(selectedRun)&&selectedRun.state!=='needs_input')}>{draftLocked?'重试原发送':updateRun ? '提交补充' : '发送'}<span>↑</span></button></div>
            <ContextDebug key={conversation.id} info={backend} conversation={editingDraft?'':conversation.id} draft={draft}>{developer&&<div className="cp-parameter-debug"><label>推理力度<select disabled={draftLocked} value={sampling.reasoning_effort} onChange={e => setSampling({ ...sampling, reasoning_effort:e.target.value })}>{['low','high','max'].map(v => <option key={v}>{v}</option>)}</select></label><label>温度<input disabled={draftLocked} type="number" min="0" max="1" step="0.1" value={sampling.temperature} onChange={e => setSampling({ ...sampling, temperature:Number(e.target.value) })} /></label><label>top_p<input disabled={draftLocked} type="number" min="0.01" max="1" step="0.01" value={sampling.top_p} onChange={e => setSampling({ ...sampling, top_p:Number(e.target.value) })} /></label><button type="button" disabled={!!editingDraft} onClick={() => dispatch({ type:'compress' })}>整理当前会话上下文</button></div>}</ContextDebug>

          </form>
          <div className="cp-composer-hint"><span>Enter 发送 · Shift + Enter 换行</span>{!real && <span>演示数据仅存于此设备</span>}</div>

          {(state.notice || storageError || live?.error) && <p className="cp-notice" role="status">{live?.error || storageError || state.notice}</p>}
        </div>
      </div>

      <aside className={`cp-inspector ${inspector ? "is-open" : ""}`} aria-hidden={!inspector} {...(!inspector ? { inert: "" } : {})} aria-label="协作详情"><header><span>{tab==='tasks' ? '任务' : tab==='members' ? '伙伴' : '待办'}</span><button className="cp-icon-button cp-inspector-close" onClick={() => setInspector(false)} aria-label="关闭协作详情">×</button></header>
        <div className="cp-tabs" role="tablist" aria-label="协作详情分类">{([['tasks', '任务'], ['members', '成员'], ['inbox', `待办${pendingCount ? ` ${pendingCount}` : ""}`]] as const).map(([key, title]) =>
          <button key={key} role="tab" id={`cp-tab-${key}`} aria-selected={tab === key} aria-controls="cp-detail-panel" tabIndex={tab === key ? 0 : -1} onClick={() => setTab(key)} onKeyDown={event => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            event.preventDefault(); const keys = ["tasks", "members", "inbox"] as const;
            const next = event.key === "Home" ? 0 : event.key === "End" ? 2 : (keys.indexOf(tab) + (event.key === "ArrowRight" ? 1 : 2)) % 3;
            setTab(keys[next]); document.getElementById(`cp-tab-${keys[next]}`)?.focus();
          }}>{title}</button>)}</div>
        <div className="cp-inspector-body" id="cp-detail-panel" role="tabpanel" aria-labelledby={`cp-tab-${tab}`}>
          {tab === "tasks" ? <><div className="cp-section-heading"><span>当前会话</span><span>{runs.filter(runActive).length} 项进行中</span></div>{visibleRuns.map(run => runCard(run))}
            {!visibleRuns.length && <div className="cp-empty"><span>↗</span><h3>给想法一个去处</h3><p>直接描述想完成的事，执行请求会自动成为后台任务。聊天不会被任务占住。</p></div>}
            <div className="cp-side-note">{real ? "最多2项长任务与1条聊天同时执行。只有用户提交能创建协作链，成员消息不会互相唤醒。" : "委派与交接会留下记录。演示不会调用真实工具或消耗模型额度。"}</div></> : tab === "members" ? <><div className="cp-section-heading"><span>你 ＋ {members.length} 位 Agent</span></div>
              <div className="cp-member-card"><span className="cp-user-avatar">你</span><div><strong>你</strong><p>设定方向，做出决定</p></div><small>所有者</small></div>
              {members.map(agent => <div className="cp-member-card" key={agent.id}><Avatar agent={agent} /><div><strong>{agent.name}{agent.id === conversation.coordinator && <small>协调者</small>}</strong><p>{agent.role}</p><button onClick={() => setEditingAgent(agent)}>认识与配置 ↗</button></div></div>)}
              {conversation.kind === "group" && <button className="cp-button cp-full" onClick={() => { setEditingGroup(conversation); setGroupOpen(true); }}>邀请或调整伙伴</button>}<button className="cp-button cp-full" onClick={addAgent}>＋ 创建 Agent</button><div className="cp-side-note">群里共享目标和成果。伙伴各有性格和擅长的事，你可以随时调整。</div></> : <><div className="cp-section-heading"><span>全部会话 · 待你处理</span><span>{pendingCount} 项</span></div>
                {!pendingCount && <div className="cp-empty"><span>◇</span><h3>现在无需你介入</h3><p>{real ? "所有会话中需要补充信息的任务会显示在这里。" : "在成果文档中选择「演示交付确认」，体验任务如何向你请求决定。"}</p></div>}
                {waitingInputs.map(run=><div key={run.id}><p className="cp-muted">{state.conversations.find(c=>c.id===run.conversation)?.title||'原对话'} · {run.title}</p><ClarificationCard key={run.id+':'+run.clarification_version} run={run} busy={!!live?.busy} onAction={action=>sendAction(action)} onText={()=>void supplement(run)} onOpen={()=>select(run.conversation)}/></div>)}
                {pending.map(request => { const run = state.runs.find(item => item.id === request.run)!; return <article className="cp-request" key={request.id}><span className="cp-eyebrow">交付确认 · 演示</span><h3>{run.title}</h3><p>模拟确认将成果发送到飞书。点击后仅记录演示决定，没有实际发送。</p><div><button className="cp-button" onClick={() => dispatch({ type: "decide", id: request.id, approved: false })}>拒绝</button><button className="cp-button cp-primary" onClick={() => dispatch({ type: "decide", id: request.id, approved: true })}>确认演示</button></div><button className="cp-text-button" onClick={() => { select(request.conversation); setArtifactId(run.id); }}>查看原任务成果 ↗</button></article>; })}</>}
        </div></aside>
    </div>
    {menu && <div className="cp-menu-dismiss" onMouseDown={() => setMenu(null)} onContextMenu={event => { event.preventDefault(); setMenu(null); }}>
      <div className="cp-conversation-menu" role="menu" aria-label="会话操作" ref={menuBox} style={{ left: menu.x, top: menu.y }} onMouseDown={event => event.stopPropagation()} onKeyDown={event => {
        if (event.key === "Escape" || event.key === "Tab") { if (event.key === "Escape") event.preventDefault(); setMenu(null); }
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
          event.preventDefault(); const buttons = Array.from(menuBox.current?.querySelectorAll<HTMLButtonElement>("button") || []);
          const current = buttons.indexOf(document.activeElement as HTMLButtonElement);
          buttons[event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (current + (event.key === "ArrowDown" ? 1 : buttons.length - 1)) % buttons.length]?.focus();
        }
      }}>
        {state.conversations.find(c => c.id === menu.id)?.kind === "group" && <button role="menuitem" onClick={() => { setRenaming(menu.id); setRenameText(state.conversations.find(c => c.id === menu.id)!.title); setMenu(null); }}>重命名群聊</button>}
        <button role="menuitem" onClick={() => { setMovingConversation(menu.id); setMoveProject(state.conversations.find(c=>c.id===menu.id)?.project_id || ''); setMenu(null); }}>移动到项目</button>
        <button role="menuitem" onClick={() => { setDeleting(menu.id); setMenu(null); }}>删除对话</button>
      </div>
    </div>}
    {projectForm && <Modal title={projectForm.id ? '项目设置' : '新建项目'} onClose={() => setProjectForm(null)}><form className="cp-form" onSubmit={async event => { event.preventDefault(); if (!projectForm.name.trim() || projectNameError) return; if (await sendAction(projectForm.id ? {type:'rename_project',id:projectForm.id,name:projectForm.name,instructions:projectForm.instructions||''} : {type:'create_project',name:projectForm.name})) {setProjectForm(null);setListView('history');} }}>
      <label>项目名称<input aria-label="项目名称" value={projectForm.name} maxLength={40} placeholder="例如：Atlas 开发、AI 研究" required onChange={event => setProjectForm({...projectForm,name:event.target.value})} /></label>
      {projectForm.id&&<><p className="cp-muted cp-project-path">{projects.find(p=>p.id===projectForm.id)?.primary||'尚未绑定本地文件夹'}</p><button type="button" className="cp-button" disabled={projectPicking} onClick={()=>void chooseProjectFolder(projectForm.id)}>{projects.find(p=>p.id===projectForm.id)?.primary?'打开对应文件夹':'绑定文件夹'}</button><label>项目说明<textarea aria-label="项目说明" rows={3} maxLength={4000} value={projectForm.instructions||''} onChange={event=>setProjectForm({...projectForm,instructions:event.target.value})} placeholder="项目目标、约定及希望伙伴遵循的要求"/></label><p className="cp-muted">本项目的文件与明确收藏的成果可供后续聊天使用，各段对话独立保留历史。主目录、只读参考目录及权限在顶部文件夹中配置。</p>{projects.find(p=>p.id===projectForm.id)?.sources?.map(source=><div className="cp-project-source" key={source.id}><span>{source.title}{source.truncated?' · 内容节选':''}</span><button type="button" onClick={()=>void sendAction({type:'project_details',id:projectForm.id!,instructions:projectForm.instructions||'',remove_source:source.id})}>移除共享</button></div>)}</>}
      {(projectNameError || live?.error) && <p role="alert" className="cp-error">{projectNameError || live?.error}</p>}
      <footer>{projectForm.id && <><button type="button" className="cp-text-button cp-danger" onClick={()=>{setDeletingProject(projectForm.id!);setProjectForm(null);}}>删除项目</button></>}<button type="button" className="cp-button" onClick={()=>setProjectForm(null)}>取消</button><button className="cp-button cp-primary" disabled={live?.busy || !projectForm.name.trim() || !!projectNameError}>{projectForm.id ? '保存项目' : '创建项目'}</button></footer>
    </form></Modal>}
    {migrationOpen&&<Modal title="按工作目录整理已有对话" onClose={()=>setMigrationOpen(false)}><ProjectMigration backend={backend} busy={!!live?.busy} onClose={()=>setMigrationOpen(false)} onApply={(token,ids)=>sendAction({type:'migrate_projects',token,ids})}/></Modal>}
    {deletingProject && <Modal title="删除项目？" onClose={()=>setDeletingProject('')}><div className="cp-form"><p>删除「{projects.find(p=>p.id===deletingProject)?.name}」后，其中所有对话会移至「未归类」。聊天记录、成果和正在执行的任务都会保留。</p>{live?.error && <p role="alert" className="cp-error">{live.error}</p>}<footer><button className="cp-button" onClick={()=>setDeletingProject('')}>取消</button><button className="cp-button" disabled={live?.busy} onClick={async()=>{if(await sendAction({type:'delete_project',id:deletingProject}))setDeletingProject('');}}>确认删除项目</button></footer></div></Modal>}
    {movingConversation && <Modal title="移动对话" onClose={()=>setMovingConversation('')}><form className="cp-form" onSubmit={async event=>{event.preventDefault();if(await sendAction({type:'move_conversation',id:movingConversation,project_id:moveProject})){setMovingConversation('');setCollapsedProjects(old=>({...old,[moveProject]:false}));}}}><p>将「{state.conversations.find(c=>c.id===movingConversation)?.title}」移入项目，保留消息、草稿和任务。</p><label>目标项目<select aria-label="目标项目" value={moveProject} onChange={event=>setMoveProject(event.target.value)}><option value="">未归类</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>{!projects.length && <p className="cp-muted">还没有项目，可先在侧栏点击「新建项目」。</p>}{live?.error && <p role="alert" className="cp-error">{live.error}</p>}<footer><button type="button" className="cp-button" onClick={()=>setMovingConversation('')}>取消</button><button className="cp-button cp-primary" disabled={live?.busy}>移动对话</button></footer></form></Modal>}
    {deleting && <Modal title="删除这段对话？" onClose={() => setDeleting("")}><div className="cp-form"><p>删除「{state.conversations.find(c => c.id === deleting)?.title}」的聊天记录和成果。伙伴配置与私有记忆保留；底层任务诊断日志保留。{state.conversations.find(c => c.id===deleting)?.legacy && "旧历史源文件也会保留，此操作仅删除统一工作区中的会话副本。"}</p>
      {state.runs.some(r => r.conversation === deleting && runActive(r)) && <p role="alert" className="cp-error">请先停止此会话中的任务，等待结束后再删除。</p>}
      {live?.error && <p role="alert">{live.error}</p>}<footer><button className="cp-button" onClick={() => setDeleting("")}>取消</button><button className="cp-button" disabled={live?.busy || state.runs.some(r => r.conversation === deleting && runActive(r))} onClick={async () => {
        const id = deleting; if (await sendAction({ type: "delete_conversation", id })) { setDeleting(""); setDrafts(old => { const next = { ...old }; delete next[id]; return next; }); setUpdateRun(""); setTarget(""); setArtifactId(""); }
      }}>确认删除</button></footer></div></Modal>}
    {renaming && <Modal title="重命名群聊" onClose={() => setRenaming("")}><form className="cp-form" onSubmit={async event => { event.preventDefault(); if (await sendAction({ type: "rename_conversation", id: renaming, title: renameText })) setRenaming(""); }}><label>群名称<input value={renameText} maxLength={40} onChange={event => setRenameText(event.target.value)} /></label>{live?.error && <p role="alert">{live.error}</p>}<footer><button className="cp-button cp-primary" disabled={live?.busy || !renameText.trim()}>保存名称</button></footer></form></Modal>}
    {discardDraft&&<Modal title="放弃新对话草稿？" onClose={()=>setDiscardDraft(false)}><p>{newConversation.draft?.attempt?'首条消息结果尚未确认，后台可能已接收。放弃只关闭本地草稿，不取消可能已开始的任务；建议先读取列表核对。':'放弃后清除这份未发送草稿，已有对话保持不变。'}</p><div className="cp-modal-footer"><button className="cp-button" onClick={()=>setDiscardDraft(false)}>保留草稿</button><button className="cp-button" onClick={clearNewDraft}>确认放弃草稿</button></div></Modal>}
    {groupOpen && <GroupForm editing={editingGroup} agents={state.agents} projects={projects} projectId={groupProject} real={real} actionError={live?.error} busy={live?.busy} onClose={() => setGroupOpen(false)} onCreate={async (title, members, goal, project_id) => { if (await sendAction({ type: "group", id: editingGroup?.id, title, members, goal, project_id })) { if(!editingGroup)newConversation.hide();setGroupOpen(false); setTarget(""); setUpdateRun(""); } }} />}
    {editingAgent && <AgentForm developer={developer} agent={editingAgent} agents={state.agents} real={real} models={state.models} actionError={live?.error} busy={live?.busy} onClose={() => setEditingAgent(null)} onSave={async agent => { if (await sendAction({ type: "agent", agent })) setEditingAgent(null); }} />}
    {currentArtifact?.artifact && <Modal title={real ? "成果文档" : "成果文档 · 演示"} onClose={() => setArtifactId("")}><div className="cp-document"><div className="cp-document-label">LOCAL ARTIFACT / {real ? "实际任务成果" : "结构样例"}</div><div className="cp-markdown"><Markdown text={currentArtifact.artifact} externalLinks={standalone} /></div></div>
      <footer className="cp-document-footer"><span>{real ? "成果已保存在本地，未发送到外部服务" : "未生成真实报告，未发送到外部服务"}</span>{!real && <button className="cp-button cp-primary" disabled={state.requests.some(request => request.run === currentArtifact.id)} onClick={() => {
        dispatch({ type: "request", run: currentArtifact.id }); setArtifactId(""); setInspector(true); setTab("inbox");
      }}>{state.requests.some(request => request.run === currentArtifact.id) ? "已处理或已加入待办" : "演示交付确认"}</button>}</footer></Modal>}
    {resetOpen && <Modal title="重置原型演示？" onClose={() => setResetOpen(false)}><div className="cp-form"><p>清除原型中的群、消息、Agent 配置和演示任务，恢复初始场景。真实聊天、记忆和设置不受影响。</p><footer><button className="cp-button" onClick={() => setResetOpen(false)}>取消</button><button className="cp-button cp-primary" onClick={() => {
      dispatch({ type: "reset" }); setAutomatic(false); setDrafts({}); setUpdateRun(""); setTarget(""); setMode("auto"); setResetOpen(false);
    }}>确认重置原型</button></footer></div></Modal>}
  </section>;
}
