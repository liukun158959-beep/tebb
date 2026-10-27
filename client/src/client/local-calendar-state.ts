import type { CalendarEvent } from './schedule-state';
import type { WeekTransport } from './schedule-week-state';

export type LocalOperation={id:string;local_id:string;type:string;state:string;stage:string;error:string;event?:CalendarEvent;calendar_id?:string};
export type SyncEntry={local_id:string;version:number;request_id:string;state:'queued'|'synced'|'failed'|'unconfirmed';stage?:string;error?:string};
export type SyncBatch={id:string;created_at:string;entries:SyncEntry[]};
export type LocalCalendarPage={ok:boolean;events:CalendarEvent[];operations:LocalOperation[];batches:SyncBatch[];operation?:LocalOperation;error?:string};
export type LocalResult={ok:boolean;event?:CalendarEvent;operation?:LocalOperation;batch?:SyncBatch;change_state?:string;stage?:string;error?:string};
type Marker={request_id:string;local_id?:string;type:string};
type StorageLike=Pick<Storage,'getItem'|'setItem'|'removeItem'>;
export class LocalCalendarController{
  page:LocalCalendarPage={ok:true,events:[],operations:[],batches:[]};busy=false;loading=false;error='';notice='';unknown:Marker|null=null;batch:SyncBatch|null=null;
  private key='tebb.calendar.pending.v1';private serial=0;private listeners=new Set<()=>void>();private damaged=false;
  constructor(private transport:WeekTransport,private storage?:StorageLike,private id=()=>crypto.randomUUID()){
    try{const value=JSON.parse(storage?.getItem(this.key)||'null');if(value){if(typeof value.request_id!=='string'||typeof value.type!=='string')throw Error();this.unknown=value;}}
    catch{this.damaged=true;this.error='本机日程核对记录读取失败，请保留记录并核对后台回执，暂停新写入。';}
  }
  subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener);};};
  private emit(){this.listeners.forEach(fn=>fn());}
  private clear(){this.unknown=null;try{this.storage?.removeItem(this.key);}catch{this.notice='本机编号清理失败，重新打开后先核对回执。';}}
  async read(){const serial=++this.serial;this.loading=true;this.emit();try{
    const page=await this.transport('load_local_calendar',{request_id:this.unknown?.request_id||''}) as LocalCalendarPage;
    if(serial!==this.serial)return false;if(!page.ok)throw Error(page.error||'本地日程读取失败。');this.page=page;
    if(page.operation&&['saved','synced','resolved_absent','retimed','observed'].includes(page.operation.state)){this.clear();this.error='';this.notice='已读取原操作回执。';}
    if(this.unknown?.type==='sync'&&page.events.some(e=>e.local_id===this.unknown?.local_id&&e.sync_state==='synced')){this.clear();this.error='';this.notice='已读取现有飞书绑定，不会重复创建。';}
    if(this.batch)this.batch=page.batches.find(b=>b.id===this.batch!.id)||this.batch;
    return true;
  }catch(error){if(serial===this.serial)this.error=String(error);return false;}finally{if(serial===this.serial)this.loading=false;this.emit();}}
  async change(input:Record<string,unknown>):Promise<LocalResult>{
    if(this.busy||this.damaged||this.unknown&&!['resolve_sync','resolve_save'].includes(String(input.type))){this.error='有执行中或结果待核对的操作，请先读取原编号。';this.emit();return {ok:false,error:this.error};}
    const action:Record<string,unknown>&{request_id:string}={...input,request_id:this.id()};
    if(action.type==='create'&&!action.local_id)action.local_id=action.request_id;
    const marker={request_id:action.request_id,local_id:action.local_id as string|undefined,type:String(action.type)};
    try{this.storage?.setItem(this.key,JSON.stringify(marker));}catch{this.error='本机无法登记编号，尚未提交操作。';this.emit();return {ok:false,error:this.error};}
    const previous=this.unknown;this.unknown=marker;this.busy=true;this.error='';this.notice='';++this.serial;this.loading=false;this.emit();
    try{const result=await this.transport('change_local_calendar',{action}) as LocalResult;
      if(result.change_state!=='unconfirmed'){this.clear();if(!result.ok&&previous){this.unknown=previous;this.storage?.setItem(this.key,JSON.stringify(previous));}}
      if(result.batch)this.batch=result.batch;
      if(!result.ok)this.error=result.error||'日程操作未完整确认，请读取核对。';
      return result;
    }catch(error){this.error='连接中断或结果未确认，保留编号，不自动重发。 '+String(error);return {ok:false,change_state:'unconfirmed',error:this.error};}
    finally{this.busy=false;await this.read();this.emit();}
  }
  async runBatch(events:CalendarEvent[]){
    if(this.busy)return;
    const made=await this.change({type:'prepare_sync',events:events.map(e=>({local_id:e.local_id,version:e.local_version}))});
    if(made.ok&&made.batch)await this.continueBatch(made.batch);
  }
  async continueBatch(batch:SyncBatch){if(this.busy||this.unknown)return;this.busy=true;this.batch=batch;this.error='';this.emit();
    try{for(const entry of batch.entries.filter(e=>e.state==='queued')){
      const result=await this.transport('calendar_sync_step',{batch_id:batch.id,local_id:entry.local_id}) as LocalResult;
      if(!result.ok||!result.batch)throw Error(result.error||'同步清单结果未确认，请读取核对后继续。');
      this.batch=result.batch;this.emit();
    }}catch(error){this.error=String(error)+' 不会自动重发创建，请先读取清单。';}
    finally{this.busy=false;await this.read();this.emit();}
  }
}
