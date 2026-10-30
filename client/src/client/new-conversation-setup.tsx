import { X, ArrowLeft } from 'lucide-react';
import type { DemoAgent,DemoProject } from './chat-prototype-state';
import type { ConversationDraft } from './conversation-draft';

export function NewConversationSetup({draft,agents,projects,busy,onChange,onGroup,onHide,onDiscard}:{
  draft:ConversationDraft;agents:DemoAgent[];projects:DemoProject[];busy:boolean;
  onChange:(value:Partial<Pick<ConversationDraft,'agent_id'|'project_id'>>)=>void;
  onGroup:()=>void;onHide:()=>void;onDiscard:()=>void;
}){
  const locked=busy||!!draft.attempt;
  return <section className="cp-new-setup" aria-label="新对话草稿">
    <div className="cp-new-setup-heading"><span>未发送草稿 · 首条消息发送后加入列表</span><div>
      <button type="button" className="cp-text-button" onClick={onHide}><ArrowLeft size={15}/>返回对话</button>
      <button type="button" className="cp-icon-button" aria-label="放弃新对话草稿" disabled={busy} onClick={onDiscard}><X size={17}/></button>
    </div></div>
    <div className="cp-new-fields"><label>对话方式<select aria-label="新对话伙伴" disabled={locked} value={draft.agent_id} onChange={e=>e.target.value==='__group__'?onGroup():onChange({agent_id:e.target.value})}>
      {!agents.some(a=>a.id===draft.agent_id)&&<option value={draft.agent_id}>伙伴已移除，请重新选择</option>}
      {agents.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}<option value="__group__">新建协作群…</option>
    </select></label>
    <label>所属项目<select aria-label="新对话所属项目" disabled={locked} value={draft.project_id} onChange={e=>onChange({project_id:e.target.value})}>
      <option value="">不属于项目</option>{draft.project_id&&!projects.some(p=>p.id===draft.project_id)&&<option value={draft.project_id}>项目已移除，请重新选择</option>}
      {projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}
    </select></label></div>
    {draft.attempt&&<p role="status" className="cp-muted">{draft.failed?'发送未成功或回执未确认。原消息与去重编号已保留，可核对列表后重试原发送；不会自动发送。':'正在确认首条消息，请稍候…'}</p>}
  </section>;
}
