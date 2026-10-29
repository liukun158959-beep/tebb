import { ContextDebug } from './context-debug';
import { ClarificationCard } from './clarification-card';
import type { ClarificationAction } from './clarification-state';
import { ComposerPicker } from './composer-popover';
import { useEffect, useRef, useState } from 'react';
import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { backendInfo, rpc, type BackendInfo } from './api';
import { runActive, type DemoState } from './chat-prototype-state';
import { StreamText } from './stream-text';
import { useAutoGrowInput } from './auto-grow-input';
import { ExecutionTree, executionMessage, visibleChatMessage } from './run-progress';
import { CollaborationSync, loadCollaboration } from './collaboration-sync';
import { composerTrigger, applyComposerChoice } from './composer-picker';
import { AppIcon } from './companion-ui';

type Progress = { enabled: boolean; task?: { id: string; state: string; text: string; status: string; answer: string; events: string[] } };
export function PetChat({ backend }: { backend?: BackendInfo }) {
  const [info, setInfo] = useState<BackendInfo | null>(backend || null), [state, setState] = useState<DemoState | null>(null);
  const [visible, setVisible] = useState(true), [conversation, setConversation] = useState('');
  const [drafts, setDrafts] = useState<Record<string, string>>({}), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [menu, setMenu] = useState<{x:number;y:number} | null>(null), [progress, setProgress] = useState<Progress | null>(null);
  const [model, setModel] = useState(''), [pollError, setPollError] = useState('');
  const [pinned,setPinned]=useState(false),[pinBusy,setPinBusy]=useState(false);
  const [creating,setCreating]=useState(false),[resumeRun,setResumeRun]=useState('');
  const newRequest=useRef<{id:string;agent:string;conversation:string} | null>(null);
  const [cursor,setCursor]=useState(0),[pickerIndex,setPickerIndex]=useState(0),[dismissed,setDismissed]=useState(false);
  const [skillMap,setSkillMap]=useState<Record<string,string[]>>({});
  const input=useRef<HTMLTextAreaElement>(null);
  const scroller = useRef<HTMLDivElement>(null), following = useRef(true), generation = useRef(0), alive = useRef(true);
  const pending = useRef<{ fingerprint: string; id: string } | null>(null);
  const sync = useRef(new CollaborationSync());
  const cid = conversation || state?.selected || '', draft = drafts[cid] || '';
  const current = state?.conversations.find(c => c.id === cid);
  const skills=skillMap[cid] || [];
  const trigger=dismissed ? null : composerTrigger(draft,cursor);
  const options=trigger?.kind==='members' ? (state?.agents || []).filter(a=>(!current?.members || current.members.includes(a.id)) && a.name.toLowerCase().includes(trigger.query.toLowerCase())).map(a=>({id:a.id,name:a.name,detail:a.personality || a.role})) : (state?.skills || []).filter(s=>(s.id+' '+s.description).toLowerCase().includes((trigger?.query || '').toLowerCase())).map(s=>({id:s.id,name:s.id,detail:s.description}));
  const runs = state?.runs.filter(r => r.conversation === cid) || [], active = runs.filter(runActive);
  const messages = state?.messages.filter(m => m.conversation === cid && visibleChatMessage(m,runs)) || [];
  useAutoGrowInput(input, draft, `${cid}:${visible}`);
  const accept = (next: DemoState) => { const accepted=sync.current.snapshot(next);if(accepted)setState(accepted);setConversation(old=>old || next.selected); };
  useEffect(() => {
    alive.current = true;
    if (!backend) void backendInfo().then(value => { if (alive.current) setInfo(value); }).catch(e => { if (alive.current) setError(String(e)); });
    let disposed = false; let off: (() => void) | undefined;
    if (isTauri()) void invoke<{visible:boolean;pinned:boolean}>('get_pet_chat_state').then(value=>{if(alive.current && typeof value?.pinned==='boolean'){setPinned(value.pinned);setVisible(value.visible);}}).catch(e=>{if(alive.current)setError(String(e));});
    if (isTauri()) void listen<boolean>('pet-chat-visible', event => setVisible(event.payload)).then(unlisten => { if (disposed) unlisten(); else off = unlisten; });
    const link = (event:Event) => { const detail=(event as CustomEvent).detail;if(detail?.kind==='web') void invoke('open_link',{url:detail.url}).catch(e=>setError(String(e))); };
    window.addEventListener('desk-open-preview',link);
    return () => { window.removeEventListener('desk-open-preview',link);alive.current = false; disposed = true; off?.(); };
  }, [backend]);
  useEffect(() => {
    if (!info || !visible) return;
    let live = true, fetching = false;
    const refresh = async () => {
      if (fetching) return; fetching = true;
      try {
        const [collab, task] = await Promise.all([loadCollaboration(info,sync.current.sync), rpc<Progress>(info, 'load_task_progress')]);
        if (live) {const next=sync.current.update(collab);if(next){setState(next);setConversation(old=>old || next.selected);}setProgress(task);setPollError(''); }
      } catch (e) { if (live) setPollError(String(e)); } finally { fetching = false; }
    };
    void refresh(); const timer = window.setInterval(() => void refresh(), 400);
    return () => { live = false; window.clearInterval(timer); };
  }, [info?.port, info?.token, visible]);
  useEffect(() => {
    if (following.current && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [state?.revision, progress?.task?.answer, progress?.task?.status, cid, visible]);
  useEffect(()=>{setPickerIndex(0);},[trigger?.query,trigger?.kind,cid,options.length]);
  useEffect(()=>{setDismissed(false);setResumeRun('');setCursor(drafts[cid]?.length || 0);},[cid]);
  function changeDraft(value:string, position=value.length) {setDrafts(old=>({...old,[cid]:value}));setCursor(position);setDismissed(false);}
  function choose(index:number) {
    const option=options[index];if(!trigger || !option) return;
    if(trigger.kind==='skills' && !skills.includes(option.id) && skills.length>=10){setError('每轮最多选择10个 Skill。');return;}
    const result=applyComposerChoice(draft,trigger,option.name);
    if(trigger.kind==='skills')setSkillMap(old=>({...old,[cid]:[...new Set([...skills,option.id])]}));
    changeDraft(result.text,result.cursor);setDismissed(true);input.current?.focus();
    if(input.current)requestAnimationFrame(()=>input.current?.setSelectionRange(result.cursor,result.cursor));
  }
  function insertTrigger(value:string) {const start=input.current?.selectionStart ?? draft.length;const end=input.current?.selectionEnd ?? start;const spacer=start && !/\s$/.test(draft.slice(0,start))?' ':'';changeDraft(draft.slice(0,start)+spacer+value+draft.slice(end),start+spacer.length+1);input.current?.focus();}
  async function togglePin(){if(pinBusy)return;setPinBusy(true);try{const value=await invoke<boolean>('set_pet_chat_pinned',{pinned:!pinned});setPinned(value);}catch(e){setError(String(e));}finally{setPinBusy(false);}}
  async function newChat(){
    if(!info || creating || busy)return;
    setCreating(true);setError('');const agent=current?.coordinator || 'tebb';
    if(!newRequest.current || newRequest.current.agent!==agent || newRequest.current.conversation!==cid)newRequest.current={id:crypto.randomUUID(),agent,conversation:cid};
    try {
      const result=await rpc<{state:DemoState}>(info,'collaboration_action',{action:{type:'new_chat',conversation:cid,agent_id:agent,request_id:newRequest.current.id}});
      if(!alive.current)return;
      accept(result.state);setConversation(result.state.selected);setModel('');setDismissed(true);following.current=true;newRequest.current=null;
    }catch(e){if(alive.current)setError(String(e)+'；重试将沿用新对话编号。');}
    finally{if(alive.current)setCreating(false);}
  }
  async function close() { setMenu(null); await invoke('set_pet_chat_visible', { visible: false }).catch(e => setError(String(e))); }
  async function openMain() {
    try { if(info && cid) await rpc(info,'collaboration_action',{action:{type:'select',id:cid,conversation:cid,request_id:crypto.randomUUID()}});await invoke('show_main'); } catch(e) {setError(String(e));}
  }
  async function send() {
    if (!info || !cid || !draft.trim() || busy) return;
    const text = draft, selected = cid, stamp = ++generation.current;
    const body = { type: 'send', conversation: selected, text, target: '', mode: 'auto', model_id: model, skills, ...(resumeRun?{run:resumeRun}:{}) };
    const fingerprint = JSON.stringify(body);
    if (pending.current?.fingerprint !== fingerprint) pending.current = { fingerprint, id: crypto.randomUUID() };
    setBusy(true); setError('');
    try {
      const result = await rpc<{ state: DemoState }>(info, 'collaboration_action', { action: { ...body, request_id: pending.current.id } });
      if (!alive.current || stamp !== generation.current) return;
      accept(result.state); pending.current = null;setResumeRun('');
      setSkillMap(old=>old[selected]===skills ? {...old,[selected]:[]} : old);setDismissed(true);
      setDrafts(old => old[selected] === text ? { ...old, [selected]: '' } : old); following.current = true;
    } catch (e) { if (alive.current) setError(String(e) + '；重试相同内容会沿用提交编号。'); }
    finally { if (alive.current) setBusy(false); }
  }
  async function clarify(action:ClarificationAction){
    if(!info||busy)return false;
    const body=JSON.stringify(action);if(pending.current?.fingerprint!==body)pending.current={fingerprint:body,id:crypto.randomUUID()};
    setBusy(true);setError('');try{const result=await rpc<{state:DemoState}>(info,'collaboration_action',{action:{...action,request_id:pending.current.id}});accept(result.state);pending.current=null;return true;}
    catch(e){setError(String(e));return false;}finally{setBusy(false);}
  }
  async function stop(id: string) {
    if (!info) return;
    try { const result = await rpc<{state:DemoState}>(info, 'collaboration_action', { action: { type:'cancel', id, conversation:cid, request_id:crypto.randomUUID() } }); accept(result.state); }
    catch(e) { setError(String(e)); }
  }
  return <main className="pet-chat-window" onContextMenu={event => { if ((event.target as HTMLElement).closest('input,textarea')) return; event.preventDefault(); setMenu({x:Math.max(8,Math.min(event.clientX,window.innerWidth-160)), y:Math.max(8,Math.min(event.clientY,window.innerHeight-76))}); }}>
    <header className="pet-chat-title" data-tauri-drag-region><strong data-tauri-drag-region>tebb.</strong><span data-tauri-drag-region>{active.length ? '正在执行' : runs.some(r=>r.state==='needs_input')?'待补充':'随时聊聊'}</span><div className="pet-chat-window-actions" role="toolbar" aria-label="浮动聊天窗口操作"><button type="button" aria-label="创建新对话" title="创建新对话，保留当前任务和草稿" disabled={!info || creating || busy} onClick={()=>void newChat()}><AppIcon name="new_chat" /></button><button type="button" className="pet-chat-pin" aria-label={pinned?'取消置顶':'置顶对话框'} title={pinned?'取消置顶：点击其他界面时收起':'置顶：点击其他界面时保持显示'} aria-pressed={pinned} disabled={pinBusy} onClick={()=>void togglePin()}><AppIcon name="pin" /></button><button type="button" title="在主窗口打开" aria-label="在主窗口打开" onClick={() => void openMain()}><AppIcon name="expand" /></button><button type="button" aria-label="关闭对话框" title="关闭，任务继续执行" onClick={() => void close()}><AppIcon name="close" /></button></div></header>
    {resumeRun&&<div role="status">补充原任务目标<button type="button" onClick={()=>setResumeRun('')}>改发新消息</button></div>}
    <div className="pet-chat-controls"><select aria-label="当前会话" value={cid} onChange={e => { setConversation(e.target.value); setModel(''); following.current = true; }}>{state?.conversations.map(c => <option value={c.id} key={c.id}>{c.title}</option>)}</select><select aria-label="本轮模型" value={model} onChange={e => setModel(e.target.value)}><option value="">默认模型</option>{state?.models?.map(m => <option key={m.id} value={m.id}>{m.model}</option>)}</select></div>
    <div className="pet-chat-scroll" ref={scroller} onScroll={e => { const el=e.currentTarget; following.current = el.scrollHeight-el.scrollTop-el.clientHeight < 40; }} aria-label="聊天与任务过程">
      {!state && <p className="pet-chat-placeholder">正在连接当前会话…</p>}
      {state && !messages.length && <p className="pet-chat-placeholder">{current?.title || '当前会话'}<br/>可以直接提问，也可以 @ 伙伴安排任务。</p>}
      {messages.map(message => { const run=runs.find(r=>r.id===message.run);return <article key={message.id} className={`pet-chat-message ${message.sender==='user'?'from-user':''}`}><small>{message.sender==='user'?'你':state?.agents.find(a=>a.id===message.sender)?.name || 'tebb'}</small>{message.sender!=='user' && run && <ExecutionTree run={run} runs={runs} agents={state?.agents||[]}/>}{run?.state==='needs_input'&&message.sender!=='user'&&<ClarificationCard key={run.id+':'+run.clarification_version} run={run} busy={busy} onAction={clarify} onText={()=>{setResumeRun(run.id);input.current?.focus();}}/>}{(message.sender==='user'||run?.state!=='needs_input')&&<StreamText text={executionMessage(message,runs)} active={!!run && runActive(run) && !!message.text} />}{message.sender!=='user' && run && (runActive(run)||run.state==='needs_input') && <button className="pet-chat-stop" onClick={()=>void stop(run.id)}>停止任务</button>}</article>; })}
      {progress?.enabled && progress.task && <details className="pet-chat-task" open><summary>其他通道 · {progress.task.text}</summary><ol>{progress.task.events?.map((event,index)=><li key={index}><span className="tebb-stream-new">{event}</span></li>)}</ol><StreamText text={progress.task.answer || progress.task.status} active={['running','queued','cancelling'].includes(progress.task.state)} /></details>}
    </div>
    {(error || pollError) && <p className="pet-chat-error" role="alert">{error || pollError}</p>}
    <form className="pet-chat-composer" onSubmit={e=>{e.preventDefault();void send();}}>
      {trigger && <ComposerPicker anchor={input} className="pet-chat-picker" id="pet-composer-options" label={trigger.kind==='members'?'选择成员':'选择技能'} onClose={()=>setDismissed(true)}>{options.map((option,index)=><button type="button" role="option" id={`pet-option-${index}`} key={option.id} aria-selected={index===pickerIndex} onMouseDown={e=>e.preventDefault()} onClick={()=>choose(index)}><strong>{option.name}</strong><small>{option.detail}</small></button>)}{!options.length && <p>{trigger.kind==='skills'?'没有匹配的 Skill，可在主窗口配置。':'没有匹配的会话成员。'}按 Esc 退出选择。</p>}</ComposerPicker>}
      {skills.length>0 && <div className="pet-chat-skills">{skills.map(id=><button type="button" key={id} aria-label={`移除技能 ${id}`} onClick={()=>setSkillMap(old=>({...old,[cid]:skills.filter(s=>s!==id)}))}>{id} ×</button>)}</div>}
      <textarea ref={input} disabled={!cid} value={draft} maxLength={4000} aria-label="浮动聊天消息" placeholder="输入消息，@ 成员 / Skill…" aria-expanded={!!trigger} aria-controls={trigger?'pet-composer-options':undefined} aria-activedescendant={trigger && options.length?`pet-option-${pickerIndex}`:undefined} onChange={e=>changeDraft(e.target.value,e.target.selectionStart ?? e.target.value.length)} onSelect={e=>setCursor(e.currentTarget.selectionStart)} onKeyDown={e=>{
        if(e.nativeEvent.isComposing || e.keyCode===229)return;
        if(trigger){if(e.key==='Escape'){e.preventDefault();setDismissed(true);return;}if(['ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();setPickerIndex(index=>options.length?(index+(e.key==='ArrowUp'?-1:1)+options.length)%options.length:0);return;}if((e.key==='Enter'&&!e.shiftKey) || (e.key==='Tab'&&options.length)){e.preventDefault();choose(pickerIndex);return;}}
        if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();void send();}
      }}/><footer><div className="pet-chat-compose-actions"><button type="button" aria-label="选择成员" disabled={!cid} title="@ 成员" onClick={()=>insertTrigger('@')}>@</button><button type="button" aria-label="选择技能" disabled={!cid} title="/ Skill" onClick={()=>insertTrigger('/')}>/</button></div><span>{active.length ? <><i className="tebb-stream-cursor" />任务执行中</>:'Enter 发送 · Shift+Enter 换行'}</span><button type="submit" disabled={busy || !cid || !draft.trim()}>发送 ↑</button></footer></form>
    {menu && <><button className="pet-chat-menu-dismiss" aria-label="收起菜单" onClick={()=>setMenu(null)}/><div className="pet-chat-menu" role="menu" style={{left:menu.x,top:menu.y}}><button role="menuitem" onClick={()=>void close()}>关闭对话框</button><small>任务继续，重新打开可恢复</small></div></>}
    <ContextDebug key={cid} info={info} conversation={cid} draft={draft}/>
  </main>;
}
