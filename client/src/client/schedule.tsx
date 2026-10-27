import { useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { rpc, type BackendInfo } from './api';
import { DateTimeField } from './datetime-field';
import { usePageActive } from './page-stack';
import { ScheduleController, absoluteTime, beijingDay, beijingStamp, eventHealth, eventTime, unresolved,
  type CalendarEvent, type ScheduleAction, type SchedulePage, type ScheduleTransport } from './schedule-state';
import './schedule.css';

export type ScheduleTarget={item_id?:string;calendar_id?:string;event_id?:string;day?:string;create?:boolean};
type Draft={summary:string;start:string;end:string};
type Props={info:BackendInfo|null;debug?:boolean;transport?:ScheduleTransport;target:ScheduleTarget;onClose:()=>void;onReturn:(id:string)=>void;onWeek?:(target:ScheduleTarget)=>void;onChange:()=>void};
const blank=(title:string,day:string):Draft=>{const start=day===beijingDay()?beijingStamp(new Date(Math.ceil((Date.now()+30*60000)/300000)*300000).toISOString()):day+'T09:00';return {summary:title,start,end:beijingStamp(new Date(new Date(absoluteTime(start)).getTime()+3600000).toISOString())};};
const key=(event:CalendarEvent)=>event.calendar_id+'/'+event.event_id;

export function SchedulePane({info,debug=false,transport,target,onClose,onReturn,onWeek,onChange}:Props){
  const active=usePageActive();const root=useRef<HTMLElement|null>(null),leaveRoot=useRef<HTMLDivElement|null>(null);
  const [day,setDay]=useState(target.day||beijingDay()),[draft,setDraft]=useState<Draft|null>(null),[picker,setPicker]=useState<'start'|'end'|null>(null);
  const [selection,setSelection]=useState(target.event_id?`${target.calendar_id}/${target.event_id}`:''),[formError,setFormError]=useState('');
  const [leave,setLeave]=useState<null|(()=>void)>(null),[confirmedAbsent,setConfirmedAbsent]=useState(false);
  const draftBase=useRef('');
  const initialLocate=useRef(!!target.event_id);
  const changes=useRef(onChange);changes.current=onChange;
  const controller=useMemo(()=>new ScheduleController(transport||(async(method,args)=>{
    if(!info)throw Error('后端尚未连接。');return rpc<SchedulePage>(info,method,args,{timeoutMs:120000});
  }),target.item_id||'',target.day||beijingDay(),typeof window!=='undefined'?window.localStorage:undefined),[transport,info?.port,info?.token,target.item_id]);
  const [,redraw]=useReducer(value=>value+1,0);
  useEffect(()=>controller.subscribe(redraw),[controller]);
  useEffect(()=>{if(!active)return;let live=true;void(async()=>{await controller.read(day);if(live){const checked=await controller.read(day,true);if(live){changes.current();if(checked&&initialLocate.current){initialLocate.current=false;const event=controller.page.links.find(link=>key(link.event)===`${target.calendar_id}/${target.event_id}`)?.event;if(event?.verification==='confirmed'&&event.start.includes('T')){setDay(beijingDay(event.start));setSelection(key(event));}}}}})();
    const timer=window.setInterval(()=>{if(!controller.loading&&!controller.pending)void controller.read(day);},5000);return()=>{live=false;window.clearInterval(timer);};},[active,controller,day]);
  useEffect(()=>{const previous=document.activeElement as HTMLElement|null;root.current?.querySelector<HTMLElement>('button')?.focus();return()=>{if(previous?.isConnected)previous.focus();};},[]);
  useEffect(()=>{if(leave)leaveRoot.current?.querySelector<HTMLElement>('button')?.focus();},[!!leave]);
  useEffect(()=>{if(selection)root.current?.querySelector('[data-calendar-selected=true]')?.scrollIntoView?.({block:'nearest',behavior:'instant'});},[selection,controller.page.events]);
  const item=controller.page.items.find(i=>i.id===target.item_id);
  const links=controller.page.links.filter(link=>!target.item_id||link.item_id===target.item_id);
  const operations=controller.page.operations.filter(op=>op.item_id===target.item_id&&unresolved(op)&&op.type==='create');
  const blocked=controller.pending||!!controller.unknown;
  const selected=controller.page.events.find(e=>key(e)===selection)||links.find(l=>key(l.event)===selection)?.event;
  function guard(next:()=>void){if(draft&&JSON.stringify(draft)!==draftBase.current){setLeave(()=>next);setPicker(null);}else next();}
  async function refresh(){await controller.read(day,true);changes.current();}
  async function apply(action:Omit<ScheduleAction,'request_id'|'item_id'|'version'|'day'>){
    if(!item)return false;
    const ok=await controller.change({...action,item_id:item.id,version:item.version,day});changes.current();return ok;
  }
  async function save(){
    if(!draft)return false;setFormError('');
    try{
      if(!draft.summary.trim())throw Error('请输入安排标题。');
      const start=absoluteTime(draft.start),end=absoluteTime(draft.end);
      if(new Date(start)>=new Date(end))throw Error('结束时间必须晚于开始时间。');
      const ok=await apply({type:'create',summary:draft.summary,start,end});
      if(ok){setDraft(null);setPicker(null);const event=controller.page.operation?.event;if(event){setSelection(key(event));setDay(beijingDay(event.start));}}
      return ok;
    }catch(error){setFormError(String(error));return false;}
  }
  function chooseDate(next:string){guard(()=>{initialLocate.current=false;setDraft(null);setDay(next);setSelection('');});}
  function startDraft(){if(!item)return;if(onWeek){guard(()=>onWeek({day,item_id:item.id,create:true}));return;}guard(()=>{initialLocate.current=false;const next=blank(item.title,day);draftBase.current=JSON.stringify(next);setDraft(next);setFormError('');setPicker(null);});}
  function updateStart(start:string){if(!draft)return;const duration=Math.max(5,(new Date(absoluteTime(draft.end)).getTime()-new Date(absoluteTime(draft.start)).getTime())/60000);
    setDraft({...draft,start,end:beijingStamp(new Date(new Date(absoluteTime(start)).getTime()+duration*60000).toISOString())});}
  function duration(minutes:number){if(draft)setDraft({...draft,end:beijingStamp(new Date(new Date(absoluteTime(draft.start)).getTime()+minutes*60000).toISOString())});}
  const durationValue=draft?(new Date(absoluteTime(draft.end)).getTime()-new Date(absoluteTime(draft.start)).getTime())/60000:60;
  function backToItem(id:string){guard(()=>{setDraft(null);onReturn(id);});}
  return <><div className="wb-shade" onClick={()=>{if(!controller.pending)guard(onClose);}}/><aside className="schedule-pane" role="dialog" aria-modal="true" aria-label="日程安排" ref={root}
    onKeyDown={event=>{if(event.key==='Escape'){event.stopPropagation();if(leave)setLeave(null);else if(picker)setPicker(null);else if(!controller.pending)guard(onClose);}
      if(event.key==='Tab'){const scope=leaveRoot.current||root.current;const nodes=[...(scope?.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled]),a[href]')||[])];const first=nodes[0],last=nodes[nodes.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}}}>
    <header className="sc-header"><div><span className="sc-eyebrow">时间安排 · 北京时间 UTC+8</span><h2>{item?item.title:'日程安排'}</h2></div><button type="button" aria-label="关闭日程安排" disabled={controller.pending} onClick={()=>guard(onClose)}>×</button></header>
    <p className="sc-caption">{controller.page.calendar?.summary||'当前飞书用户主日历'}{debug?' · 演示数据，未连接飞书':''}</p>
    {onWeek&&<button type="button" onClick={()=>guard(()=>onWeek({day,item_id:target.item_id,...(selected?{calendar_id:selected.calendar_id,event_id:selected.event_id}:{})}))}>打开周视图</button>}
    <div className="sc-date"><label>查看日期<input type="date" aria-label="查看日程日期" value={day} disabled={controller.pending} onChange={e=>{if(e.target.value)chooseDate(e.target.value);}}/></label><button type="button" disabled={controller.loading||controller.pending} onClick={()=>void refresh()}>{controller.loading?'核对中…':'读取核对'}</button></div>
    {controller.error&&<p role="alert" className="sc-error">{controller.error}</p>}
    {controller.unknown&&<div className="sc-warning" role="alert"><strong>请求结果未确认</strong><p>操作编号：{controller.unknown.request_id}</p><p>保留输入，先读取核对。原请求可能仍在后台执行。</p><button type="button" disabled={controller.loading||controller.pending} onClick={()=>void refresh()}>按原编号核对</button></div>}
    {controller.missingReceipt&&controller.unknown&&item&&<section className="sc-warning" aria-label="核对未登记请求"><p>尚未查到服务端记录。核对飞书后明确选择；结束原编号可防止迟到的请求再次执行。</p>
      {selected?.eligible&&<button type="button" disabled={controller.pending||item.archived} onClick={()=>void controller.reconcile({type:'resolve',item_id:item.id,version:item.version,day,operation_id:controller.unknown!.request_id,resolution:'link',calendar_id:selected.calendar_id,event_id:selected.event_id}).then(()=>changes.current())}>确认已创建，关联所选日程</button>}
      <label className="sc-check"><input type="checkbox" checked={confirmedAbsent} onChange={e=>setConfirmedAbsent(e.target.checked)}/>{controller.unknown.type==='create'?'我已核对飞书，确认原请求没有创建有效日程':'我已核对关联现状，确认可以结束原编号'}</label>
      <button type="button" disabled={!confirmedAbsent||controller.pending} onClick={()=>void controller.reconcile({type:'resolve',item_id:item.id,version:item.version,day,operation_id:controller.unknown!.request_id,resolution:'absent',confirmed:true}).then(ok=>{changes.current();if(ok){setDraft(null);setConfirmedAbsent(false);}})}>确认结束未登记请求</button>
    </section>}
    {operations.map(operation=><section className="sc-warning" key={operation.id} aria-label="待核对安排"><strong>{operation.event?'飞书已有创建结果，关联尚未完整确认':'创建结果未确认'}</strong><p>{operation.error||'后台可能仍在执行，先核对飞书。'}</p><small>操作编号：{operation.id}</small>
      <p>{operation.event?eventTime(operation.event):`${operation.summary||''} ${operation.start||''} → ${operation.end||''}`}</p>
      {operation.event?<button type="button" disabled={blocked||item?.archived} onClick={()=>void apply({type:'resolve',operation_id:operation.id,resolution:'link',calendar_id:operation.event!.calendar_id,event_id:operation.event!.event_id}).then(ok=>{if(ok)setDraft(null);})}>核对此日程并完成关联</button>:<p>在下方选择已经创建的那场日程，再明确完成关联。</p>}
      {selected?.eligible&&!operation.event&&<button type="button" disabled={blocked||item?.archived} onClick={()=>void apply({type:'resolve',operation_id:operation.id,resolution:'link',calendar_id:selected.calendar_id,event_id:selected.event_id}).then(ok=>{if(ok)setDraft(null);})}>确认用所选日程解决本次操作</button>}
      <label className="sc-check"><input type="checkbox" checked={confirmedAbsent} onChange={e=>setConfirmedAbsent(e.target.checked)}/>我已核对飞书，确认本次没有有效日程</label>
      <button type="button" disabled={!confirmedAbsent||blocked} onClick={()=>void apply({type:'resolve',operation_id:operation.id,resolution:'absent',confirmed:true}).then(ok=>{if(ok){setDraft(null);setConfirmedAbsent(false);}})}>确认结束本次操作</button>
    </section>)}
    {item&&<div className="sc-actions"><button type="button" className="wb-primary" disabled={item.archived||blocked||!!operations.length||controller.page.calendar?.writable===false} onClick={startDraft}>＋ 新建并关联安排</button><button type="button" onClick={()=>backToItem(item.id)}>返回业务事项</button></div>}
    {item?.archived&&<p className="sc-caption">事项已归档，恢复后可以新增或调整关联。</p>}
    {draft&&<form className="sc-form" onSubmit={event=>{event.preventDefault();void save();}}>
      <label>安排标题<input aria-label="安排标题" maxLength={120} value={draft.summary} disabled={blocked||!!operations.length} onChange={e=>setDraft({...draft,summary:e.target.value})}/></label>
      <div className="sc-times"><span>开始</span><DateTimeField portalRoot={root.current} marker="start" placeholder="开始时间" value={draft.start} disabled={blocked||!!operations.length} open={picker==='start'} onOpen={()=>setPicker('start')} onClose={()=>setPicker(null)} onChange={updateStart}/>
        <span>结束</span><DateTimeField portalRoot={root.current} marker="end" placeholder="结束时间" value={draft.end} disabled={blocked||!!operations.length} open={picker==='end'} onOpen={()=>setPicker('end')} onClose={()=>setPicker(null)} onChange={end=>setDraft({...draft,end})}/></div>
      <label>时长<select aria-label="安排时长" value={[30,60,90,120].includes(durationValue)?String(durationValue):'custom'} disabled={blocked||!!operations.length} onChange={e=>{if(e.target.value!=='custom')duration(Number(e.target.value));}}>{[30,60,90,120].map(m=><option key={m} value={m}>{m} 分钟</option>)}<option value="custom">自定义（按结束时间）</option></select></label>
      <p className="sc-caption">将创建一场单次日程并关联本事项。业务进度和截止日期保持独立。</p>
      {formError&&<p role="alert" className="sc-error">{formError}</p>}
      <div className="sc-actions"><button type="submit" className="wb-primary" disabled={blocked||!!operations.length||item?.archived}>{controller.pending?'提交中…':'确认新建并关联'}</button><button type="button" disabled={controller.pending} onClick={()=>guard(()=>{setDraft(null);setPicker(null);})}>收起表单</button></div>
    </form>}
    <section className="sc-section"><h3>所选日期</h3>{!controller.page.events_valid&&<p className="sc-warning">{controller.page.events_error||'尚未完成远端核对。'}{controller.page.events.length?' 下方为上次读取的信息。':''}</p>}
      {controller.page.events_valid&&!controller.page.events.length&&<p className="sc-caption">这一天没有读到日程。</p>}
      {(controller.page.day===day?controller.page.events:[]).map(event=>{const owner=controller.page.links.find(link=>key(link.event)===key(event));const source=controller.page.items.find(i=>i.id===owner?.item_id);
        return <article className={`sc-event${selection===key(event)?' sc-selected':''}`} data-calendar-selected={selection===key(event)} key={key(event)}><button className="sc-event-title" type="button" onClick={()=>guard(()=>{initialLocate.current=false;setSelection(key(event));setDraft(null);})}>{event.summary}</button><p>{eventTime(event)}</p><small>{eventHealth(controller.page.events_valid?event:{...event,verification:'read_failed'})}{!event.eligible?' · '+event.reason:''}</small>
          {source?<div className="sc-event-link"><span>关联：{source.title}{source.archived?'（已归档）':''}</span><button type="button" onClick={()=>backToItem(source.id)}>定位事项</button></div>:<p className="sc-caption">未关联事项</p>}
          {item&&!owner&&<button type="button" disabled={!event.eligible||item.archived||blocked||!!operations.length} onClick={()=>void apply({type:'link',calendar_id:event.calendar_id,event_id:event.event_id})}>关联到本事项</button>}
          {item&&owner?.item_id===item.id&&<button type="button" disabled={item.archived||blocked} onClick={()=>void apply({type:'unlink',calendar_id:event.calendar_id,event_id:event.event_id})}>解除本地关联</button>}
        </article>;})}
    </section>
    {!!links.length&&<section className="sc-section"><h3>{item?'此事项的全部安排':'已关联安排'}</h3>{links.map(link=><article className="sc-event" key={link.item_id+key(link.event)}><strong>{link.event.summary}</strong><p>{eventTime(link.event)}</p><small>{eventHealth(link.event)}{link.event.error?' · '+link.event.error:''}</small><div className="sc-actions"><button type="button" onClick={()=>guard(()=>{initialLocate.current=false;setDraft(null);setDay(link.event.start.slice(0,10)||day);setSelection(key(link.event));})}>定位日程</button><button type="button" onClick={()=>backToItem(link.item_id)}>定位事项</button>{item&&<button type="button" disabled={item.archived||blocked} onClick={()=>void apply({type:'unlink',calendar_id:link.event.calendar_id,event_id:link.event.event_id})}>解除关联</button>}</div></article>)}</section>}
    <footer className="sc-caption" role="status">{controller.notice}</footer>
    {leave&&<div className="sc-leave" role="alertdialog" aria-modal="true" aria-label="未保存的安排" ref={leaveRoot}><h3>有未保存的安排</h3><p>选择明确创建后继续，或放弃本地表单。已发出的操作仍保留核对记录。</p><button type="button" className="wb-primary" disabled={blocked||!!operations.length} onClick={()=>void save().then(ok=>{if(ok){const next=leave;setLeave(null);next();}})}>确认新建并继续</button><button type="button" disabled={controller.pending} onClick={()=>{const next=leave;setDraft(null);setPicker(null);setLeave(null);next();}}>放弃表单并继续</button><button type="button" onClick={()=>setLeave(null)}>继续编辑</button></div>}
  </aside></>;
}
