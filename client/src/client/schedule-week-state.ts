import { absoluteTime, addCalendarDays, beijingDay, beijingStamp, calendarKey, calendarWeekStart,
  type CalendarEvent, type ScheduleOperation, type ScheduleTimeAction, type ScheduleWeekPage } from './schedule-state';

export type WeekTransport=(method:string,args:Record<string,unknown>)=>Promise<unknown>;
type StorageLike=Pick<Storage,'getItem'|'setItem'|'removeItem'>;
export type TimeMarker=ScheduleTimeAction;
export type TimeDraft={start:string;end:string};
// Calendar API timestamps are whole seconds; keep the existing RPC ISO contract.
export const scheduleWireTime=(iso:string)=>new Date(iso).toISOString().replace(/\.\d{3}Z$/,'Z');
export const timeCompleted=(op?:ScheduleOperation|null)=>!!op&&['retimed','observed','unchanged','not_started'].includes(op.state);
export const timeEditable=(event?:CalendarEvent)=>!!event&&event.editable===true&&event.verification==='confirmed'
  &&new Date(event.end).getTime()>Date.now()&&(event.sync_state==='local'||!!event.edit_checked_at&&Date.now()-new Date(event.edit_checked_at).getTime()<300000);
export const minutesInDay=(iso:string)=>{const s=beijingStamp(iso);return Number(s.slice(11,13))*60+Number(s.slice(14,16));};
export function validTimeDraft(event:CalendarEvent,draft:TimeDraft,now=Date.now()){
  const a=Date.parse(draft.start),b=Date.parse(draft.end),duration=b-a,old=Date.parse(event.end)-Date.parse(event.start);
  if(!Number.isFinite(a)||!Number.isFinite(b)||a>=b)return '结束时间须晚于开始时间。';
  if(beijingDay(draft.start)!==beijingDay(draft.end))return '本批起止须在同一北京时间日期。';
  if(Date.parse(event.end)<=now)return '已结束日程本批只读。';
  if(b<=now)return '调整后的结束时间须晚于当前时间。';
  if(duration<900000&&duration!==old)return '缩放最短15分钟，原有短日程可保持原时长移动。';
  return '';
}
export function dragTimeDraft(event:CalendarEvent,mode:'move'|'start'|'end',minuteDelta:number,days:number):TimeDraft{
  const minute=minutesInDay(mode==='end'?event.end:event.start);
  const snapped=Math.round((minute+minuteDelta)/15)*15;
  const target=Date.parse(absoluteTime(beijingDay(mode==='end'?event.end:event.start)+'T00:00'))+snapped*60000;
  if(mode==='move')return {start:new Date(target+days*86400000).toISOString(),end:new Date(target+days*86400000+Date.parse(event.end)-Date.parse(event.start)).toISOString()};
  return mode==='start'?{start:new Date(target).toISOString(),end:event.end}:{start:event.start,end:new Date(target).toISOString()};
}
export function pointerTimeDraft(event:CalendarEvent,draft:TimeDraft):TimeDraft{
  const startChanged=Date.parse(event.start)!==Date.parse(draft.start),endChanged=Date.parse(event.end)!==Date.parse(draft.end);
  if(!startChanged&&!endChanged)return draft;
  // FullCalendar snaps deltas; align the resulting edge as well for legacy off-grid events.
  return dragTimeDraft({...event,...draft},startChanged&&endChanged?'move':startChanged?'start':'end',0,0);
}
export function overlappingEvents(events:CalendarEvent[],event:CalendarEvent,draft:TimeDraft){
  return events.filter(other=>calendarKey(other)!==calendarKey(event)&&other.status!=='cancelled'&&other.start.includes('T')
    &&Date.parse(other.start)<Date.parse(draft.end)&&Date.parse(other.end)>Date.parse(draft.start));
}

const empty=(day:string):ScheduleWeekPage=>({ok:true,week:true,day,week_start:calendarWeekStart(day),week_end:addCalendarDays(calendarWeekStart(day),7),
  item_id:'',events:[],events_valid:false,events_error:'',checked_at:'',links:[],items:[],operations:[]});
const markerValid=(v:unknown):v is TimeMarker=>{const m=v as TimeMarker;return !!m&&['retime','reconcile_time'].includes(m.type)
  &&typeof m.request_id==='string'&&typeof m.calendar_id==='string'&&typeof m.event_id==='string'&&typeof m.day==='string'
  &&typeof m.original_start==='string'&&typeof m.original_end==='string';};

export class ScheduleWeekController {
  page:ScheduleWeekPage;loading=false;error='';notice='';
  readonly pending=new Map<string,TimeMarker>();readonly unknown=new Map<string,TimeMarker>();
  readonly inspections=new Map<string,CalendarEvent>();readonly checking=new Set<string>();
  readonly missing=new Set<string>();
  private serial=0;private eventSerial=new Map<string,number>();private listeners=new Set<()=>void>();
  private storageDamaged=false;
  private storageKey='tebb.schedule.week.pending.v1';
  private interacting=false;
  constructor(private transport:WeekTransport,day:string,private storage?:StorageLike,private id=()=>crypto.randomUUID()){
    this.page=empty(day);
    try{const saved=JSON.parse(storage?.getItem(this.storageKey)||'[]');if(!Array.isArray(saved))throw Error();
      for(const marker of saved){if(!markerValid(marker))throw Error();this.unknown.set(calendarKey(marker),marker);}}
    catch{this.storageDamaged=true;this.error='本机核对记录读取失败，修改已暂停；请保留本机记录并核对飞书及操作回执。';}
  }
  subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener);};};
  private emit(){this.listeners.forEach(fn=>fn());}
  private persist(){if(this.unknown.size)this.storage?.setItem(this.storageKey,JSON.stringify([...this.unknown.values()]));else this.storage?.removeItem(this.storageKey);}
  beginInteraction(){this.interacting=true;++this.serial;this.loading=false;
    for(const key of this.checking)this.eventSerial.set(key,(this.eventSerial.get(key)||0)+1);this.checking.clear();}
  endInteraction(){this.interacting=false;}
  private adopt(page:ScheduleWeekPage){
    for(const event of page.events){const key=calendarKey(event),old=this.event(key);
      if(old&&Date.parse(event.checked_at)>=Date.parse(old.checked_at)&&
          (event.start!==old.start||event.end!==old.end||event.summary!==old.summary||event.local_version!==old.local_version
            ||event.sync_state!==old.sync_state||event.editable!==old.editable||event.verification!==old.verification)){
        this.inspections.delete(key);this.eventSerial.set(key,(this.eventSerial.get(key)||0)+1);this.checking.delete(key);
      }}
    this.page=page;
    for(const op of page.operations||[]){
      if(op.type!=='retime'||!op.calendar_id||!op.event_id||timeCompleted(op))continue;
      const key=calendarKey(op as Required<Pick<CalendarEvent,'calendar_id'|'event_id'>>);
      if(!this.unknown.has(key)&&op.original_start&&op.original_end)this.unknown.set(key,{type:'retime',request_id:op.id,
        calendar_id:op.calendar_id,event_id:op.event_id,day:page.day,original_start:op.original_start,original_end:op.original_end,start:op.start,end:op.end});
    }
  }
  event(key:string){return this.inspections.get(key)||this.page.events.find(e=>calendarKey(e)===key)
    ||this.page.links.find(l=>calendarKey(l.event)===key)?.event;}
  blocked(key:string){return this.storageDamaged||this.pending.has(key)||this.unknown.has(key);}
  async read(day=this.page.day,refresh=false){
    if(this.interacting)return false;
    const serial=++this.serial;this.loading=true;if(!this.storageDamaged)this.error='';this.emit();
    try{
      const page=await this.transport('load_schedule_week',{day,refresh}) as ScheduleWeekPage;
      if(serial!==this.serial)return false;
      if(!page.ok)throw Error(page.error||'周日程读取失败。');
      this.adopt(page);
      for(const [key,marker] of [...this.unknown]){
        if(this.pending.has(key))continue;
        const checked=await this.transport('load_schedule_week',{day,request_id:marker.request_id,refresh:false}) as ScheduleWeekPage;
        if(serial!==this.serial)return false;
        if(!checked.ok)continue;
        if(checked.week)this.adopt(checked); // Current cache, never the historical operation.event snapshot.
        if(timeCompleted(checked.operation)){this.unknown.delete(key);this.missing.delete(key);
          this.notice=checked.operation?.state==='not_started'?'原请求未开始修改，请核对后重新操作。':'已读取原操作回执。';}
        else if(!checked.operation)this.missing.add(key);
      }
      try{if(!this.storageDamaged)this.persist();}catch{this.notice+=' 本机核对标记保存失败，重开后仍需读取核对。';}
      return true;
    }catch(error){if(serial===this.serial)this.error=String(error);return false;}
    finally{if(serial===this.serial){this.loading=false;this.emit();}}
  }
  async inspect(event:Pick<CalendarEvent,'calendar_id'|'event_id'>){
    if(this.interacting||this.pending.has(calendarKey(event))||this.checking.has(calendarKey(event)))return false;
    const key=calendarKey(event),version=(this.eventSerial.get(key)||0)+1;this.eventSerial.set(key,version);
    this.checking.add(key);this.emit();
    try{
      const result=await this.transport('inspect_schedule_event',{calendar_id:event.calendar_id,event_id:event.event_id}) as {ok:boolean;event?:CalendarEvent;error?:string};
      if(version!==this.eventSerial.get(key))return false;
      if(result.event)this.inspections.set(key,result.event);
      if(!result.ok){this.error=result.error||'日程核对失败，保留上次时间。';return false;}
      return true;
    }catch(error){if(version===this.eventSerial.get(key)){this.error=String(error);const old=this.event(key);if(old)this.inspections.set(key,{...old,editable:false,edit_reason:'读取失败，请重新核对',verification:'read_failed'});}return false;}
    finally{if(version===this.eventSerial.get(key)){this.checking.delete(key);this.emit();}}
  }
  async prepareVisible(){
    const candidates=this.page.events.filter(e=>{const current=this.event(calendarKey(e));return e.eligible&&e.sync_state!=='local'&&
      !this.blocked(calendarKey(e))&&!this.checking.has(calendarKey(e))&&Date.parse(e.end)>Date.now()&&
      (!current?.edit_checked_at||Date.now()-Date.parse(current.edit_checked_at)>=240000);});
    let index=0;await Promise.all(Array.from({length:Math.min(2,candidates.length)},async()=>{
      while(index<candidates.length){const event=candidates[index++];await this.inspect(event);}
    }));
  }
  async retime(event:CalendarEvent,draft:TimeDraft){
    const key=calendarKey(event);
    if(this.blocked(key)||this.checking.has(key)){this.error='此日程正在保存或待核对。';this.emit();return false;}
    if(!timeEditable(event)){this.error=event.edit_reason||'请先核实编辑权限。';this.emit();return false;}
    const invalid=validTimeDraft(event,draft);if(invalid){this.error=invalid;this.emit();return false;}
    if(Date.parse(event.start)===Date.parse(draft.start)&&Date.parse(event.end)===Date.parse(draft.end))return true;
    return this.submit({type:'retime',request_id:this.id(),day:beijingDay(draft.start),calendar_id:event.calendar_id,event_id:event.event_id,
      original_start:event.start,original_end:event.end,start:scheduleWireTime(draft.start),end:scheduleWireTime(draft.end)});
  }
  async reconcile(key:string){
    const marker=this.unknown.get(key),event=this.event(key);
    if(this.storageDamaged||!marker||!event||this.pending.has(key)||!['confirmed','missing','deleted'].includes(event.verification)){this.error='请先读取当前飞书时间及失效状态。';this.emit();return false;}
    return this.submit({type:'reconcile_time',request_id:this.id(),day:this.page.day,calendar_id:marker.calendar_id,event_id:marker.event_id,
      original_start:event.start,original_end:event.end,operation_id:marker.operation_id||marker.request_id,confirmed:true,verification:event.verification});
  }
  private async submit(action:ScheduleTimeAction){
    const key=calendarKey(action),previous=this.unknown.get(key);
    this.unknown.set(key,action);
    try{this.persist();}catch{if(previous)this.unknown.set(key,previous);else this.unknown.delete(key);this.error='操作编号无法保存在本机，尚未提交。';this.emit();return false;}
    this.pending.set(key,action);this.error='';this.notice='';++this.serial;this.loading=false;
    this.eventSerial.set(key,(this.eventSerial.get(key)||0)+1);this.checking.delete(key);this.emit();
    try{
      const result=await this.transport('change_schedule',{action}) as ScheduleWeekPage;
      ++this.serial;
      if(result.events&&result.week)this.adopt(result);
      if(result.operation?.event)this.inspections.set(key,result.operation.event);
      if(result.change_state!=='unconfirmed'){
        if(!result.ok&&previous)this.unknown.set(key,previous);else {this.unknown.delete(key);this.missing.delete(key);}
      }
      try{this.persist();}catch{this.notice='本机核对标记清理失败，重开后先读取原回执。';}
      if(!result.ok){this.error=result.error||'操作尚未完整确认，请先读取核对。';return false;}
      this.notice=result.operation?.notice||(action.type==='retime'?'时间已保存并核对，事项进度保持不变。':'已按当前时间结束本轮核对，没有重发改期。');return true;
    }catch(error){this.error='连接断开或结果未确认；保留原时间、拟调整时间和编号，先读取核对。 '+String(error);return false;}
    finally{this.pending.delete(key);this.emit();}
  }
}
