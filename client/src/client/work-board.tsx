import { useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { DndContext, DragOverlay, KeyboardSensor, PointerSensor, closestCenter, pointerWithin,
  useDraggable, useDroppable, useSensor, useSensors, type CollisionDetection } from '@dnd-kit/core';
import { motion, useReducedMotion } from 'framer-motion';
import { rpc, type BackendInfo } from './api';
import { usePageActive } from './page-stack';
import { Markdown } from './Markdown';
import { SchedulePane, type ScheduleTarget } from './schedule';
import { beijingStamp } from './schedule-state';
import { WorkBoardController, WORK_STATES, demoWorkTransport, workStateLabel, runStateLabel,
  type WorkFields, type WorkItem, type WorkState, type WorkRun, type WorkPage } from './work-board-state';
import './work-board.css';

type Props={info:BackendInfo|null;debug?:boolean;onOpen:(conversation:string)=>Promise<void>|void};
const priorities={high:'高',normal:'中',low:'低'};
const blank=():WorkFields=>({title:'',description:'',project_id:'',priority:'normal',due:''});
const collision:CollisionDetection=args=>{const hits=pointerWithin(args);return hits.length?hits:closestCenter(args);};

function ItemCard({item,controller,onSelect,onMove,onSchedule,overlay=false}:{item:WorkItem;controller:WorkBoardController;onSelect:()=>void;onMove:(id:string,state:WorkState)=>void;onSchedule:(item:WorkItem)=>void;overlay?:boolean}) {
  const drag=useDraggable({id:overlay?'overlay:'+item.id:item.id,data:{state:item.state},disabled:overlay||item.archived||controller.uncertain.has(item.id)});
  const reduce=useReducedMotion();
  const linked=item.links.map(link=>controller.page.runs.find(run=>run.id===link.id)).filter(Boolean) as WorkRun[];
  const busy=controller.pending.has(item.id),uncertain=controller.uncertain.has(item.id);
  const arrangement=item.schedules?.next||item.schedules?.previous;
  return <motion.article ref={overlay?undefined:drag.setNodeRef} layout={!overlay&&!drag.isDragging&&!reduce}
    transition={{duration:reduce?0:.18}} className={`wb-card${drag.isDragging?' wb-lifted':''}${overlay?' wb-ghost':''}`}
    data-work-card={item.id} data-state={item.state} aria-label={`${item.title}，${workStateLabel(item.state)}`}>
    <div className="wb-card-top"><span className="wb-project">▱ {item.project_name||'未归属'}</span>
      {!overlay&&<button type="button" className="wb-grip" ref={drag.setActivatorNodeRef} {...drag.listeners} {...drag.attributes}
        aria-label={`拖动 ${item.title}`} disabled={item.archived||uncertain}>⠿</button>}</div>
    <button type="button" className="wb-card-title" onClick={onSelect}>{item.title}</button>
    {item.description&&<p className="wb-card-description">{item.description}</p>}
    {item.removed_project&&<p className="wb-removed">原项目已移除：{item.removed_project}</p>}
    <div className="wb-meta"><span className={item.priority==='high'?'wb-high':''}>⚑ {priorities[item.priority]}优先级</span><span>{item.due||'未设截止日'}</span></div>
    {!overlay&&!!item.schedules?.events.length&&<button type="button" className="wb-schedule-summary" onClick={()=>onSchedule(item)}>{arrangement?`◷ ${arrangement.start.includes('T')?beijingStamp(arrangement.start).slice(5).replace('T',' '):arrangement.start}${item.schedules.unchecked?' · 未核对':''}`:`◷ 历史安排 · ${item.schedules.events.length} 场`}</button>}
    {!!item.links.length&&<div className="wb-ai"><span className="wb-avatar">{linked[0]?.agent.slice(0,1)||'AI'}</span><span>{linked[0]?`${linked[0].agent} · ${runStateLabel(linked[0])}`:'关联记录已失效'}{item.links.length>1?` · 共 ${item.links.length} 条`:''}</span></div>}
    {!overlay&&<div className="wb-card-bottom"><span role="status">{uncertain?'结果未确认':busy?'保存中…':controller.intents.has(item.id)?'待保存':''}</span>
      <label><span className="sr-only">移动 {item.title}</span><select value={item.state} aria-label={`移动 ${item.title}`} disabled={item.archived||uncertain}
        onChange={e=>onMove(item.id,e.target.value as WorkState)}>{WORK_STATES.map(([id,label])=><option value={id} key={id}>移至 · {label}</option>)}</select></label></div>}
    {controller.rowErrors.get(item.id)&&<p className="wb-row-error" role="alert">{controller.rowErrors.get(item.id)}</p>}
  </motion.article>;
}
function Column({state,label,children,count}:{state:WorkState;label:string;children:React.ReactNode;count:number}) {
  const drop=useDroppable({id:state});
  return <section ref={drop.setNodeRef} className={`wb-column${drop.isOver?' wb-drop-target':''}`} data-work-column={state} aria-label={label}>
    <header><span className={`wb-state-dot ${state}`}/><h2>{label}</h2><span className="wb-count">{count}</span></header>{children}
    {!count&&<div className="wb-empty-column">暂无事项</div>}{drop.isOver&&<div className="wb-drop-slot">移至{label}</div>}
  </section>;
}

export function WorkBoardPane({info,debug=false,onOpen}:Props) {
  const active=usePageActive(),reduce=useReducedMotion();
  const demo=useMemo(()=>debug?demoWorkTransport():null,[debug]);
  const controller=useMemo(()=>new WorkBoardController(demo||(async(method,args)=>{
    if(!info)throw Error('后端尚未连接。');return rpc<WorkPage>(info,method,args,{timeoutMs:15000});
  })),[info?.port,info?.token,demo]);
  const [,redraw]=useReducer(value=>value+1,0);
  const [project,setProject]=useState('all'),[query,setQuery]=useState(''),[archived,setArchived]=useState(false);
  const [selected,setSelected]=useState(''),[draft,setDraft]=useState<(WorkFields&{id?:string;version?:number})|null>(null);
  const [formError,setFormError]=useState(''),[detailError,setDetailError]=useState(''),[runId,setRunId]=useState(''),[linking,setLinking]=useState(false);
  const [dragging,setDragging]=useState<string|null>(null);
  const [scheduleTarget,setScheduleTarget]=useState<ScheduleTarget|null>(null),[leaveWork,setLeaveWork]=useState<null|(()=>void)>(null);
  const leaveRoot=useRef<HTMLDivElement|null>(null);
  const draftRef=useRef(draft);draftRef.current=draft;
  const draftBase=useRef('');
  const formRequest=useRef('');const formRoot=useRef<HTMLElement|null>(null);
  const moveFocus=useRef('');
  useEffect(()=>{if(!moveFocus.current)return;const label=moveFocus.current;moveFocus.current='';[...document.querySelectorAll<HTMLElement>('.work-board select,.work-board .wb-grip')].find(node=>node.getAttribute('aria-label')===label)?.focus();});
  function move(id:string,state:WorkState){const label=document.activeElement?.getAttribute('aria-label')||'';if(label.startsWith('移动 ')||label.startsWith('拖动 '))moveFocus.current=label;void controller.move(id,state);}
  const dragSensors=useSensors(useSensor(PointerSensor,{activationConstraint:{distance:6}}),useSensor(KeyboardSensor));
  useEffect(()=>controller.subscribe(redraw),[controller]);
  useEffect(()=>{if(!active||(!info&&!debug))return;void controller.load();const timer=window.setInterval(()=>{if(!controller.loading)void controller.load();},5000);return()=>window.clearInterval(timer);},[active,controller,info,debug]);
  const item=controller.items.find(i=>i.id===selected);
  const drawer=!scheduleTarget&&(!!draft||!!item);
  useEffect(()=>{if(!drawer)return;const previous=document.activeElement as HTMLElement|null;formRoot.current?.querySelector<HTMLElement>('button,input')?.focus();return()=>{if(previous?.isConnected)previous.focus();};},[drawer,!!draft,selected]);
  useEffect(()=>{setRunId('');setLinking(false);setDetailError('');},[selected]);
  useEffect(()=>{if(leaveWork)leaveRoot.current?.querySelector<HTMLElement>('button')?.focus();},[!!leaveWork]);
  const visible=controller.items.filter(item=>item.archived===archived&&(project==='all'||(project==='none'?!item.project_id:item.project_id===project))&&(!query||(item.title+' '+item.description).toLocaleLowerCase().includes(query.toLocaleLowerCase())));
  useEffect(()=>{if(controller.loaded&&project!=='all'&&project!=='none'&&!controller.page.projects.some(p=>p.id===project))setProject('none');},[controller.loaded,controller.page.projects,project]);
  function guardDraft(next:()=>void){if(draft&&JSON.stringify(draft)!==draftBase.current)setLeaveWork(()=>next);else next();}
  function close(){guardDraft(()=>{setDraft(null);setSelected('');setFormError('');formRequest.current='';});}
  function openSchedule(targetItem?:WorkItem){guardDraft(()=>{const event=targetItem?.schedules?.next||targetItem?.schedules?.previous||targetItem?.schedules?.events[0];setDraft(null);setScheduleTarget({item_id:targetItem?.id,...(event?{calendar_id:event.calendar_id,event_id:event.event_id,day:event.start.slice(0,10)}:{})});});}
  function returnToItem(id:string){const targetItem=controller.items.find(i=>i.id===id);if(!targetItem){setDetailError('事项已不可用，请重新读取核对。');return;}setScheduleTarget(null);setProject('all');setQuery('');setArchived(targetItem.archived);setDraft(null);setSelected(id);void controller.load();}
  function recoverForm(){const key=draftRef.current?.id||'create',receipt=controller.completed.get(key);if(formRequest.current&&receipt?.request_id===formRequest.current&&!controller.uncertain.has(key)){setDraft(null);setSelected(receipt.item_id);setFormError('');formRequest.current='';}}
  async function refresh(){await controller.load(true);recoverForm();}
  async function retry(key:string){await controller.retry(key);recoverForm();}
  function create(){guardDraft(()=>{formRequest.current='';setSelected('');const next={...blank(),project_id:project!=='all'&&project!=='none'?project:''};draftBase.current=JSON.stringify(next);setDraft(next);setFormError('');});}
  async function save(event:React.FormEvent){event.preventDefault();if(!draft)return;const submitted=draft;
    if(!draft.title.trim()){setFormError('请输入事项标题。');return false;}
    const fields:WorkFields={title:draft.title,description:draft.description,project_id:draft.project_id,priority:draft.priority,due:draft.due};
    const ok=await controller.change(draft.id?{type:'save',id:draft.id,version:draft.version,fields}:{type:'create',fields});
    if(draftRef.current!==submitted)return false;
    if(ok){setDraft(null);setSelected(controller.completed.get(draft.id||'create')?.item_id||'');setFormError('');formRequest.current='';}
    else {formRequest.current=controller.uncertain.get(draft.id||'create')?.action.request_id||'';setFormError(controller.error||'事项未保存，请核对后再操作。');}
    return ok;
  }
  async function openRun(conversation:string){try{setDetailError('');if(debug){setDetailError('演示记录不会打开真实会话。');return;}await onOpen(conversation);}catch(error){setDetailError(String(error));}}
  const candidates=controller.page.runs.filter(run=>!item?.links.some(link=>link.id===run.id))
    .slice().sort((a,b)=>Number(b.project_id===item?.project_id)-Number(a.project_id===item?.project_id)||b.created-a.created);
  const fieldBusy=controller.pending.has(draft?.id||'create')||controller.uncertain.has(draft?.id||'create');
  return <div className="work-board" data-work-board data-demo={debug?'true':'false'}>
    <header className="wb-heading"><div><span className="wb-eyebrow">工作区 / 工作看板</span><h1>把事情一步步做完。</h1><p>研究、动手、验收，都有自己的位置。{debug&&' · 演示数据，未连接后端'}</p></div>
      <button type="button" className="wb-primary" onClick={create}>＋ 新增事项</button></header>
    <div className="wb-toolbar"><label className="wb-search"><span aria-hidden="true">⌕</span><input aria-label="搜索事项" placeholder="搜索事项" value={query} onChange={e=>setQuery(e.target.value)}/></label>
      <select aria-label="筛选项目" value={project} onChange={e=>setProject(e.target.value)}><option value="all">所有项目</option><option value="none">未归属项目</option>{controller.page.projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select>
      <button type="button" aria-pressed={archived} onClick={()=>setArchived(value=>!value)}>{archived?'查看日常事项':'已归档'}</button>
      <button type="button" onClick={()=>openSchedule()}>日程安排</button>
      <button type="button" disabled={controller.loading} onClick={()=>void refresh()}>{controller.loading?'读取中…':'刷新核对'}</button><span className="wb-sort">优先级 · 截止日期</span></div>
    {(controller.error||formError)&&<p className="wb-error" role="alert">{controller.error||formError}</p>}
    {[...controller.uncertain].map(([key,record])=><div className="wb-uncertain" role="alert" key={key}><div><strong>保存结果未确认</strong><p>{record.message}</p><small>操作编号：{record.action.request_id}</small></div><div><button type="button" onClick={()=>void refresh()} disabled={controller.loading}>重新读取核对</button><button type="button" onClick={()=>void retry(key)} disabled={controller.pending.has(key)}>手动重试原操作</button></div></div>)}
    {!controller.loaded&&controller.loading&&<p role="status">正在读取事项…</p>}
    <DndContext sensors={dragSensors} collisionDetection={collision}
      accessibility={{screenReaderInstructions:{draggable:'按空格抓起，方向键移动，空格放下，Escape 取消。也可使用卡片底部的移至菜单。'},announcements:{
        onDragStart:()=> '已抓起事项。',onDragOver:({over})=>over?`目标：${workStateLabel(String(over.id))}`:'请选择目标列。',
        onDragEnd:({over})=>over?`已放入${workStateLabel(String(over.id))}，正在保存。`:'移动已取消。',onDragCancel:()=> '移动已取消。'}}}
      onDragStart={event=>setDragging(String(event.active.id))} onDragCancel={()=>setDragging(null)}
      onDragEnd={event=>{setDragging(null);if(event.over)move(String(event.active.id),String(event.over.id) as WorkState);}}>
      <div className="wb-columns">{WORK_STATES.map(([state,label])=><Column key={state} state={state} label={label} count={visible.filter(item=>item.state===state).length}>
        {visible.filter(item=>item.state===state).map(item=><ItemCard key={item.id} item={item} controller={controller} onMove={move} onSchedule={openSchedule} onSelect={()=>guardDraft(()=>{setSelected(item.id);setDraft(null);setFormError('');})}/>)}</Column>)}</div>
      <DragOverlay dropAnimation={reduce?null:{duration:180,easing:'ease-out'}}>{dragging&&controller.items.find(i=>i.id===dragging)?<ItemCard item={controller.items.find(i=>i.id===dragging)!} controller={controller} onSelect={()=>{}} onMove={move} onSchedule={openSchedule} overlay/>:null}</DragOverlay>
    </DndContext>
    <footer className="wb-footer"><span>{visible.length} 件{archived?'已归档':''}事项 · 四阶段工作流</span><span role="status" aria-live="polite">{controller.notice}</span></footer>
    {drawer&&<><div className="wb-shade" onClick={close}/><aside className="wb-detail" role="dialog" aria-modal="true" aria-label={draft?'编辑事项':'事项详情'} ref={formRoot}
      onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();if(leaveWork)setLeaveWork(null);else close();}if(event.key==='Tab'){const nodes=[...(leaveRoot.current||event.currentTarget).querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href]')];const first=nodes[0],last=nodes[nodes.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}}}>
      <header className="wb-detail-header"><span>{draft?'编辑事项':item?.archived?'已归档事项':'事项详情'}</span><button type="button" aria-label="关闭事项详情" onClick={close}>×</button></header>
      {draft?<form onSubmit={event=>void save(event)}><label>事项标题<input aria-label="事项标题" value={draft.title} maxLength={120} disabled={fieldBusy} onChange={e=>setDraft({...draft,title:e.target.value})}/></label>
        <div className="wb-detail-fields"><label>所属项目<select aria-label="事项所属项目" disabled={fieldBusy} value={draft.project_id} onChange={e=>setDraft({...draft,project_id:e.target.value})}><option value="">未归属项目</option>{draft.project_id&&!controller.page.projects.some(p=>p.id===draft.project_id)&&<option value={draft.project_id}>原项目已移除，请重新选择</option>}{controller.page.projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          <label>优先级<select aria-label="事项优先级" disabled={fieldBusy} value={draft.priority} onChange={e=>setDraft({...draft,priority:e.target.value as WorkFields['priority']})}>{Object.entries(priorities).map(([key,name])=><option key={key} value={key}>{name}</option>)}</select></label>
          <label>截止日期<input type="date" aria-label="事项截止日期" value={draft.due} disabled={fieldBusy} onChange={e=>setDraft({...draft,due:e.target.value})}/></label></div>
        <label>事项说明<textarea aria-label="事项说明" value={draft.description} maxLength={8000} rows={7} disabled={fieldBusy} onChange={e=>setDraft({...draft,description:e.target.value})}/></label>
        <p className="wb-detail-hint">截止日表示最晚完成日；实际工作的时间在事项详情中安排。</p>
        {formError&&<p role="alert" className="wb-error">{formError}</p>}
        <div className="wb-detail-actions"><button className="wb-primary" disabled={fieldBusy} type="submit">{fieldBusy?'等待保存或核对…':'保存事项'}</button><button type="button" onClick={()=>guardDraft(()=>setDraft(null))}>取消</button></div>
      </form>:item&&<><h2>{item.title}</h2><div className="wb-detail-info"><span>状态</span><select aria-label="事项状态" value={item.state} disabled={item.archived||controller.uncertain.has(item.id)} onChange={e=>void controller.move(item.id,e.target.value as WorkState)}>{WORK_STATES.map(([id,label])=><option value={id} key={id}>{label}</option>)}</select><span>所属项目</span><strong>{item.project_name||'未归属项目'}</strong><span>优先级</span><strong>{priorities[item.priority]}优先级</strong><span>截止日期</span><strong>{item.due||'未设置'}</strong></div>
        {item.removed_project&&<p className="wb-removed">原项目已移除：{item.removed_project}。可重新关联。</p>}
        <section className="wb-detail-section"><h3>事项说明</h3>{item.description?<Markdown text={item.description}/>:<p>暂未添加说明。</p>}</section>
        <section className="wb-detail-section"><div className="wb-section-heading"><h3>时间安排</h3><button type="button" onClick={()=>openSchedule(item)}>{item.archived?'查看安排':'安排时间'}</button></div><p>{item.schedules?.unchecked?'有安排尚未完成核对，请打开日程读取确认。':item.schedules?.events.length?`已关联 ${item.schedules.events.length} 场日程。`:'尚未关联日程。'}</p></section>
        <section className="wb-detail-section"><div className="wb-section-heading"><h3>关联 AI 执行</h3><button type="button" onClick={()=>setLinking(!linking)}>{linking?'收起选择':'＋ 关联记录'}</button></div>
          {linking&&<div className="wb-link-picker"><label>选择执行记录<select aria-label="选择执行记录" value={runId} onChange={e=>setRunId(e.target.value)}><option value="">同项目记录优先排列</option>{candidates.map(run=><option value={run.id} key={run.id}>{run.project_name} / {run.agent} · {run.title}</option>)}</select></label><button type="button" disabled={!runId||controller.pending.has(item.id)||controller.uncertain.has(item.id)} onClick={()=>void controller.change({type:'link',id:item.id,run_id:runId}).then(ok=>{if(ok){setRunId('');setLinking(false);}})}>确认关联</button></div>}
          {!item.links.length&&<p>尚未关联执行记录。关联不会启动或取消任务。</p>}
          {item.links.map(link=>{const run=controller.page.runs.find(r=>r.id===link.id);return <article className="wb-run" key={link.id}><header><strong>{run?.title||link.title}</strong><button type="button" aria-label={`解除关联 ${link.title}`} disabled={controller.pending.has(item.id)||controller.uncertain.has(item.id)} onClick={()=>void controller.change({type:'unlink',id:item.id,run_id:link.id})}>解除</button></header>
            <p>{run?`${run.agent} · ${runStateLabel(run)}`:'原执行记录已失效'}</p><small>关联时项目：{link.project_name} · {run?.conversation_title||'原对话记录'}</small>
            {run&&<><p>{run.error||run.status}</p>{run.answer&&<details><summary>查看执行成果</summary><Markdown text={run.answer}/></details>}<button type="button" onClick={()=>void openRun(run.conversation)}>回到来源对话</button></>}
          </article>;})}
        </section>{detailError&&<p role="alert" className="wb-error">{detailError}</p>}
        <div className="wb-detail-actions"><button type="button" disabled={controller.pending.has(item.id)||controller.uncertain.has(item.id)} onClick={()=>{const next={id:item.id,version:item.version,title:item.title,description:item.description,project_id:item.project_id,priority:item.priority,due:item.due};draftBase.current=JSON.stringify(next);setDraft(next);setFormError('');}}>编辑事项</button>
          <button type="button" disabled={controller.pending.has(item.id)||controller.uncertain.has(item.id)} onClick={()=>void controller.change({type:'archive',id:item.id,archived:!item.archived})}>{item.archived?'恢复事项':'归档事项'}</button></div>
        <p className="wb-detail-hint">业务进度由你确认。归档保留内容和关联，不停止 AI 执行。</p>
      </>}
      {leaveWork&&<div className="sc-leave" role="alertdialog" aria-modal="true" aria-label="未保存的事项" ref={leaveRoot}><h3>事项有未保存修改</h3><button type="button" disabled={fieldBusy} onClick={()=>void save({preventDefault(){}} as React.FormEvent).then(ok=>{if(ok){const next=leaveWork;setLeaveWork(null);next();}})}>保存并继续</button><button type="button" disabled={controller.pending.has(draft?.id||'create')} onClick={()=>{const next=leaveWork;setDraft(null);setLeaveWork(null);next();}}>放弃修改并继续</button><button type="button" onClick={()=>setLeaveWork(null)}>继续编辑</button></div>}
    </aside></>}
    {scheduleTarget&&<SchedulePane key={scheduleTarget.item_id||'all'} info={info} debug={debug} transport={demo?.schedule} target={scheduleTarget} onClose={()=>setScheduleTarget(null)} onReturn={returnToItem} onChange={()=>void controller.load()}/>}
  </div>;
}
