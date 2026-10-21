import { useEffect, useMemo, useReducer, useRef, useState, type KeyboardEvent } from 'react';
import FullCalendar from '@fullcalendar/react';
import timeGridPlugin from '@fullcalendar/timegrid';
import interactionPlugin, { type EventResizeDoneArg } from '@fullcalendar/interaction';
import luxonPlugin from '@fullcalendar/luxon3';
import zhLocale from '@fullcalendar/core/locales/zh-cn';
import type { EventDropArg, EventInput } from '@fullcalendar/core';
import { useReducedMotion } from 'framer-motion';
import { rpc, type BackendInfo } from './api';
import { DateTimeField } from './datetime-field';
import { usePageActive } from './page-stack';
import { absoluteTime, addCalendarDays, beijingDay, beijingStamp, calendarKey, calendarWeekStart, eventHealth, eventTime,
  type CalendarEvent } from './schedule-state';
import { ScheduleWeekController, dragTimeDraft, pointerTimeDraft, minutesInDay, overlappingEvents, timeEditable, validTimeDraft,
  type TimeDraft, type WeekTransport } from './schedule-week-state';
import type { ScheduleTarget } from './schedule';
import './schedule-week.css';

type Props={info:BackendInfo|null;debug?:boolean;transport?:WeekTransport;target:ScheduleTarget;revision?:number;
  onClose:()=>void;onReturn:(id:string)=>void;onDay:(target:ScheduleTarget)=>void;onChange:()=>void;view?:'week'|'day';closeLabel?:string;onNew?:(day:string)=>void;onNavigationGuard?:(guard:((next:()=>void)=>void)|null)=>void};
type Form={key:string;start:string;end:string};
type KeyboardDraft={event:CalendarEvent;draft:TimeDraft};
const sameDraft=(event:CalendarEvent,draft:TimeDraft)=>Date.parse(event.start)===Date.parse(draft.start)&&Date.parse(event.end)===Date.parse(draft.end);
const safeDay=(event:CalendarEvent,fallback:string)=>{try{return event.start.includes('T')?beijingDay(event.start):event.start||fallback;}catch{return fallback;}};
function annotateEvent(element:HTMLElement,source:CalendarEvent){
  element.setAttribute('aria-label',`${source.summary}，${eventTime(source)}。空格开始键盘调整，方向键移动，Enter保存，Escape取消；或使用下方时间表单。`);
  element.title=source.summary+'\n'+eventTime(source);
}

export function ScheduleWeekPane({info,debug=false,transport,target,revision,onClose,onReturn,onDay,onChange,view='week',closeLabel='返回工作看板',onNew,onNavigationGuard}:Props){
  const active=usePageActive(),reduce=useReducedMotion();
  const root=useRef<HTMLElement|null>(null),calendar=useRef<FullCalendar|null>(null),details=useRef<HTMLElement|null>(null);
  const [day,setDay]=useState(target.day||beijingDay()),[fullDay,setFullDay]=useState(false),[narrow,setNarrow]=useState(false);
  const [selected,setSelected]=useState(target.event_id&&target.calendar_id?calendarKey(target as {calendar_id:string;event_id:string}):'');
  const [form,setForm]=useState<Form|null>(null),[picker,setPicker]=useState<'start'|'end'|null>(null),[formError,setFormError]=useState('');
  const [leave,setLeave]=useState<null|(()=>void)>(null),[keyboard,setKeyboard]=useState<KeyboardDraft|null>(null),[announcement,setAnnouncement]=useState('');
  const [pointerCancelled,setPointerCancelled]=useState(false),[locateCount,setLocateCount]=useState(0);
  const cancelPointer=useRef(false),pointerActive=useRef(false),keyboardRef=useRef(keyboard);keyboardRef.current=keyboard;
  const locateInitial=useRef(!!target.event_id),changes=useRef(onChange);changes.current=onChange;
  const [version,redraw]=useReducer(value=>value+1,0);
  const controller=useMemo(()=>new ScheduleWeekController(transport||((method,args)=>{
    if(!info)throw Error('后端尚未连接。');return rpc(info,method,args,{timeoutMs:120000});
  }),target.day||beijingDay(),typeof window!=='undefined'?window.localStorage:undefined),[transport,info?.port,info?.token]);
  const monday=calendarWeekStart(day),days=Array.from({length:7},(_,i)=>addCalendarDays(monday,i));
  const event=controller.event(selected),owner=controller.page.links.find(link=>calendarKey(link.event)===selected);
  const item=controller.page.items.find(i=>i.id===owner?.item_id);
  const busy=controller.pending.has(selected),blocked=controller.blocked(selected),checking=controller.checking.has(selected);
  const initialForm=event&&event.start.includes('T')&&Number.isFinite(Date.parse(event.start))&&Number.isFinite(Date.parse(event.end))?{key:selected,start:beijingStamp(event.start),end:beijingStamp(event.end)}:null;
  const dirty=!!form&&!!initialForm&&(form.start!==initialForm.start||form.end!==initialForm.end||form.key!==initialForm.key);
  useEffect(()=>{onNavigationGuard?.(guard);return()=>onNavigationGuard?.(null);},[onNavigationGuard,dirty,form,selected]);
  useEffect(()=>{if(!active)return;const before=(event:Event)=>{if(dirty){event.preventDefault();guard((event as CustomEvent<{navigate:()=>void}>).detail.navigate);}};window.addEventListener?.('tebb-before-navigate',before);return()=>window.removeEventListener?.('tebb-before-navigate',before);},[active,dirty,form,selected]);
  useEffect(()=>controller.subscribe(redraw),[controller]);
  useEffect(()=>{if(!active)return;let live=true;void(async()=>{await controller.read(day);if(live)await controller.read(day,true);})();
    const timer=window.setInterval(()=>{if(!controller.loading)void controller.read(day);},5000);return()=>{live=false;window.clearInterval(timer);};},[active,controller,monday]);
  useEffect(()=>{if(active&&revision!==undefined)void controller.read(day);},[active,controller,revision]);
  useEffect(()=>{if(!root.current||typeof ResizeObserver==='undefined')return;const observer=new ResizeObserver(entries=>setNarrow(entries[0].contentRect.width<680));observer.observe(root.current);return()=>observer.disconnect();},[]);
  useEffect(()=>{calendar.current?.getApi().changeView(narrow||view==='day'?'timeGridDay':'timeGridWeek',day);},[day,narrow,view]);
  useEffect(()=>{root.current?.querySelectorAll<HTMLElement>('[data-week-event]').forEach(element=>{
    const source=controller.event(element.dataset.weekEvent||'');if(source)annotateEvent(element,source);
  });},[controller,version,day,narrow]);
  useEffect(()=>{if(!active)return;calendar.current?.getApi().updateSize();},[active,narrow]);
  useEffect(()=>{if(!selected&&controller.page.events.length){const first=target.event_id?controller.page.events.find(e=>e.event_id===target.event_id):controller.page.events.find(e=>e.eligible)||controller.page.events[0];if(first)setSelected(calendarKey(first));}},[selected,controller.page.events,target.event_id]);
  useEffect(()=>{
    if(!active||!selected)return;
    const source=controller.event(selected)||((target.calendar_id&&target.event_id)?{calendar_id:target.calendar_id,event_id:target.event_id}:null);
    if(!source)return;let live=true;
    void controller.inspect(source).then(()=>{const current=controller.event(selected);if(live&&current&&locateInitial.current){locateInitial.current=false;setDay(safeDay(current,day));if(current.start.includes('T')&&(minutesInDay(current.start)<480||minutesInDay(current.end)>1200))setFullDay(true);setLocateCount(n=>n+1);}});
    return()=>{live=false;};
  },[active,controller,selected,controller.page.checked_at]);
  useEffect(()=>{if(!locateCount)return;const id=window.requestAnimationFrame?.(()=>{const element=[...(root.current?.querySelectorAll<HTMLElement>('[data-week-event]')||[])].find(node=>node.dataset.weekEvent===selected);element?.scrollIntoView?.({block:'nearest',behavior:reduce?'instant':'smooth'});element?.focus({preventScroll:true});});return()=>{if(id!==undefined)window.cancelAnimationFrame?.(id);};},[locateCount,fullDay,narrow,day,reduce]);
  useEffect(()=>{if(!leave)return;root.current?.querySelector<HTMLElement>('[role=alertdialog] button')?.focus();},[!!leave]);
  useEffect(()=>{
    if(!active)return;
    // FullCalendar's pointer capture can leave focus outside the calendar root.
    const cancel=(e:globalThis.KeyboardEvent)=>{if(e.key==='Escape'&&pointerActive.current){
      cancelPointer.current=true;setPointerCancelled(true);setAnnouncement('已取消拖动，未发送修改。');e.preventDefault();
    }};
    document.addEventListener?.('keydown',cancel,true);return()=>document.removeEventListener?.('keydown',cancel,true);
  },[active]);
  function guard(next:()=>void){if(dirty)setLeave(()=>next);else {setForm(null);setPicker(null);setKeyboard(null);next();}}
  function choose(key:string){if(key!==selected)guard(()=>{setSelected(key);setFormError('');});}
  function focusDetails(){details.current?.scrollIntoView?.({block:'nearest',behavior:reduce?'instant':'smooth'});details.current?.querySelector<HTMLElement>('button,input')?.focus();}
  function locate(source:CalendarEvent){guard(()=>{setSelected(calendarKey(source));setDay(safeDay(source,day));if(source.start.includes('T')&&(minutesInDay(source.start)<480||minutesInDay(source.end)>1200))setFullDay(true);setLocateCount(n=>n+1);});}
  async function apply(source:CalendarEvent,draft:TimeDraft){
    const invalid=validTimeDraft(source,draft);if(invalid){setAnnouncement(invalid+' 未保存。');return false;}
    if(sameDraft(source,draft)){setAnnouncement('时间未变化，未发送修改。');return true;}
    const overlaps=overlappingEvents(controller.page.events,source,draft);
    setAnnouncement((overlaps.length?`与已读的 ${overlaps.length} 场日程重叠；`:'')+'正在保存时间。');
    const ok=await controller.retime(source,draft);changes.current();
    if(ok){setForm(null);setPicker(null);setDay(beijingDay(draft.start));setAnnouncement(overlaps.length?`已保存；与已读的 ${overlaps.length} 场日程重叠，未阻止安排。`:'时间已保存。');}
    else setAnnouncement('本轮未完整确认，请按页面实际阶段读取核对。');
    return ok;
  }
  function changed(arg:EventDropArg|EventResizeDoneArg){
    if(cancelPointer.current){arg.revert();return;}
    const source=arg.oldEvent.extendedProps.source as CalendarEvent;
    if(!arg.event.start||!arg.event.end||arg.event.allDay){arg.revert();return;}
    const draft=pointerTimeDraft(source,{start:arg.event.start.toISOString(),end:arg.event.end.toISOString()});
    if(validTimeDraft(source,draft)){arg.revert();setAnnouncement('此位置不符合时间规则，未保存。');return;}
    void apply(source,draft);
  }
  function startPointer(){cancelPointer.current=false;pointerActive.current=true;setPointerCancelled(false);setKeyboard(null);}
  function stopPointer(){window.setTimeout(()=>{pointerActive.current=false;cancelPointer.current=false;setPointerCancelled(false);},0);}
  function keyboardMove(e:KeyboardEvent<HTMLElement>){
    if(e.key==='Escape'){
      if(pointerActive.current){cancelPointer.current=true;setPointerCancelled(true);setAnnouncement('已取消拖动，未发送修改。');e.preventDefault();e.stopPropagation();return;}
      if(keyboardRef.current){setKeyboard(null);setAnnouncement('已取消键盘调整，未发送修改。');e.preventDefault();e.stopPropagation();return;}
      if(picker){setPicker(null);e.stopPropagation();return;}
    }
    const node=(e.target as HTMLElement).closest<HTMLElement>('[data-week-event]');
    if(!node)return;const source=controller.event(node.dataset.weekEvent||'');if(!source)return;
    if(e.key===' '||e.key==='Enter'){
      e.preventDefault();e.stopPropagation();
      const draft=keyboardRef.current;
      if(draft){setKeyboard(null);void apply(draft.event,draft.draft);}
      else if(timeEditable(source)&&!controller.blocked(calendarKey(source))&&!controller.checking.has(calendarKey(source))&&!dirty){setSelected(calendarKey(source));setKeyboard({event:source,draft:{start:source.start,end:source.end}});setAnnouncement('已抓取。方向键移动，Shift或Alt加上下键调整边缘，Enter保存，Escape取消。');}
      else {choose(calendarKey(source));void controller.inspect(source);focusDetails();}
      return;
    }
    const current=keyboardRef.current;
    if(current&&['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key)){
      e.preventDefault();e.stopPropagation();
      const mode=e.altKey?'start':e.shiftKey?'end':'move';
      const draft=dragTimeDraft({...current.event,...current.draft},mode,e.key==='ArrowUp'?-15:e.key==='ArrowDown'?15:0,e.key==='ArrowLeft'?-1:e.key==='ArrowRight'?1:0);
      if(beijingDay(draft.start)<monday||beijingDay(draft.start)>=addCalendarDays(monday,7)){setAnnouncement('跨周调整请使用日期表单。');return;}
      setKeyboard({...current,draft});setDay(beijingDay(draft.start));setAnnouncement(validTimeDraft(current.event,draft)||`预览 ${beijingStamp(draft.start)} → ${beijingStamp(draft.end)}`);
    }
  }
  const visible=controller.page.events.map(row=>controller.event(calendarKey(row))||row);
  const unknownDate=visible.filter(row=>!row.start||!Number.isFinite(Date.parse(row.start)));
  const outside=fullDay?[]:visible.filter(row=>row.start.includes('T')&&(minutesInDay(row.start)<480||minutesInDay(row.end)>1200||beijingDay(row.start)!==beijingDay(row.end)));
  const events:EventInput[]=visible.filter(row=>!unknownDate.includes(row)).map(source=>{
    const key=calendarKey(source),pending=controller.unknown.get(key),preview=keyboard?.event&&calendarKey(keyboard.event)===key?keyboard.draft:null;
    const timed=source.start.includes('T');
    const shown=preview||(pending?.start&&pending.end?{start:pending.start,end:pending.end}:source);
    const editable=timeEditable(source)&&!controller.blocked(key)&&!controller.checking.has(key)&&!dirty&&!keyboard;
    return {id:key,title:source.summary,start:shown.start,end:timed?shown.end:(source.end>source.start?source.end:undefined),allDay:!timed,
      editable,startEditable:editable,durationEditable:editable,
      classNames:[key===selected?'sw-selected':'',source.eligible?'':'sw-readonly',controller.pending.has(key)?'sw-pending':'',pending&&!controller.pending.has(key)?'sw-unknown':'',preview?'sw-preview':''],
      extendedProps:{source,linked:controller.page.links.some(l=>calendarKey(l.event)===key),pending:controller.pending.has(key),unknown:!!pending&&!controller.pending.has(key)}};
  });
  async function saveForm(){if(!form||!event||form.key!==selected)return false;try{setFormError('');
    return await apply(event,{start:form.start===beijingStamp(event.start)?event.start:absoluteTime(form.start),end:form.end===beijingStamp(event.end)?event.end:absoluteTime(form.end)});
  }catch(error){setFormError(String(error));return false;}}
  function field(which:'start'|'end',value:string){if(initialForm)setForm({...initialForm,...form,[which]:value});}
  return <section className={`schedule-week${pointerCancelled?' sw-cancelled':''}`} ref={root} aria-label="周日程安排" onKeyDownCapture={keyboardMove} data-week-view data-week-version={version}>
    <header className="sw-heading"><div><span className="wb-eyebrow">工作区 / 日程安排</span><h1>把时间留给重要的事。</h1><p>{controller.page.calendar?.summary||'当前飞书用户主日历'} · 北京时间 UTC+8{debug?' · 演示数据，未连接飞书':''}</p></div><div className="sc-actions">{onNew&&<button type="button" className="wb-primary" onClick={()=>guard(()=>onNew(day))}>＋ 新建日程</button>}<button type="button" onClick={()=>guard(onClose)}>{closeLabel}</button></div></header>
    <div className="sw-toolbar"><button type="button" aria-label="上一周" onClick={()=>guard(()=>setDay(addCalendarDays(day,-7)))}>‹</button><button type="button" aria-label="下一周" onClick={()=>guard(()=>setDay(addCalendarDays(day,7)))}>›</button>
      <strong>{monday.replaceAll('-','.')} — {addCalendarDays(monday,6).slice(5).replace('-','.')}</strong><button type="button" onClick={()=>guard(()=>setDay(beijingDay()))}>本周</button>
      <label>定位日期<input type="date" aria-label="周日程定位日期" value={day} onChange={e=>{if(e.target.value)guard(()=>setDay(e.target.value));}}/></label>
      <button type="button" aria-pressed={fullDay} onClick={()=>setFullDay(v=>!v)}>{fullDay?'08:00–20:00':'完整24小时'}</button>
      <button type="button" disabled={controller.loading} onClick={()=>void controller.read(day,true)}>{controller.loading?'读取中…':'读取核对'}</button>
      <button type="button" onClick={()=>guard(()=>onDay({day,item_id:item?.id||target.item_id,...(event?{calendar_id:event.calendar_id,event_id:event.event_id}:{})}))}>{item||target.item_id?'单日 / 新建关联':'单日查看'}</button>
    </div>
    {narrow&&<div className="sw-day-picker" aria-label="本周日期">{days.map((value,i)=><button type="button" key={value} aria-pressed={day===value} onClick={()=>guard(()=>setDay(value))}>{['一','二','三','四','五','六','日'][i]} {value.slice(8)}</button>)}</div>}
    {controller.error&&<p className="sc-error" role="alert">{controller.error}</p>}
    {target.event_id&&controller.page.events_valid&&!controller.loading&&!controller.page.events.some(e=>e.event_id===target.event_id)&&<p className="sc-warning" role="status">当前区间未找到目标日程，请核对日期、访问权限或远端是否已删除；不会用另一场日程替代。</p>}
    {!controller.page.events_valid&&<p className="sc-warning">{controller.page.events_error||'尚未完成本周远端核对。'}{visible.length?' 下方保留上次信息，不能据此判断没有安排。':''}</p>}
    {!!outside.length&&<div className="sw-outside" aria-label="时段外安排"><span>时段外 / 跨日 {outside.length}</span>{outside.map(row=><button type="button" key={calendarKey(row)} onClick={()=>locate(row)}>{row.summary} · {eventTime(row)}</button>)}</div>}
    {!!unknownDate.length&&<div className="sw-outside">{unknownDate.map(row=><button type="button" key={calendarKey(row)} onClick={()=>choose(calendarKey(row))}>{row.summary} · 时间未核实</button>)}</div>}
    {!!controller.unknown.size&&<div className="sw-recovery" aria-label="待核对改期">{[...controller.unknown].map(([key,record])=><section key={key} className="sc-warning"><strong>{controller.pending.has(key)?'日程正在保存':'日程结果未确认'}</strong>
      <p>{controller.event(key)?.summary||record.event_id} · 原时间 {record.original_start} → {record.original_end}</p>{record.start&&<p>拟调整 {record.start} → {record.end}</p>}<small>操作编号：{record.request_id}</small>
      {controller.missing.has(key)&&<p>尚未查到原回执，不能据此认为没有执行。请先读取当前日程，再明确结束等待。</p>}
      <div className="sc-actions"><button type="button" onClick={()=>{setSelected(key);void controller.inspect(record);void controller.read(day);}}>读取此日程及原回执</button>
        {!controller.pending.has(key)&&<button type="button" disabled={!['confirmed','missing','deleted'].includes(controller.event(key)?.verification||'')||controller.checking.has(key)} onClick={()=>void controller.reconcile(key).then(()=>{changes.current();void controller.inspect(record);})}>以当前已核对状态结束等待</button>}</div>
      {!controller.pending.has(key)&&<p>先读当前时间，再明确结束本轮等待；不会重发改期，也不表示原修改一定未执行。</p>}
    </section>)}</div>}
    <div className="sw-live" role="status" aria-live="polite">{announcement||controller.notice|| (checking?'正在核对组织者、权限和完整参与人…':'选择日程后核对编辑资格；普通个人日程可拖动改期。')}</div>
    <div className="sw-calendar"><FullCalendar ref={calendar} plugins={[timeGridPlugin,interactionPlugin,luxonPlugin]} locale={zhLocale} timeZone="Asia/Shanghai"
      firstDay={1} initialView={narrow||view==='day'?'timeGridDay':'timeGridWeek'} initialDate={day} headerToolbar={false} height="auto" allDaySlot allDayText="全天"
      slotMinTime={fullDay?'00:00:00':'08:00:00'} slotMaxTime={fullDay?'24:00:00':'20:00:00'} slotDuration="00:30:00" snapDuration="00:15:00"
      slotLabelInterval="01:00:00" slotLabelFormat={{hour:'2-digit',minute:'2-digit',hour12:false}} eventTimeFormat={{hour:'2-digit',minute:'2-digit',hour12:false}}
      dayHeaderFormat={{weekday:'short',month:'numeric',day:'numeric'}} events={events} editable eventResizableFromStart eventOverlap
      eventDragMinDistance={6} dragRevertDuration={reduce?0:160} dragScroll={!reduce} nowIndicator eventMinHeight={18} eventShortHeight={32}
      eventAllow={(drop,dragged)=>{const source=dragged?.extendedProps.source as CalendarEvent|undefined;return !!source&&!cancelPointer.current&&!drop.allDay&&timeEditable(source)&&!controller.blocked(calendarKey(source))&&!dirty
        &&!validTimeDraft(source,pointerTimeDraft(source,{start:drop.start.toISOString(),end:drop.end.toISOString()}));}}
      eventDragStart={startPointer} eventResizeStart={startPointer} eventDragStop={stopPointer} eventResizeStop={stopPointer} eventDrop={changed} eventResize={changed}
      eventClick={arg=>{arg.jsEvent.preventDefault();choose(arg.event.id);}}
      eventDidMount={arg=>{arg.el.dataset.weekEvent=arg.event.id;arg.el.setAttribute('role','button');arg.el.setAttribute('tabindex','0');const source=arg.event.extendedProps.source as CalendarEvent;
        annotateEvent(arg.el,source);}}
      eventContent={arg=>{const preview=arg.isMirror&&arg.event.start&&arg.event.end?pointerTimeDraft(arg.event.extendedProps.source as CalendarEvent,{start:arg.event.start.toISOString(),end:arg.event.end.toISOString()}):null;
        return <div className="sw-event-content"><b>{arg.event.extendedProps.pending?'保存中 · ':arg.event.extendedProps.unknown?'未核对 · ':arg.event.extendedProps.linked?'↗ ':''}{arg.event.title}</b><span className="sw-event-time">{preview?`${beijingStamp(preview.start).slice(11)} - ${beijingStamp(preview.end).slice(11)}`:arg.timeText}</span></div>;}}/></div>
    <section className="sw-details" ref={details} aria-label="日程时间详情">
      <div className="sw-detail-heading"><h2>{event?.summary||'选择一场日程'}</h2>{event&&<button type="button" onClick={()=>locate(event)}>定位日程</button>}{item&&<button type="button" onClick={()=>guard(()=>onReturn(item.id))}>↗ {item.title}{item.archived?' · 已归档':''}</button>}</div>
      {event&&<><p className="sc-caption">{eventTime(event)} · {eventHealth(event)}{checking?' · 编辑资格核对中':!timeEditable(event)?' · '+(event.edit_reason||'请重新核对编辑资格'):''}</p>
        <div className="sc-actions"><button type="button" disabled={checking} onClick={()=>void controller.inspect(event)}>核对编辑资格</button>
          <button type="button" disabled={!timeEditable(event)||blocked||checking} onClick={()=>{setKeyboard(null);setForm(initialForm);setFormError('');setPicker(null);focusDetails();}}>修改时间</button>
          <button type="button" onClick={()=>guard(()=>onDay({day:safeDay(event,day),calendar_id:event.calendar_id,event_id:event.event_id,item_id:item?.id||target.item_id}))}>单日查看 / 关联</button></div>
        {timeEditable(event)&&!blocked&&<p className="sw-keyboard-help">聚焦时间块后：空格抓取，方向键移动；Shift + 上下调整结束，Alt + 上下调整开始；Enter保存，Escape取消。</p>}
      </>}
      {form&&event&&form.key===selected&&<form className="sw-time-form" onSubmit={e=>{e.preventDefault();void saveForm();}}>
        <div className="sw-fields"><span>开始</span><DateTimeField portalRoot={root.current} marker="start" placeholder="开始时间" value={form.start} disabled={blocked} open={picker==='start'} onOpen={()=>setPicker('start')} onClose={()=>setPicker(null)} onChange={value=>field('start',value)}/>
          <span>结束</span><DateTimeField portalRoot={root.current} marker="end" placeholder="结束时间" value={form.end} disabled={blocked} open={picker==='end'} onOpen={()=>setPicker('end')} onClose={()=>setPicker(null)} onChange={value=>field('end',value)}/></div>
        {formError&&<p className="sc-error" role="alert">{formError}</p>}
        <div className="sc-actions"><button type="submit" className="wb-primary" disabled={!dirty||blocked}>保存时间</button><button type="button" disabled={busy} onClick={()=>{setForm(null);setPicker(null);setFormError('');}}>放弃表单</button></div>
      </form>}
    </section>
    {leave&&<div className="sc-leave" role="alertdialog" aria-label="未保存的日程时间"><h3>日程时间尚未保存</h3><button type="button" disabled={blocked} onClick={()=>void saveForm().then(ok=>{if(ok){const next=leave;setLeave(null);next();}})}>保存并继续</button><button type="button" disabled={busy} onClick={()=>{const next=leave;setForm(null);setPicker(null);setLeave(null);next();}}>放弃并继续</button><button type="button" onClick={()=>setLeave(null)}>继续编辑</button></div>}
  </section>;
}
