import { createScheduleDemo, type ScheduleTransport, type CalendarEvent } from './schedule-state';
export const WORK_STATES = [['todo','待办'],['doing','进行中'],['review','待验收'],['done','已完成']] as const;
export type WorkState = typeof WORK_STATES[number][0];
export type WorkFields = { title: string; description: string; project_id: string; priority: 'high'|'normal'|'low'; due: string };
export type WorkLink = { id: string; title: string; conversation: string; project_name: string };
export type WorkItem = WorkFields & { id: string; state: WorkState; archived: boolean; version: number; created: number; updated: number; links: WorkLink[]; project_name: string; removed_project: string; schedules?:{events:CalendarEvent[];next:CalendarEvent|null;unchecked:boolean;previous:CalendarEvent|null} };
export type WorkRun = { id: string; title: string; conversation: string; conversation_title: string; project_id: string; project_name: string; agent: string; state: string; delivery_state: string; kind: string; status: string; error: string; answer: string; created: number };
export type WorkReceipt = { request_id: string; item_id: string; version: number; operation: string };
export type WorkPage = { ok: boolean; revision: number; items: WorkItem[]; projects: { id: string; name: string }[]; runs: WorkRun[]; receipt?: WorkReceipt|null; error?: string; conflict?: boolean; change_state?: string };
export type WorkAction = { type: string; request_id: string; id?: string; version?: number; state?: WorkState; fields?: WorkFields; archived?: boolean; run_id?: string };
type Intent = { state: WorkState; sequence: number };
type Uncertain = { action: WorkAction; message: string; desired?: WorkState };
export type WorkTransport = (method: string, args?: Record<string, unknown>) => Promise<WorkPage>;
const empty = (): WorkPage => ({ok:true,revision:-1,items:[],projects:[],runs:[]});
export const workStateLabel = (state: string) => WORK_STATES.find(row=>row[0]===state)?.[1] || state;
export function runStateLabel(run: WorkRun) {
  const labels: Record<string,string> = {pending:'待执行',queued:'排队中',running:'执行中',waiting:'等待依赖',cancelling:'停止中',succeeded:'执行成功',failed:'执行失败',cancelled:'已停止',timed_out:'已超时',interrupted:'已中断'};
  if (run.kind==='plan' && run.delivery_state) return ({running:'已委派 · 交付进行中',succeeded:'整体已交付',failed:'交付未完整完成'} as Record<string,string>)[run.delivery_state] || labels[run.state] || run.state;
  return labels[run.state] || run.state;
}
export function mergeWorkPage(previous: WorkPage, incoming: WorkPage): WorkPage {
  const newer = incoming.revision >= previous.revision;
  const items = new Map(previous.items.map(item=>[item.id,item]));
  for(const item of incoming.items) {
    const old = items.get(item.id);
    if(!old || item.version>old.version || (newer && item.version===old.version)) items.set(item.id,item);
  }
  const rank={high:0,normal:1,low:2};
  return {...(newer?incoming:previous),items:[...items.values()].sort((a,b)=>rank[a.priority]-rank[b.priority]||(a.due||'9999').localeCompare(b.due||'9999')||a.created-b.created||a.id.localeCompare(b.id))};
}

// One owner of confirmed snapshots and per-item user intentions. IO is injected.
export class WorkBoardController {
  page=empty(); loading=false; loaded=false; error=''; notice='';
  intents=new Map<string,Intent>(); pending=new Set<string>(); uncertain=new Map<string,Uncertain>();
  rowErrors=new Map<string,string>(); private running=new Map<string,Promise<void>>(); private sequence=0;
  completed=new Map<string,WorkReceipt>();
  private readSequence=0; private listeners=new Set<()=>void>();
  constructor(private transport:WorkTransport, private id=()=>crypto.randomUUID()) {}
  subscribe=(listener:()=>void)=>{this.listeners.add(listener);return()=>{this.listeners.delete(listener);};};
  private emit(){this.listeners.forEach(listener=>listener());}
  get items(){return this.page.items.map(item=>this.intents.has(item.id)?{...item,state:this.intents.get(item.id)!.state}:item);}
  private accept(page:WorkPage){this.page=mergeWorkPage(this.page,page);this.loaded=true;}
  async load(reconcile=false) {
    const read=++this.readSequence;this.loading=true;this.error='';this.emit();
    try {
      const page=await this.transport('load_work_board');if(!page.ok)throw Error(page.error||'事项读取失败。');
      this.accept(page);
      if(reconcile) for(const [key,record] of [...this.uncertain]) {
        const checked=await this.transport('load_work_board',{request_id:record.action.request_id});
        if(!checked.ok)throw Error(checked.error||'操作回执读取失败。');
        this.accept(checked);
        if(checked.receipt){this.completed.set(key,checked.receipt);this.uncertain.delete(key);this.intents.delete(key);this.rowErrors.delete(key);this.notice=record.desired?'原操作已确认；尚未保存的新移动请重新操作。':'已确认原操作保存成功。';}
        else this.notice='暂未查到原操作回执。可继续核对，或手动重试原操作；不会自动重发。';
      }
      if(reconcile)for(const key of [...this.intents.keys()])if(!this.running.has(key)&&!this.uncertain.has(key)){
        this.intents.delete(key);this.rowErrors.delete(key);this.notice='已读取最新状态；尚未保存的移动请重新操作。';
      }
    } catch(error) {if(read===this.readSequence)this.error=String(error);}
    finally {if(read===this.readSequence)this.loading=false;this.emit();}
  }
  move(id:string,state:WorkState):Promise<void> {
    if(this.uncertain.has(id)){this.rowErrors.set(id,'先核对结果未确认的原操作。');this.emit();return Promise.resolve();}
    if(this.pending.has(id)&&!this.running.has(id)){this.rowErrors.set(id,'该事项正在保存其他修改，请稍后移动。');this.emit();return Promise.resolve();}
    const item=this.page.items.find(item=>item.id===id);if(!item||item.archived)return Promise.resolve();
    this.intents.set(id,{state,sequence:++this.sequence});this.rowErrors.delete(id);this.emit();
    const existing=this.running.get(id);if(existing)return existing;
    const promise=this.flush(id).finally(()=>{this.running.delete(id);this.emit();});this.running.set(id,promise);return promise;
  }
  private async flush(id:string) {
    this.pending.add(id);
    try {
      while(this.intents.has(id)) {
        const intent={...this.intents.get(id)!};const item=this.page.items.find(item=>item.id===id)!;
        if(intent.state===item.state){this.intents.delete(id);break;}
        const action:WorkAction={type:'move',request_id:this.id(),id,version:item.version,state:intent.state};
        let result:WorkPage;
        try{result=await this.transport('change_work_item',{action});}
        catch(error){this.markUncertain(id,action,String(error));break;}
        if(result.ok){this.accept(result);this.notice='事项状态已保存。';if(this.intents.get(id)?.sequence===intent.sequence)this.intents.delete(id);}
        else if(result.change_state==='unconfirmed'||result.change_state==='committed') {this.markUncertain(id,action,result.error||'结果未确认。');break;}
        else if(result.conflict){if(this.intents.get(id)?.sequence===intent.sequence)this.intents.delete(id);this.rowErrors.set(id,(result.error||'事项已变化。')+' 最新未保存的移动请刷新后重新操作。');break;}
        else if(this.intents.get(id)?.sequence===intent.sequence){this.intents.delete(id);this.rowErrors.set(id,result.error||'保存失败，已恢复已确认状态。');break;}
        this.emit();
      }
    } finally {this.pending.delete(id);this.emit();}
  }
  private markUncertain(key:string,action:WorkAction,message:string){
    const desired=this.intents.get(key)?.state;
    this.uncertain.set(key,{action,message,desired:desired!==action.state?desired:undefined});
    this.rowErrors.set(key,'保存结果未确认，已暂停此事项后续保存。请先重新读取核对。');this.emit();
  }
  async change(action:Omit<WorkAction,'request_id'>):Promise<boolean> {
    const key=action.id||'create';
    if(this.pending.has(key)||this.uncertain.has(key)){this.error='该事项仍在保存或等待核对，请先处理原操作。';this.emit();return false;}
    const item=this.page.items.find(item=>item.id===action.id);
    const request:WorkAction={...action,request_id:this.id(),...(item?{version:action.version??item.version}:{})};
    return this.submit(request,key);
  }
  private async submit(action:WorkAction,key:string):Promise<boolean> {
    this.pending.add(key);this.completed.delete(key);this.error='';this.emit();
    try {
      const result=await this.transport('change_work_item',{action});
      if(result.ok){const latest=this.uncertain.get(key)?.desired;this.accept(result);if(result.receipt)this.completed.set(key,result.receipt);this.uncertain.delete(key);this.intents.delete(key);this.rowErrors.delete(key);this.notice=latest?'原操作已确认；后续未保存的移动请重新操作。':'事项操作已保存。';return true;}
      if(result.change_state==='unconfirmed'||result.change_state==='committed')this.markUncertain(key,action,result.error||'保存结果未确认。');
      else {this.uncertain.delete(key);this.intents.delete(key);this.rowErrors.set(key,result.error||'原操作未保存，请刷新后重新操作。');}
      this.error=result.error||'事项操作失败。';return false;
    } catch(error){this.markUncertain(key,action,String(error));this.error='连接中断或调用结果未确认，请核对原操作。';return false;}
    finally{this.pending.delete(key);this.emit();}
  }
  async retry(key:string){const record=this.uncertain.get(key);if(record&&!this.pending.has(key))await this.submit(record.action,key);}
}

export function workBoardFixture():WorkPage {
  const fields={description:'',project_id:'tebb',priority:'normal' as const,due:'2026-10-24'};
  const cards:WorkItem[]=[
    {id:'demo-keyboard',...fields,title:'补齐工作看板键盘操作',description:'不用拖拽，也可以跨列推进事项。',state:'todo',archived:false,version:1,created:1,updated:1,links:[],project_name:'tebb',removed_project:''},
    {id:'demo-week',...fields,title:'整理本周学习清单',description:'先收集想法，再安排时间。',project_id:'',project_name:'',priority:'low',due:'',state:'todo',archived:false,version:1,created:2,updated:2,links:[],removed_project:''},
    {id:'demo-model',...fields,title:'完善模型连接错误提示',description:'保留输入，明确超时和凭据错误的区别。',priority:'high',due:'2026-10-22',state:'doing',archived:false,version:1,created:3,updated:3,links:[{id:'demo-run',title:'模型连接处理',conversation:'demo-chat',project_name:'tebb'}],project_name:'tebb',removed_project:''},
    {id:'demo-review',...fields,title:'业务看板首轮验收',description:'核对拖动后的状态与实际保存结果。',priority:'high',due:'2026-10-23',state:'review',archived:false,version:1,created:4,updated:4,links:[],project_name:'tebb',removed_project:''},
    {id:'demo-done',...fields,title:'完成后台去 GUI 继承',state:'done',archived:false,version:1,created:5,updated:5,links:[],project_name:'tebb',removed_project:''}
  ];
  return {ok:true,revision:1,projects:[{id:'tebb',name:'tebb'},{id:'atlas',name:'Atlas'}],items:cards,runs:[{id:'demo-run',title:'模型连接处理',conversation:'demo-chat',conversation_title:'码可私聊',project_id:'tebb',project_name:'tebb',agent:'码可',state:'running',delivery_state:'',kind:'task',status:'演示：正在检查错误回执',error:'',answer:'',created:1}]};
}
export function demoWorkTransport():WorkTransport&{schedule:ScheduleTransport} {
  let page=workBoardFixture();const receipts=new Map<string,WorkReceipt>();let count=0;
  const transport:WorkTransport=async(method,args={})=>{
    if(method==='load_work_board')return structuredClone({...page,receipt:receipts.get(String(args.request_id))||null});
    const action=args.action as WorkAction;
    if(receipts.has(action.request_id))return structuredClone({...page,receipt:receipts.get(action.request_id)});
    let item=page.items.find(i=>i.id===action.id);
    if(action.type==='create') {item={...action.fields!,id:'demo-new-'+(++count),state:'todo',archived:false,version:0,created:Date.now(),updated:Date.now(),links:[],project_name:'',removed_project:''};page.items.push(item);}
    else if(!item||item.version!==action.version)return {...page,ok:false,conflict:true,error:'演示事项已变化，请刷新。',change_state:'not_changed'};
    if(action.type==='save')Object.assign(item!,action.fields);
    if(action.type==='move')item!.state=action.state!;
    if(action.type==='archive')item!.archived=action.archived!;
    if(action.type==='link'&&!item!.links.some(l=>l.id===action.run_id)){const run=page.runs.find(r=>r.id===action.run_id)!;item!.links.push({id:run.id,title:run.title,conversation:run.conversation,project_name:run.project_name});}
    if(action.type==='unlink')item!.links=item!.links.filter(l=>l.id!==action.run_id);
    item!.version++;item!.project_name=page.projects.find(p=>p.id===item!.project_id)?.name||'';page.revision++;
    const receipt={request_id:action.request_id,item_id:item!.id,version:item!.version,operation:action.type};receipts.set(action.request_id,receipt);
    return structuredClone({...page,receipt});
  };
  return Object.assign(transport,{schedule:createScheduleDemo(()=>page)});
}
