import { lazy, Suspense, useEffect, useMemo, useReducer, useRef, useState, type ComponentProps } from 'react';
import { rpc, type BackendInfo } from './api';
import { BoardPane, type BoardPayload } from './board';
import type { CreatedAgenda } from './board-actions';
import { CalendarCreate } from './calendar-create';
import { usePageActive } from './page-stack';
import { SchedulePane, type ScheduleTarget } from './schedule';
import { beijingDay } from './schedule-state';
import { WorkBoardController, demoWorkTransport, runStateLabel } from './work-board-state';
import { Markdown } from './Markdown';
import { WorkspaceDialog } from './workspace-dialog';
import './work-board.css';
const ScheduleWeekPane=lazy(()=>import('./schedule-week').then(module=>({default:module.ScheduleWeekPane})));
type Props=Omit<ComponentProps<typeof BoardPane>,'onCreateEvent'> & {info:BackendInfo|null;target?:(ScheduleTarget&{view?:'today'|'week'|'day'})|null;onItem:(id:string)=>void;onRun:(conversation:string)=>void;
  onCreated:(summary:string,start:string,end:string)=>Promise<CreatedAgenda|undefined>};

export function CalendarWorkspace(props:Props){
  const guard=useRef<((next:()=>void)=>void)|null>(null);
  const active=usePageActive(),[view,setView]=useState<'today'|'week'|'day'>('today'),[target,setTarget]=useState<ScheduleTarget>({}),[daily,setDaily]=useState<ScheduleTarget|null>(null),[creating,setCreating]=useState(''),[revision,bump]=useReducer(n=>n+1,0);
  const [review,setReview]=useState(false),[reviewText,setReviewText]=useState(''),[reviewError,setReviewError]=useState(''),[reviewBusy,setReviewBusy]=useState(false),[workError,setWorkError]=useState('');
  const [demoBoard,setDemoBoard]=useState<BoardPayload|null>(null);
  const demo=useMemo(()=>props.debug?demoWorkTransport():null,[props.debug]);
  const controller=useMemo(()=>new WorkBoardController(demo||((method,args)=>{if(!props.info)throw Error('后端尚未连接。');return rpc(props.info,method,args);})),[props.info?.port,props.info?.token,demo]);
  const [,redraw]=useReducer(n=>n+1,0);
  useEffect(()=>controller.subscribe(redraw),[controller]);
  useEffect(()=>{if(!active)return;let live=true;async function read(){if(controller.loading)return;await controller.load();if(live)setWorkError(controller.error);
    if(demo&&!props.data){const page=await demo.schedule('load_schedule',{day:beijingDay(),item_id:'',refresh:false});if(live)setDemoBoard({ok:true,date:beijingDay(),agenda:{ok:page.events_valid,error:page.events_error,items:page.events.map(e=>({summary:e.summary,start:e.start.includes('T')?e.start.slice(11,16):e.start+' 全天',end:e.end.includes('T')?e.end.slice(11,16):e.end,event_id:e.event_id}))},tasks:{ok:true,items:[]}});}}
    void read();const timer=window.setInterval(()=>void read(),5000);return()=>{live=false;window.clearInterval(timer);};
  },[active,controller,demo,revision,props.data]);
  useEffect(()=>{if(props.target){setTarget(props.target);setView(props.target.view||'week');}},[props.target]);
  const items=controller.items.filter(item=>!item.archived&&item.state==='review');
  const runs=controller.page.runs.filter(run=>['failed','timed_out','interrupted','waiting'].includes(run.state)||(run.state==='succeeded'&&run.answer));
  function locate(next:ScheduleTarget){setTarget({...next});setView('week');}
  function changed(){bump();props.onRefresh();}
  async function reviewCall(method:string){if(reviewBusy||!props.info||props.debug)return;setReviewBusy(true);setReviewError('');try{
    const result=await rpc<{ok:boolean;error?:string;summary?:string;url?:string}>(props.info,method,{}, {timeoutMs:120000});if(result.summary)setReviewText(result.summary);if(!result.ok)throw Error(result.error||'复盘未完整完成，请核对。');if(result.url)setReviewError('飞书文档已创建：'+result.url);props.onRefresh();
  }catch(e){setReviewError(String(e));}finally{setReviewBusy(false);}}
  const attention=<div className="calendar-attention"><h2>待验收事项</h2>{workError&&<p role="alert" className="text-destructive">{workError}</p>}{!workError&&!controller.loaded&&<p role="status">读取事项中…</p>}{controller.loaded&&!items.length&&!workError&&<p>暂无待验收事项。</p>}{items.map(item=><div className="calendar-attention-row" key={item.id}><span>{item.title}</span><button type="button" onClick={()=>props.onItem(item.id)}>检查事项 →</button></div>)}<h2>AI 提醒</h2>{!runs.length&&!workError&&<p>暂无需要留意的执行记录。</p>}{runs.slice(0,5).map(run=><div className="calendar-attention-row" key={run.id}><span>{run.title}<small>{run.agent} · {runStateLabel(run)}</small></span><button type="button" onClick={()=>props.onRun(run.conversation)}>来源对话 →</button></div>)}</div>;
  return <div className="calendar-workspace work-board" data-calendar-workspace>
    <div className="calendar-heading"><div><span className="wb-eyebrow">工作区 / 日程</span><h1>日程</h1><p>飞书主日历 · 北京时间 UTC+8</p></div>{view==='today'&&<button type="button" className="wb-primary" onClick={()=>setCreating(beijingDay())}>＋ 新建日程</button>}</div>
    <nav className="workspace-tabs" aria-label="日程视图">{[['today','今天'],['week','周视图'],['day','单日']].map(([id,label])=><button type="button" key={id} aria-pressed={view===id} onClick={()=>{const next=()=>{if(id==='today')setTarget({});setView(id as typeof view);};if(guard.current)guard.current(next);else next();}}>{label}</button>)}</nav>
    {view==='today'?<BoardPane {...props} data={props.data||demoBoard} embedded onViewEvent={event_id=>locate({event_id,day:props.data?.date||beijingDay()})} attention={attention}
      review={<div className="calendar-retro"><span>本周复盘</span><button type="button" onClick={()=>{setReviewText(props.data?.summary||'');setReview(true);}}>查看 / 生成复盘 →</button></div>}/>:<Suspense fallback={<p role="status">正在打开时间轴…</p>}><ScheduleWeekPane key={`${target.calendar_id||''}/${target.event_id||''}/${target.day||''}`} info={props.info} debug={props.debug} transport={demo?.schedule} target={target} revision={revision} view={view} onNavigationGuard={next=>{guard.current=next;}} closeLabel="回到今天" onClose={()=>{setView('today');setTarget({});}} onReturn={props.onItem} onDay={setDaily} onChange={changed} onNew={setCreating}/></Suspense>}
    {daily&&<SchedulePane info={props.info} debug={props.debug} transport={demo?.schedule} target={daily} onClose={()=>setDaily(null)} onReturn={id=>{setDaily(null);props.onItem(id);}} onChange={changed} onWeek={next=>{setDaily(null);locate(next);}}/>}
    {creating&&<CalendarCreate info={props.info} day={creating} initialItem={target.item_id} items={controller.items} transport={demo?.schedule} busy={!!props.deleting} error={props.createError||''} onCreate={demo?async(summary,start,end)=>{const page=await demo.schedule('change_schedule',{action:{type:'create',request_id:crypto.randomUUID(),item_id:'',version:0,day:start.slice(0,10),summary,start,end}});if(!page.ok)throw Error(page.error);return {ok:true,summary,start,end,event_id:page.operation?.event?.event_id};}:props.onCreated} onClose={()=>setCreating('')} onSaved={next=>{setCreating('');changed();locate(next);}}/>}
    {review&&<WorkspaceDialog title="本周复盘" onClose={()=>{if(!reviewBusy)setReview(false);}}>{reviewText?<Markdown text={reviewText}/>:<p>尚无已生成的复盘。</p>}<div className="sc-actions"><button type="button" disabled={reviewBusy||props.debug||!props.info} onClick={()=>void reviewCall('generate_week_review')}>{reviewBusy?'处理中…':'生成本周复盘'}</button><button type="button" disabled={reviewBusy||props.debug||!props.info||!reviewText} onClick={()=>void reviewCall('write_today_summary_doc')}>新建飞书文档</button></div>{reviewError&&<p role="status">{reviewError}</p>}{props.debug&&<p>隔离演示不调用模型或发布飞书文档。</p>}</WorkspaceDialog>}
  </div>;
}
