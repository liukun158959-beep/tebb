import { useRef } from 'react';
import { Bot, Users, FlaskConical, PenLine, CodeXml, ChartNoAxesCombined, ChevronRight, Folder, MoreHorizontal, SquarePen, FolderOpen, Folders, Settings2, Inbox, Plus, type LucideIcon } from 'lucide-react';
import { runActive,type DemoState,type DemoConversation,type DemoAgent,type DemoProject } from './chat-prototype-state';
import './chat-sidebar.css';

const PARTNER_ICONS:Record<string,LucideIcon>={research:FlaskConical,writer:PenLine,coder:CodeXml,chart:ChartNoAxesCombined};
function PartnerIcon({agent,group=false}:{agent:string;group?:boolean}){
  const Icon=group?Users:PARTNER_ICONS[agent]||Bot;
  return <Icon size={17} strokeWidth={1.65} aria-hidden="true"/>;
}
export function ChatSidebar({state,selected,draftActive,mobile,listView,onListView,collapsed,onToggle,onNew,onSelect,onMenu,onKeys,onProject,onFolder,onMigrate,onAgent,onPick,onAdd,pendingCount,onInbox,busy,real}:{
  state:DemoState;selected?:string;draftActive:boolean;mobile:boolean;listView:'history'|'companions';onListView:(value:'history'|'companions')=>void;
  collapsed:Record<string,boolean>;onToggle:(id:string)=>void;onNew:()=>void;onSelect:(id:string)=>void;
  onMenu:(id:string,event:React.MouseEvent<HTMLElement>|React.KeyboardEvent<HTMLElement>)=>void;onKeys:(id:string,event:React.KeyboardEvent<HTMLElement>)=>void;
  onProject:(project:DemoProject)=>void;onFolder:()=>void;onMigrate:()=>void;onAgent:(agent:DemoAgent)=>void;onPick:(agent:DemoAgent)=>void;onAdd:()=>void;
  pendingCount:number;onInbox:()=>void;busy:boolean;real:boolean;
}){
  const management=useRef<HTMLDetailsElement>(null);
  const projects=state.projects||[];
  function manage(callback:()=>void){if(management.current)management.current.open=false;callback();}
  function row(item:DemoConversation){
    const agent=state.agents.find(a=>a.id===item.coordinator),active=!draftActive&&item.id===selected;
    return <div className="cp-history-item" key={item.id}>
      <button data-conversation={item.id} data-chat-partner={item.kind==='dm'?item.coordinator:undefined} className={'cp-conversation'+(active?' is-selected':'')} aria-pressed={active}
        aria-label={item.title+'，'+(item.kind==='group'?'协作群':agent?.name||'伙伴')} title={item.title+(item.kind==='group'?' · 协作群': ' · '+(agent?.name||''))}
        onContextMenu={event=>onMenu(item.id,event)} onKeyDown={event=>onKeys(item.id,event)} onClick={()=>onSelect(item.id)}>
        <PartnerIcon group={item.kind==='group'} agent={item.coordinator}/><span><strong>{item.title}</strong></span>
        {item.kind==='group'&&<small className="cp-conversation-kind">群</small>}
        {state.runs.some(r=>r.conversation===item.id&&runActive(r))&&<i className="cp-live-dot"/>}
      </button><button className="cp-icon-button cp-history-more" aria-label={item.title+'的会话操作'} title="会话操作" onClick={event=>onMenu(item.id,event)}><MoreHorizontal size={16}/></button>
    </div>;
  }
  return <aside className={'cp-conversations cp-sidebar-unified'+(mobile?' cp-list-open':'')} aria-label="会话列表">
    <div className="cp-sidebar-title"><strong>伙伴聊天</strong>
      <details ref={management} className="cp-sidebar-management" onKeyDown={e=>{if(e.key==='Escape'&&management.current){management.current.open=false;management.current.querySelector('summary')?.focus();}}}>
        <summary aria-label="管理项目与对话" title="管理项目与对话"><MoreHorizontal size={19}/></summary>
        <div role="menu" aria-label="项目管理"><button role="menuitem" disabled={busy} onClick={()=>manage(onFolder)}><FolderOpen size={17}/>打开文件夹</button>
          {real&&<button role="menuitem" onClick={()=>manage(onMigrate)}><Folders size={17}/>整理已有对话…</button>}
        </div>
      </details>
    </div>
    <button className="cp-new-conversation" aria-pressed={draftActive} onClick={onNew}><SquarePen size={18}/>新对话</button>
    <div className="cp-list-switch" role="tablist" aria-label="聊天导航"><button role="tab" aria-selected={listView==='history'} onClick={()=>onListView('history')}>对话</button><button role="tab" aria-selected={listView==='companions'} onClick={()=>onListView('companions')}>伙伴</button></div>
    {listView==='history'?<div className="cp-history-tree" role="tabpanel" aria-label="对话记录">
      <div className="cp-list-heading"><span>项目</span></div>
      {!projects.length&&<p className="cp-project-empty">还没有项目，可从右上角管理中打开文件夹。</p>}
      {projects.map(project=>{
        const chats=[...state.conversations].reverse().filter(c=>c.project_id===project.id),open=!collapsed[project.id];
        return <section className="cp-chat-project" key={project.id} aria-label={project.name+'项目'} data-chat-project={project.id}>
          <div className="cp-project-heading"><button className="cp-project-toggle" aria-expanded={open} aria-controls={'cp-project-'+project.id} onClick={()=>onToggle(project.id)}><ChevronRight className="cp-project-chevron" size={14}/><Folder size={17}/><strong title={project.name}>{project.name}</strong><small>{chats.length}</small></button>
            <button className="cp-icon-button" aria-label={project.name+'的项目设置'} title="项目设置" onClick={()=>onProject(project)}><MoreHorizontal size={16}/></button></div>
          <div id={'cp-project-'+project.id} className="cp-project-chats" hidden={!open}>{chats.map(row)}{!chats.length&&<p className="cp-project-empty">还没有对话</p>}</div>
        </section>;
      })}
      <div className="cp-list-heading cp-ungrouped-heading"><span>其他对话</span></div>
      {[...state.conversations].reverse().filter(c=>!projects.some(p=>p.id===c.project_id)).map(row)}
    </div>:<div className="cp-partner-list" role="tabpanel" aria-label="伙伴目录">
      <div className="cp-list-heading"><span>伙伴目录</span><button className="cp-icon-button" aria-label="创建 Agent" onClick={onAdd}><Plus size={17}/></button></div>
      {state.agents.map(agent=><div className="cp-companion-card" key={agent.id}><button data-companion={agent.id} className="cp-conversation" onClick={()=>onPick(agent)} title={'选择'+agent.name+'作为新对话伙伴'}><span className="cp-sidebar-agent-icon"><PartnerIcon agent={agent.id}/></span><span><strong>{agent.name}</strong><small>{agent.personality||agent.role}</small></span></button><button className="cp-icon-button" aria-label={agent.name+'的设置'} onClick={()=>onAgent(agent)}><Settings2 size={17}/></button></div>)}
    </div>}
    <div className="cp-list-bottom"><button className="cp-inbox-link" onClick={onInbox}><span><Inbox size={16}/>待你处理</span><b>{pendingCount}</b></button>
      <div className="cp-profile"><span className="cp-user-avatar">你</span><span><strong>我的工作空间</strong><small>{real?'本地执行 · 真实用量':'本地预览 · 未消耗模型额度'}</small></span></div>
    </div>
  </aside>;
}
