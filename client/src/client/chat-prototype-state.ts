import type { ClarificationForm,ClarificationAction } from './clarification-state';
// Local interaction model only. No model, tool, credential, or backend imports.
export type DemoAgent = { id: string; name: string; role: string; mark: string; tone: string; model: string; tools: string[]; memory: string; avatar?: string; personality?: string };
export type DemoProject = { id: string; name: string; primary?: string; instructions?: string; sources?: {id:string;title:string;text:string;truncated?:boolean}[] };
export type DemoConversation = { id: string; title: string; kind: "dm" | "group"; members: string[]; coordinator: string; goal: string; legacy?: boolean; summary?: string; project_id?: string };
export type DemoMessage = { id: string; conversation: string; sender: string; text: string; run?: string; kind?: "result" | "notice" };
export type ChartDraftView={draft_id:string;title:string;stage:string;attempt:number;asset_id?:string;spec?:unknown;history:{stage:string;attempt:number;error?:string;issues?:{path:string;message:string;hint?:string}[]}[]};
export type DemoRun = { id: string; conversation: string; agent: string; title: string; state: "running" | "waiting" | "succeeded" | "cancelled" | "pending" | "queued" | "cancelling" | "failed" | "timed_out" | "interrupted" | "needs_input";
  clarification_version?:string;clarification_form?:ClarificationForm;options_generation?:{state:string;error?:string;calls?:number};require_execution?:boolean;clarification?:string;created?:number;finished?:number;phases?:{kind:string;calls:number;started:number;ended:number}[];chain?:{state:DemoRun['state'];calls:number;tool_calls:number;elapsed_ms:number|null;error:string;answer:string;children:string[]};
  step: number; dependency?: string; dependencies?: string[]; delivery_state?: "running" | "succeeded" | "failed"; delivery_check?: { ok: boolean; missing?: string[]; missing_sources?: string[] }; updates: string[]; applied: number; events: string[]; artifact?: string;
  kind?: string; parent?: string; answer?: string; answer_pending?: boolean; error?: string; status?: string; calls?: number; model?: string; input_tokens?: number; output_tokens?: number;
  chart_builds?:Record<string,ChartDraftView>;
  changes?: { path: string; diff: string }[]; checks?: { command: string; state: string; exit_code: number | null; output: string; stale?: boolean; diff_limited?: boolean }[];
  citation_check?: { ok: boolean; pages_read: number; matched: { url: string; level: string; truncated: boolean;read_ranges?:number[][];characters?:number }[]; unverified: string[] } };
export type DemoRequest = { id: string; conversation: string; run: string; state: "pending" | "approved" | "rejected" };
export type DemoState = { version: 1; next: number; selected: string; projects?: DemoProject[]; agents: DemoAgent[]; conversations: DemoConversation[]; messages: DemoMessage[];
  runs: DemoRun[]; requests: DemoRequest[]; notice: string; companionVersion?: number; revision?: number; active_model?: string; workspace?: { primary: string; project_id?:string; revision?:string; references?:string[]; edit?:boolean; commands?:boolean }; skills?: { id: string; description: string }[]; models?: { id: string; model: string }[] };
export type DemoAction = ClarificationAction
  | { type: "select"; id: string }
  | { type: "send"; conversation?:string;text: string; target: string; mode: "message" | "task" | "auto"; run?: string; skills?: string[]; attachments?: string[]; model_id?: string; sampling?: { reasoning_effort: string; temperature: number; top_p: number } }
  | { type: "create_project"; name: string }
  | { type: "rename_project"; id: string; name: string; instructions?: string }
  | { type: "open_folder_project"; primary: string; bind_id?: string }
  | { type: "project_details"; id: string; instructions: string; remove_source?: string }
  | { type: "migrate_projects"; token: string; ids: string[] }
  | { type: "delete_project"; id: string }
  | { type: "move_conversation"; id: string; project_id: string }
  | { type: "open_dm"; agent_id: string; project_id?: string }
  | { type: "delete_conversation"; id: string }
  | { type: "rename_conversation"; id: string; title: string }
  | { type: "new_chat"; agent_id?: string; project_id?: string }
  | { type: "add_coder" }
  | { type: "compress" }
  | { type: "advance" }
  | { type: "cancel"; id: string }
  | { type: "group"; id?: string; title: string; members: string[]; goal: string; project_id?: string }
  | { type: "agent"; agent: DemoAgent }
  | { type: "request"; run: string }
  | { type: "decide"; id: string; approved: boolean }
  | { type: "reset" };

export const DEMO_STORAGE = "tebb.chat-prototype.v1";
export const runActive = (run: DemoRun) => ["pending", "queued", "running", "waiting", "cancelling"].includes(run.state);
export const RUN_LABELS: Record<DemoRun["state"], string> = { running: "模拟执行中", waiting: "等待前序成果", succeeded: "演示完成", cancelled: "已停止演示", pending: "待执行", queued: "排队中", cancelling: "正在停止", failed: "失败", timed_out: "超时", interrupted: "运行中断",needs_input:'待补充' };
export const STEP_LABELS = ["整理任务与资料范围", "核对资料并提炼要点", "整理成果与交接"];

export function initialDemo(): DemoState {
  return {
    version: 1, companionVersion: 1, next: 20, selected: "group-research", projects: [], notice: "", requests: [],
    agents: [
      { id: "tebb", name: "tebb · 通用助手", avatar: "spark", personality: "温暖爽快，擅长把想法变成计划。", role: "协调目标、拆分工作、汇总成果", mark: "t", tone: "gold", model: "通用模型 · 演示", tools: ["文件"], memory: "演示偏好：先交付结论，再展开依据。" },
      { id: "research", name: "研究助手", avatar: "fox", personality: "好奇又敏锐，追问为什么，也坦率承认未知。", role: "查找一手来源，比较技术方案", mark: "R", tone: "sage", model: "研究模型 · 演示", tools: ["联网搜索", "文件"], memory: "演示偏好：比较项目时列出许可证和部署门槛。" },
      { id: "writer", name: "写作助手", avatar: "cat", personality: "细腻有条理，喜欢把复杂的事讲清楚，有一点冷幽默。", role: "整理结构，将资料写成可交付文档", mark: "W", tone: "lavender", model: "写作模型 · 演示", tools: ["文件", "飞书"], memory: "演示偏好：英文技术名词旁保留中文解释。" },
    ],
    conversations: [
      { id: "group-research", title: "AI 研究室", kind: "group", members: ["tebb", "research", "writer"], coordinator: "tebb", goal: "一起研究开源 Agent，把结论整理成有依据的报告。" },
      ...["tebb", "research", "writer"].map(id => ({ id: `dm-${id}`, title: id === "tebb" ? "tebb · 通用助手" : id === "research" ? "研究助手" : "写作助手", kind: "dm" as const, members: [id], coordinator: id, goal: "独立会话，记忆按 Agent 展示。" })),
    ],
    messages: [
      { id: "m1", conversation: "group-research", sender: "notice", text: "你创建了 AI 研究室，邀请tebb · 通用助手、研究助手和写作助手加入。所有内容均为本地演示。", kind: "notice" },
      { id: "m2", conversation: "group-research", sender: "user", text: "比较三个开源 Agent 项目，帮我看看哪个更适合自部署。", run: "r1" },
      { id: "m3", conversation: "group-research", sender: "tebb", text: "我来协调。研究助手先整理比较依据，写作助手收到资料后生成报告。你可以继续聊天，也可以给任务补充要求。", run: "r1" },
    ],
    runs: [
      { id: "r1", conversation: "group-research", agent: "research", title: "比较开源 Agent 的自部署方案", state: "running", step: 0, updates: [], applied: 0, events: ["tebb · 通用助手 → 研究助手：整理项目比较依据（模拟委派）"] },
      { id: "r2", conversation: "group-research", agent: "writer", title: "整理自部署比较报告", state: "waiting", step: 0, dependency: "r1", updates: [], applied: 0, events: ["tebb · 通用助手 → 写作助手：等待研究成果后成文（模拟委派）"] },
    ],
  };
}

function artifact(run: DemoRun, agent: DemoAgent) {
  return `# ${run.title}\n\n> 交互演示，未进行联网检索、模型分析或外部写入。以下是成果结构样例。\n\n` +
    `负责人：${agent.name}\n\n## 任务目标\n${run.title}\n\n## 已应用的补充要求\n${run.updates.join("\n\n") || "无补充要求。"}\n\n` +
    "## 正式成果将包含\n- 结论与适用场景\n- 可核实的来源与比较依据\n- 部署、成本与限制\n- 后续行动建议\n\n真实执行接入后，此处由实际成果替换。";
}

export function demoReducer(previous: DemoState, action: DemoAction): DemoState {
  if (action.type === "reset") return initialDemo();
  // Clone so React strict mode and callers can safely replay an action.
  const state: DemoState = JSON.parse(JSON.stringify(previous));
  state.notice = "";
  const id = () => `demo-${state.next++}`;
  const message = (conversation: string, sender: string, text: string, run?: string, kind?: DemoMessage["kind"]) => {
    state.messages.push({ id: id(), conversation, sender, text, run, kind });
  };
  const conversation = state.conversations.find(item => item.id === state.selected)!;
  const projects = state.projects ??= [];
  const projectFor = (given: string | undefined) => given ?? conversation.project_id ?? '';
  if (action.type === 'open_folder_project') {
    const primary = action.primary.replace(/\\/g,'/').replace(/\/$/,'');
    let project = projects.find(p=>p.primary?.toLowerCase()===primary.toLowerCase()) || projects.find(p=>p.id===action.bind_id);
    if (!project) { project={id:id(),name:primary.split('/').pop() || '项目',primary};projects.push(project); }
    project.primary=primary;
    let chat=state.conversations.find(c=>c.project_id===project!.id);
    if(!chat){chat={id:id(),title:'新对话',kind:'dm',members:['tebb'],coordinator:'tebb',goal:'',project_id:project.id};state.conversations.push(chat);}
    state.selected=chat.id;
  } else if (action.type === 'project_details') {
    const project=projects.find(p=>p.id===action.id);
    if(project){project.instructions=action.instructions;if(action.remove_source)project.sources=project.sources?.filter(s=>s.id!==action.remove_source);}
  } else if (action.type === "create_project" || action.type === "rename_project") {
    const name = action.name.trim();
    if (!name || name.length > 40 || projects.some(p => p.name.toLowerCase() === name.toLowerCase() && (action.type !== 'rename_project' || p.id !== action.id))) { state.notice = '项目名称须为1至40字，且不能重复。'; return state; }
    if (action.type === 'create_project') projects.push({ id: id(), name });
    else { const p = projects.find(p => p.id === action.id); if (p) {p.name = name;if(action.instructions!==undefined)p.instructions=action.instructions;} }
  } else if (action.type === 'delete_project') {
    state.projects = projects.filter(p => p.id !== action.id);
    state.conversations.forEach(c => { if (c.project_id === action.id) delete c.project_id; });
  } else if (action.type === 'move_conversation') {
    const c = state.conversations.find(c => c.id === action.id);
    if (!c || (action.project_id && !projects.some(p => p.id === action.project_id))) { state.notice = '项目或会话不存在。'; return state; }
    if ((c.project_id || '')!==action.project_id && state.runs.some(r=>r.conversation===c.id && runActive(r))) {state.notice='请等待或停止当前会话任务后再切换项目';return state;}
    c.project_id = action.project_id;
  } else if (action.type === "new_chat") {
    const agent = state.agents.find(a => a.id === (action.agent_id || 'tebb'))!;
    const pid = projectFor(action.project_id);
    if (pid && !projects.some(p => p.id === pid)) { state.notice = '项目不存在。'; return state; }
    const dm = { id: id(), title: '新对话', kind: 'dm' as const, members: [agent.id], coordinator: agent.id, goal: '', project_id: pid };
    state.conversations.push(dm); state.selected = dm.id;
  } else if (action.type === "add_coder") {
    if (!state.agents.some(a => a.id === 'coder')) {
      state.agents.push({ id: 'coder', name: '编程助手', avatar: 'owl', personality: '务实细心，重视验证。', role: '实现代码与运行验证', tools: ['工作区读取','文件编辑','终端'], memory: '', model: '', mark: 'C', tone: 'sage' });
      state.conversations.push({ id: 'dm-coder', title: '编程助手', kind: 'dm', coordinator: 'coder', members: ['coder'], goal: '' });
    }
    state.selected = 'dm-coder';
  } else if (action.type === "select") {
    if (state.conversations.some(item => item.id === action.id)) state.selected = action.id;
  } else if (action.type === "open_dm") {
    const agent = state.agents.find(a => a.id === action.agent_id);
    if (!agent) return previous;
    const pid = projectFor(action.project_id);
    if (pid && !projects.some(p => p.id === pid)) { state.notice = '项目不存在。'; return state; }
    let dm = state.conversations.find(c => c.kind === "dm" && c.coordinator === agent.id && (c.project_id || '') === pid);
    if (!dm) { dm = { id: id(), title: agent.name, kind: "dm", members: [agent.id], coordinator: agent.id, goal: "", project_id: pid }; state.conversations.push(dm); }
    state.selected = dm.id;
  } else if (action.type === "rename_conversation") {
    const row = state.conversations.find(c => c.id === action.id && c.kind === "group");
    if (row && action.title.trim()) row.title = action.title.trim().slice(0, 40);
  } else if (action.type === "delete_conversation") {
    if (state.runs.some(r => r.conversation === action.id && runActive(r))) { state.notice = "此会话仍有任务执行中，请先停止任务并等待结束后删除"; return state; }
    state.conversations = state.conversations.filter(c => c.id !== action.id);
    state.messages = state.messages.filter(m => m.conversation !== action.id);
    state.runs = state.runs.filter(r => r.conversation !== action.id);
    state.requests = state.requests.filter(r => r.conversation !== action.id);
    if (!state.conversations.length) {
      const a = state.agents[0]; state.conversations.push({ id: id(), title: a.name, kind: "dm", members: [a.id], coordinator: a.id, goal: "" });
    }
    if (state.selected === action.id) state.selected = state.conversations[0].id;
  } else if (action.type === "send") {
    const text = action.text.trim().slice(0, 4000);
    if (!text) return previous;
    if (action.run) {
      const run = state.runs.find(item => item.id === action.run && item.conversation === conversation.id);
      if (!run || !runActive(run)) { state.notice = "这项任务已结束或不属于当前会话，请重新选择。"; return state; }
      run.updates.push(text);
      run.events.push("已接收补充要求，等待下一个演示步骤应用");
      message(conversation.id, "user", text, run.id);
      message(conversation.id, run.agent, "补充要求已接收，将在下一个演示步骤应用。", run.id);
      return state;
    }
    let target = action.target || conversation.coordinator;
    const mention = text.match(/^@([^\s]+)(?:\s|$)/);
    if (mention) {
      const mentioned = state.agents.find(agent => agent.name === mention[1] && conversation.members.includes(agent.id));
      if (!mentioned) { state.notice = "@ 的 Agent 不在当前会话中，请从发送对象中选择。"; return state; }
      target = mentioned.id;
    }
    if (!conversation.members.includes(target)) { state.notice = "发送对象不在当前会话中。"; return state; }
    message(conversation.id, "user", text);
    if (action.mode === "message" || (action.mode === "auto" && !/研究|搜索|整理|实现|修改|代码|测试|比较/.test(text))) {
      const active = state.runs.filter(run => run.conversation === conversation.id && runActive(run));
      message(conversation.id, target, active.length
        ? `收到你的消息。当前有 ${active.length} 项后台任务，聊天可以继续。要修改某项任务，请点任务卡上的「补充要求」。（本地模拟回复）`
        : "收到。这是独立的聊天消息；选择「交给 Agent」可以演示后台任务。（本地模拟回复）");
    } else {
      const delegated = conversation.kind === "group" && target === conversation.coordinator && conversation.members.length > 1;
      const workers = delegated ? conversation.members.filter(member => member !== target).slice(0, 2) : [target];
      const first = id();
      workers.forEach((agent, index) => state.runs.push({ id: index ? id() : first, conversation: conversation.id, agent,
        title: index ? `整理成果：${text}` : text, state: index ? "waiting" : "running", step: 0,
        dependency: index ? first : undefined, updates: [], applied: 0,
        events: [`${state.agents.find(a => a.id === target)!.name} → ${state.agents.find(a => a.id === agent)!.name}：${index ? "等待前序成果" : "开始任务"}（模拟）`] }));
      message(conversation.id, target, delegated ? "已演示拆分与委派。前序任务完成后，成果会交给下一位 Agent；你可以继续发消息。" : "已放入后台任务区，你可以继续聊天或补充要求。", first);
    }
  } else if (action.type === "advance") {
    // Each tick uses the previous snapshot: dependencies never skip a visible handoff.
    for (const run of state.runs) {
      if (!runActive(run)) continue;
      if (run.state === "waiting") {
        if (previous.runs.find(item => item.id === run.dependency)?.state === "succeeded") {
          run.state = "running";
          run.events.push("已收到前序成果，开始整理（模拟交接）");
          message(run.conversation, run.agent, "前序成果已收到，开始接续工作。（模拟交接）", run.id);
        }
        continue;
      }
      if (run.applied < run.updates.length) {
        run.applied = run.updates.length;
        run.events.push(`已应用 ${run.applied} 条补充要求（模拟安全边界）`);
      }
      run.events.push(STEP_LABELS[Math.min(run.step, 2)] + "（模拟）");
      run.step++;
      if (run.step >= 3) {
        run.state = "succeeded";
        run.artifact = artifact(run, state.agents.find(agent => agent.id === run.agent)!);
        message(run.conversation, run.agent, "演示成果已整理好，可打开查看结构样例。未检索资料或生成真实结论。", run.id, "result");
      }
    }
  } else if (action.type === "cancel") {
    const root = state.runs.find(run => run.id === action.id);
    if (!root || !runActive(root)) return previous;
    const cancelled = new Set([root.id]);
    let changed = true;
    while (changed) {
      changed = false;
      state.runs.forEach(run => { if (runActive(run) && run.dependency && cancelled.has(run.dependency) && !cancelled.has(run.id)) { cancelled.add(run.id); changed = true; } });
    }
    state.runs.forEach(run => { if (cancelled.has(run.id) && runActive(run)) { run.state = "cancelled"; run.events.push("演示已停止，已保留进度及补充要求"); } });
    message(root.conversation, "notice", "已停止所选任务及依赖它的后续任务，其他任务继续。", root.id, "notice");
  } else if (action.type === "group") {
    const members = [...new Set(action.members)].filter(member => state.agents.some(agent => agent.id === member));
    if (!action.title.trim() || members.length < 2) { state.notice = "请填写群名，并选择至少两位 Agent。"; return state; }
    const existing = state.conversations.find(c => c.id === action.id && c.kind === 'group');
    const pid = existing ? existing.project_id || '' : projectFor(action.project_id);
    if (pid && !projects.some(p => p.id === pid)) { state.notice = '项目不存在。'; return state; }
    const group = { id: existing?.id || id(), title: action.title.trim().slice(0, 40), kind: "group" as const, members,
      coordinator: members.includes("tebb") ? "tebb" : members[0], goal: action.goal.trim().slice(0, 500), project_id: pid };
    if (existing) state.conversations = state.conversations.map(c => c.id === group.id ? group : c);
    else { state.conversations.push(group); message(group.id, "notice", "协作群已创建。选择发送对象，或让协调者分配工作。", undefined, "notice"); }
    state.selected = group.id;
  } else if (action.type === "agent") {
    const name = action.agent.name.trim().slice(0, 24);
    if (!name || /[\s@]/.test(name) || state.agents.some(agent => agent.id !== action.agent.id && agent.name === name)) {
      state.notice = "名称不能留空、重复，或包含空格和 @。"; return state;
    }
    const existing = state.agents.find(agent => agent.id === action.agent.id);
    const agent = { ...action.agent, id: existing?.id || id(), name, role: action.agent.role.slice(0, 160), model: action.agent.model.slice(0, 80), memory: action.agent.memory.slice(0, 2000) };
    if (existing) state.agents = state.agents.map(item => item.id === agent.id ? agent : item);
    else state.agents.push(agent);
    const dm = state.conversations.find(item => item.kind === "dm" && item.coordinator === agent.id);
    if (dm) dm.title = name;
    else state.conversations.push({ id: `dm-${agent.id}`, title: name, kind: "dm", members: [agent.id], coordinator: agent.id, goal: "独立会话，记忆按 Agent 展示。" });
    state.notice = "Agent 配置已保存到本地原型，未修改真实模型、权限或记忆。";
  } else if (action.type === "request") {
    const run = state.runs.find(item => item.id === action.run && item.state === "succeeded");
    if (run && !state.requests.some(item => item.run === run.id)) state.requests.push({ id: id(), run: run.id, conversation: run.conversation, state: "pending" });
    state.notice = "交付确认已加入待办，这是交互演示，不会向飞书发送消息。";
  } else if (action.type === "decide") {
    const request = state.requests.find(item => item.id === action.id && item.state === "pending");
    if (!request) return previous;
    request.state = action.approved ? "approved" : "rejected";
    message(request.conversation, "notice", action.approved ? "已确认交付演示。没有实际向飞书发送内容。" : "已拒绝交付演示，成果保留在当前会话。", request.run, "notice");
  }
  return state;
}

export function restoreDemo(raw: string | null): DemoState {
  if (!raw || raw.length > 2_000_000) return initialDemo();
  try {
    const state = JSON.parse(raw);
    // Validate saved structures before rendering or resuming the local simulation.
    if (state.version !== 1 || !Number.isSafeInteger(state.next) || state.next < 20 || typeof state.selected !== "string") throw new Error();
    const strings = (items: unknown) => Array.isArray(items) && items.every(item => typeof item === "string");
    if (!Array.isArray(state.agents) || !state.agents.length || !state.agents.every((a: DemoAgent) => a &&
      [a.id, a.name, a.role, a.mark, a.tone, a.model, a.memory].every(value => typeof value === "string") && strings(a.tools))) throw new Error();
    const agents = new Set(state.agents.map((a: DemoAgent) => a.id));
    state.projects ??= [];
    if (!Array.isArray(state.projects) || !state.projects.every((p: DemoProject) => p && typeof p.id === 'string' && !!p.id && typeof p.name === 'string' && !!p.name.trim() && p.name.length <= 40) || new Set(state.projects.map((p: DemoProject) => p.id)).size !== state.projects.length) throw new Error();
    const projects = new Set(state.projects.map((p: DemoProject) => p.id));
    if (!Array.isArray(state.conversations) || !state.conversations.some((c: DemoConversation) => c.id === state.selected) ||
      !state.conversations.every((c: DemoConversation) => c && typeof c.id === "string" && typeof c.title === "string" && typeof c.goal === "string" &&
        ["dm", "group"].includes(c.kind) && strings(c.members) && c.members.length && c.members.every(member => agents.has(member)) && c.members.includes(c.coordinator) && (!c.project_id || projects.has(c.project_id)))) throw new Error();
    const conversations = new Set(state.conversations.map((c: DemoConversation) => c.id));
    if (!Array.isArray(state.runs) || !state.runs.every((r: DemoRun) => r && typeof r.id === "string" && conversations.has(r.conversation) && agents.has(r.agent) &&
      typeof r.title === "string" && ["running", "waiting", "succeeded", "cancelled"].includes(r.state) && Number.isInteger(r.step) && r.step >= 0 && r.step <= 3 &&
      Number.isInteger(r.applied) && strings(r.updates) && r.applied >= 0 && r.applied <= r.updates.length && strings(r.events) && (r.artifact === undefined || typeof r.artifact === "string"))) throw new Error();
    const runs = new Set(state.runs.map((r: DemoRun) => r.id));
    if (!state.runs.every((r: DemoRun) => !r.dependency || runs.has(r.dependency))) throw new Error();
    if (!Array.isArray(state.messages) || !state.messages.every((m: DemoMessage) => m && typeof m.id === "string" && conversations.has(m.conversation) &&
      typeof m.text === "string" && (agents.has(m.sender) || ["notice", "user"].includes(m.sender)) && (!m.run || runs.has(m.run)))) throw new Error();
    if (!Array.isArray(state.requests) || !state.requests.every((r: DemoRequest) => r && typeof r.id === "string" && conversations.has(r.conversation) && runs.has(r.run) && ["pending", "approved", "rejected"].includes(r.state))) throw new Error();
    if (state.companionVersion !== 1) {
      const defaults = initialDemo().agents;
      const legacy: Record<string, string> = { tebb: "tebb", research: "研究员", writer: "写作者" };
      for (const agent of state.agents as DemoAgent[]) {
        const template = defaults.find(a => a.id === agent.id);
        agent.avatar ??= template?.avatar || "owl";
        agent.personality ??= template?.personality || "";
        if (template && agent.name === legacy[agent.id] && !state.agents.some((a: DemoAgent) => a.id !== agent.id && a.name === template.name)) {
          agent.name = template.name;
          for (const c of state.conversations as DemoConversation[]) if (c.kind === "dm" && c.coordinator === agent.id) c.title = agent.name;
        }
      }
      state.companionVersion = 1;
    }
    return { ...state, notice: "" };
  } catch { return initialDemo(); }
}
