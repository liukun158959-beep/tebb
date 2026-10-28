import type { WorkPage } from './work-board-state';

export type CalendarEvent={calendar_id:string;event_id:string;summary:string;start:string;end:string;status:string;eligible:boolean;reason:string;verification:string;error:string;checked_at:string;editable?:boolean;edit_reason?:string;edit_checked_at?:string;local_id?:string;local_version?:number;sync_state?:'local'|'synced'|'unconfirmed';sync_operation?:string;remote?:{calendar_id:string;event_id:string}|null};
export type ScheduleLink={item_id:string;event:CalendarEvent};
export type ScheduleOperation={id:string;item_id:string;type:string;state:string;stage:string;error:string;event?:CalendarEvent;summary?:string;start?:string;end?:string;calendar_id?:string;event_id?:string;original_start?:string;original_end?:string;current_event?:CalendarEvent;notice?:string;execution_result?:string};
export type SchedulePage={ok:boolean;day:string;item_id:string;calendar?:{id:string;summary:string;writable:boolean};events:CalendarEvent[];events_valid:boolean;events_error:string;checked_at:string;links:ScheduleLink[];items:{id:string;title:string;version:number;archived:boolean}[];operations:ScheduleOperation[];operation?:ScheduleOperation|null;error?:string;change_state?:string;stage?:string};
export type ScheduleAction={type:'create'|'link'|'unlink'|'resolve';request_id:string;item_id:string;version:number;day:string;summary?:string;start?:string;end?:string;calendar_id?:string;event_id?:string;operation_id?:string;resolution?:'link'|'absent';confirmed?:boolean};
export type ScheduleTransport=(method:string,args:Record<string,unknown>)=>Promise<SchedulePage>;
export type ScheduleTimeAction={type:'retime'|'reconcile_time';request_id:string;day:string;calendar_id:string;event_id:string;
  original_start:string;original_end:string;summary?:string;original_summary?:string;start?:string;end?:string;operation_id?:string;confirmed?:boolean;verification?:string};
export type ScheduleWeekPage=SchedulePage&{week:true;week_start:string;week_end:string;month?:boolean;event?:CalendarEvent};
export const calendarKey=(event:Pick<CalendarEvent,'calendar_id'|'event_id'>)=>event.calendar_id+'/'+event.event_id;
export function addCalendarDays(day:string,count:number){return new Date(Date.parse(day+'T00:00:00Z')+count*86400000).toISOString().slice(0,10);}
export function calendarWeekStart(day:string){const date=new Date(day+'T00:00:00Z');if(!Number.isFinite(date.getTime()))throw Error('请选择日期。');return addCalendarDays(day,-((date.getUTCDay()+6)%7));}
type Pending=Pick<ScheduleAction,'request_id'|'item_id'|'day'|'type'>;
type StorageLike=Pick<Storage,'getItem'|'setItem'|'removeItem'>;
export const unresolved=(operation:ScheduleOperation)=>['prepared','calling','remote_created','remote_changed','unconfirmed'].includes(operation.state);
export const finished=(operation?:ScheduleOperation|null)=>!!operation&&['linked','unlinked','resolved_absent','resolved'].includes(operation.state);
const parts=(iso:string)=>Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(iso)).map(part=>[part.type,part.value]));
export function beijingStamp(iso=new Date().toISOString()){const p=parts(iso);return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;}
export const beijingDay=(iso?:string)=>beijingStamp(iso).slice(0,10);
export const absoluteTime=(stamp:string)=>{if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(stamp))throw Error('请选择日期与时间。');return stamp+':00+08:00';};
export function eventTime(event:CalendarEvent){return event.start.includes('T')?`${beijingStamp(event.start).replace('T',' ')} → ${beijingStamp(event.end).replace('T',' ')}`:`${event.start||'时间未确认'}${event.end&&event.end!==event.start?' → '+event.end:''}`;}
export function eventHealth(event:CalendarEvent){if(event.sync_state==='local')return '仅本地';if(event.sync_state==='unconfirmed')return '同步待核对';if(event.verification==='confirmed'&&(!event.checked_at||Date.now()-new Date(event.checked_at).getTime()>=300000))return '上次信息 · 未核对';return ({confirmed:'已核对',deleted:'已取消或删除',missing:'已不存在',unavailable:'不可访问',read_failed:'读取失败 · 未核对'} as Record<string,string>)[event.verification]||'未核对';}
const empty=(day:string,item_id:string):SchedulePage=>({ok:true,day,item_id,events:[],events_valid:false,events_error:'',checked_at:'',links:[],items:[],operations:[]});

// An explicit user action is the sole source of mutations; reads never replay a create.
export class ScheduleController {
  page:SchedulePage;loading=false;pending=false;error='';notice='';unknown:Pending|null=null;missingReceipt=false;
  private readSerial=0;private listeners=new Set<()=>void>();
  constructor(private transport:ScheduleTransport,readonly itemId:string,day:string,private storage?:StorageLike,private id=()=>crypto.randomUUID()){
    this.page=empty(day,itemId);
    try{const record=JSON.parse(storage?.getItem(this.storageKey)||'null');if(record?.item_id===itemId&&typeof record.request_id==='string')this.unknown=record;}catch{this.error='本地核对记录读取失败，请先核对飞书和服务端操作记录。';}
  }
  private get storageKey(){return 'tebb.schedule.pending.'+(this.itemId||'all');}
  subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener);};};
  private emit(){this.listeners.forEach(listener=>listener());}
  private clearUnknown(){this.unknown=null;this.missingReceipt=false;try{this.storage?.removeItem(this.storageKey);}catch{/* Server receipt remains authoritative. */}}
  async read(day=this.page.day,refresh=false){
    if(this.pending)return false;
    const serial=++this.readSerial;this.loading=true;this.error='';this.emit();
    try{
      const page=await this.transport('load_schedule',{day,item_id:this.itemId,refresh,request_id:this.unknown?.request_id||''});
      if(serial!==this.readSerial)return false;
      if(!page.ok)throw Error(page.error||'日程读取失败。');
      this.page=page;
      if(this.unknown){if(page.operation){this.clearUnknown();this.notice=finished(page.operation)?'原操作已确认完成。':'已读取原操作，请按实际阶段核对。';}else {this.missingReceipt=true;this.notice='尚未查到原操作记录；请先核对，不要重新创建。';}}
      return true;
    }catch(error){if(serial===this.readSerial)this.error=String(error);return false;}
    finally{if(serial===this.readSerial)this.loading=false;this.emit();}
  }
  async change(input:Omit<ScheduleAction,'request_id'>){
    return this.submit(input,false);
  }
  async reconcile(input:Omit<ScheduleAction,'request_id'>){
    if(!this.missingReceipt||!this.unknown||input.type!=='resolve'||input.operation_id!==this.unknown.request_id)return false;
    return this.submit(input,true);
  }
  private async submit(input:Omit<ScheduleAction,'request_id'>,reconciliation:boolean){
    if(this.pending||(this.unknown&&!reconciliation)){this.error='请先核对上一项操作。';this.emit();return false;}
    const previous=this.unknown;
    const action={...input,request_id:this.id()};this.pending=true;this.error='';++this.readSerial;this.loading=false;
    const marker:Pending={request_id:action.request_id,item_id:action.item_id,day:action.day,type:action.type};
    this.unknown=marker;
    try{this.storage?.setItem(this.storageKey,JSON.stringify(marker));}
    catch{this.unknown=null;this.pending=false;this.error='核对编号无法保存在本机，尚未提交操作。';this.emit();return false;}
    this.emit();
    try{
      const result=await this.transport('change_schedule',{action});
      if(result.items)this.page=result;
      else if(result.operation){this.page={...this.page,operation:result.operation,operations:[...this.page.operations.filter(op=>op.id!==result.operation!.id),result.operation].filter(unresolved)};}
      this.clearUnknown();
      if(!result.ok){if(reconciliation&&previous){this.unknown=previous;try{this.storage?.setItem(this.storageKey,JSON.stringify(previous));}catch{/* Retain the original identity in memory as well. */}}this.error=result.error||'安排未完整完成，请先核对。';return false;}
      this.notice='安排已保存。';return true;
    }catch(error){this.error='连接中断或操作结果未确认。保留编号，先读取核对；不会自动重发。 '+String(error);return false;}
    finally{this.pending=false;this.emit();}
  }
}

export function createScheduleDemo(work:()=>WorkPage):ScheduleTransport {
  let number=0;const operations=new Map<string,ScheduleOperation>();const day=beijingDay();
  const make=(id:string,title:string,start:string,end:string,eligible=true):CalendarEvent=>({calendar_id:'demo-calendar',event_id:id,summary:title,start,end,status:'confirmed',eligible,reason:eligible?'':'全天日程先只读展示',verification:'confirmed',error:'',checked_at:new Date().toISOString(),editable:eligible&&start.slice(0,10)===end.slice(0,10),edit_reason:eligible?'':'全天日程先只读展示',edit_checked_at:new Date().toISOString()});
  const events=[make('demo-focus_0','专注完善连接体验',day+'T14:00:00+08:00',day+'T15:00:00+08:00'),make('demo-reading_0','阅读与笔记',day+'T16:00:00+08:00',day+'T17:00:00+08:00'),make('demo-all_0','全天事项',day,day,false)];
  let links:ScheduleLink[]=[{item_id:'demo-model',event:events[0]}];
  function sync(){const page=work();for(const item of page.items){const rows=links.filter(l=>l.item_id===item.id).map(l=>l.event);item.schedules={events:rows,next:rows.filter(e=>e.eligible&&new Date(e.end).getTime()>Date.now()).sort((a,b)=>a.start.localeCompare(b.start))[0]||null,unchecked:false,previous:null};}}
  function snapshot(date:string,item_id:string,request_id=''):SchedulePage{sync();return structuredClone({ok:true,day:date,item_id,calendar:{id:'demo-calendar',summary:'演示主日历',writable:true},events:events.filter(e=>e.start.slice(0,10)<=date&&e.end.slice(0,10)>=date),events_valid:true,events_error:'',checked_at:new Date().toISOString(),links,items:work().items.map(({id,title,version,archived})=>({id,title,version,archived})),operations:[],operation:operations.get(request_id)||null});}
  function weekSnapshot(date:string,request_id=''):ScheduleWeekPage{
    const monday=calendarWeekStart(date),end=addCalendarDays(monday,7);
    return {...snapshot(date,'',request_id),week:true,week_start:monday,week_end:end,
      events:structuredClone(events.filter(e=>e.start.slice(0,10)<end&&e.end.slice(0,10)>=monday)),
      operations:[...operations.values()].filter(op=>op.type==='retime'&&unresolved(op))};
  }
  const timeFingerprints=new Map<string,string>();
  const localOperations=new Map<string,ScheduleOperation>();
  const batches=new Map<string,{id:string;created_at:string;entries:{local_id:string;version:number;request_id:string;state:string}[]}>();
  sync();
  return async(method,args)=>{
    if(method==='load_local_calendar')return {...snapshot(day,''),events:structuredClone(events.filter(e=>e.local_id)),operations:[],batches:structuredClone([...batches.values()]),operation:localOperations.get(String(args.request_id||''))};
    if(method==='calendar_sync_step'){
      const batch=batches.get(String(args.batch_id)),entry=batch?.entries.find(e=>e.local_id===args.local_id),event=events.find(e=>e.local_id===args.local_id);
      if(entry?.state==='queued'&&event){event.sync_state='synced';event.remote={calendar_id:'demo-calendar',event_id:'published-'+event.event_id};event.local_version=(event.local_version||1)+1;entry.state='synced';}
      return {...snapshot(day,''),batch:structuredClone(batch)};
    }
    if(method==='change_local_calendar'){
      const a=args.action as ScheduleAction&{local_id:string;events:{local_id:string;version:number}[]};
      if(a.type as string==='prepare_sync'){
        const batch={id:a.request_id,created_at:new Date().toISOString(),entries:a.events.map((e,i)=>({...e,request_id:a.request_id+'_'+i,state:'queued'}))};batches.set(batch.id,batch);return {...snapshot(day,''),batch};
      }
      let event=events.find(e=>e.local_id===a.local_id);
      const previous=localOperations.get(a.request_id);if(previous)return {...snapshot(day,''),operation:previous,event:previous.event,change_state:'committed'};
      if(a.type==='create'||a.type as string==='update'){
        if(!event){event={...make(a.local_id,a.summary!,a.start!,a.end!),calendar_id:'tebb-local',local_id:a.local_id,local_version:0,sync_state:'local'};events.push(event);}
        Object.assign(event,{summary:a.summary,start:a.start,end:a.end,local_version:(event.local_version||0)+1});
        links=links.filter(l=>calendarKey(l.event)!==calendarKey(event!));if(a.item_id)links.push({item_id:a.item_id,event});sync();
      }else if(a.type as string==='sync'&&event){event.sync_state='synced';event.remote={calendar_id:'demo-calendar',event_id:'published-'+event.event_id};event.local_version=(event.local_version||1)+1;}
      if(!event)return {...snapshot(day,''),ok:false,error:'演示本地日程不存在。'};
      const op={id:a.request_id,item_id:a.item_id||'',type:a.type,state:a.type as string==='sync'?'synced':'saved',stage:'committed',error:'',event:structuredClone(event)};
      localOperations.set(a.request_id,op);return {...snapshot(day,''),event:structuredClone(event),operation:op,change_state:'committed'};
    }
    if(method==='load_schedule_month'){const day=String(args.day),first=day.slice(0,7)+'-01',date=new Date(first+'T00:00Z'),next=new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,1)).toISOString().slice(0,10),start=calendarWeekStart(first),end=addCalendarDays(calendarWeekStart(addCalendarDays(next,-1)),7);return {...weekSnapshot(day,String(args.request_id||'')),month:true,week_start:start,week_end:end,events:structuredClone(events.filter(e=>e.start.slice(0,10)<end&&e.end.slice(0,10)>=start))};}
    if(method==='load_schedule_week')return weekSnapshot(String(args.day),String(args.request_id||''));
    if(method==='inspect_schedule_event'){
      const event=events.find(e=>e.calendar_id===args.calendar_id&&e.event_id===args.event_id);
      const owner=work().items.find(i=>links.some(l=>l.item_id===i.id&&l.event.event_id===event?.event_id));
      return {...weekSnapshot(event?.start.slice(0,10)||day),ok:!!event,event:event?{...event,
        editable:!!event.editable&&!owner?.archived,edit_reason:owner?.archived?'关联事项已归档，请先恢复':event.edit_reason,
        edit_checked_at:new Date().toISOString()}:undefined};
    }
    if(method==='load_schedule')return snapshot(String(args.day),String(args.item_id||''),String(args.request_id||''));
    const kind=(args.action as {type:string}).type;
    if(kind==='retime'||kind==='reconcile_time'){
      const action=args.action as ScheduleTimeAction,event=events.find(e=>e.event_id===action.event_id&&e.calendar_id===action.calendar_id);
      const page=()=>weekSnapshot(action.day,action.request_id);
      if(operations.has(action.request_id))return timeFingerprints.get(action.request_id)===JSON.stringify(action)?page():{...page(),ok:false,change_state:'not_changed',error:'此编号已使用。'};
      const owner=work().items.find(i=>links.some(l=>l.item_id===i.id&&l.event.event_id===event?.event_id));
      if(!event||owner?.archived||(kind==='retime'&&!event.editable))return {...page(),ok:false,change_state:'not_changed',error:'日程只读或不可用。'};
      if(Date.parse(event.start)!==Date.parse(action.original_start)||Date.parse(event.end)!==Date.parse(action.original_end))return {...page(),ok:false,change_state:'not_changed',error:'远端时间已变化，请读取后重新调整。'};
      if(kind==='retime'){
        if(!action.start||!action.end||Date.parse(action.end)<=Date.parse(action.start)||beijingDay(action.start)!==beijingDay(action.end))return {...page(),ok:false,change_state:'not_changed',error:'请选择同日的有效起止。'};
        Object.assign(event,{start:action.start,end:action.end,checked_at:new Date().toISOString(),edit_checked_at:new Date().toISOString()});
      }else if(action.operation_id){const original=operations.get(action.operation_id);if(original)original.state='observed';}
      if(owner)owner.version++;work().revision++;
      operations.set(action.request_id,{id:action.request_id,type:kind,item_id:owner?.id||'',state:kind==='retime'?'retimed':'observed',
        calendar_id:event.calendar_id,event_id:event.event_id,event:structuredClone(event),stage:'committed',error:''});
      timeFingerprints.set(action.request_id,JSON.stringify(action));
      return {...page(),change_state:'committed',stage:'committed'};
    }
    const action=args.action as ScheduleAction;
    if(!action.item_id&&action.type==='create'){
      const previous=operations.get(action.request_id);if(previous)return snapshot(action.day,'',action.request_id);
      const event=make('demo-independent-'+(++number)+'_0',action.summary!,action.start!,action.end!);events.push(event);
      operations.set(action.request_id,{id:action.request_id,item_id:'',type:'create',state:'created',stage:'committed',error:'',event});return snapshot(action.day,'',action.request_id);
    }
    const item=work().items.find(i=>i.id===action.item_id);
    if(!item)return {...snapshot(action.day,''),ok:false,error:'演示事项不存在。'};
    if(operations.has(action.request_id))return snapshot(action.day,item.id,action.request_id);
    if(item.archived||item.version!==action.version)return {...snapshot(action.day,item.id),ok:false,error:'演示事项已变化，请刷新。'};
    let event=events.find(e=>e.event_id===action.event_id);
    if(action.type==='create'){event=make('demo-created-'+(++number)+'_0',action.summary!,action.start!,action.end!);events.push(event);}
    if(action.type==='unlink')links=links.filter(l=>!(l.item_id===item.id&&l.event.event_id===action.event_id));
    else if(event){if(links.some(l=>l.event.event_id===event!.event_id&&l.item_id!==item.id))return {...snapshot(action.day,item.id),ok:false,error:'请先解除原事项关联。'};links=links.filter(l=>l.event.event_id!==event!.event_id);links.push({item_id:item.id,event});}
    item.version++;work().revision++;operations.set(action.request_id,{id:action.request_id,item_id:item.id,type:action.type,state:action.type==='unlink'?'unlinked':'linked',stage:'committed',error:'',event});
    return snapshot(action.day,item.id,action.request_id);
  };
}
