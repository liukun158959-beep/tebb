import { lazy, Suspense, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { rpc, type BackendInfo } from './api';
import { boardModel, type BoardPayload } from './board';
import { SchedulePane, type ScheduleTarget } from './schedule';
import { beijingDay, type CalendarEvent } from './schedule-state';
import { demoWorkTransport } from './work-board-state';
import { Markdown } from './Markdown';
import { WorkspaceDialog } from './workspace-dialog';
import './work-board.css';
const ScheduleWeekPane=lazy(()=>import('./schedule-week').then(module=>({default:module.ScheduleWeekPane})));
type Props={info:BackendInfo|null;data:BoardPayload|null;loading:boolean;loadError?:string;debug:boolean;onRefresh:()=>void;
  deleting?:boolean;deleteErrors?:Record<string,string>;taskErrors?:Record<string,string>;onDeleteTask?:(id:string)=>void;
  target?:(ScheduleTarget&{view?:'today'|'week'|'day'})|null;onItem:(id:string)=>void;onDeleteEvent?:(id:string)=>Promise<boolean>};

export function CalendarWorkspace(props:Props){
  const guard=useRef<((next:()=>void)=>void)|null>(null);
  const [view,setView]=useState<'week'|'day'>('week'),[target,setTarget]=useState<ScheduleTarget>({}),[daily,setDaily]=useState<ScheduleTarget|null>(null),[revision,bump]=useReducer(n=>n+1,0);
  const [panel,setPanel]=useState<'tasks'|'review'|null>(null),[reviewText,setReviewText]=useState(''),[reviewError,setReviewError]=useState(''),[reviewBusy,setReviewBusy]=useState(false),[deleteError,setDeleteError]=useState('');
  const demo=useMemo(()=>props.debug?demoWorkTransport():null,[props.debug]);
  useEffect(()=>{if(props.target){setTarget({...props.target,day:props.target.view==='today'?beijingDay():props.target.day});setView(props.target.view==='day'?'day':'week');}},[props.target]);
  function changed(){bump();}
  function navigate(next:()=>void){if(guard.current)guard.current(next);else next();}
  async function reviewCall(method:string){if(reviewBusy||!props.info||props.debug)return;setReviewBusy(true);setReviewError('');try{
    const result=await rpc<{ok:boolean;error?:string;summary?:string;url?:string}>(props.info,method,{}, {timeoutMs:120000});
    if(result.summary)setReviewText(result.summary);if(!result.ok)throw Error(result.error||'复盘未完整完成，请核对。');if(result.url)setReviewError('飞书文档已创建：'+result.url);props.onRefresh();
  }catch(e){setReviewError(String(e));}finally{setReviewBusy(false);}}
  async function deleteEvent(event:CalendarEvent){const id=event.remote?.event_id||event.event_id;
    if(event.sync_state==='local'){setDeleteError('本地日程本批未提供永久删除。');return;}
    if(!props.data?.agenda?.items?.some(row=>row.event_id===id)){setDeleteError('既有删除入口仅支持已读取的今日日程，请先刷新今日飞书数据核对。');props.onRefresh();return;}
    if(!window.confirm('删除此飞书日程？此操作会真实删除并按飞书原有规则通知参会人。'))return;
    if(props.deleting){setDeleteError('日程写入处理中，请等待结果。');return;}
    setDeleteError('');await props.onDeleteEvent?.(id);
  }
  const tasks=props.data?boardModel(props.data,new Date()):null;
  const tabs=<nav className="workspace-tabs" aria-label="日程视图">{([['week','周视图'],['day','单日']] as const).map(([id,label])=><button type="button" key={id} aria-pressed={view===id} onClick={()=>navigate(()=>setView(id))}>{label}</button>)}</nav>;
  const more=<details className="calendar-more"><summary>更多 ···</summary><div><button type="button" onClick={()=>navigate(()=>{setPanel('tasks');props.onRefresh();})}>飞书待办</button><button type="button" onClick={()=>navigate(()=>{setReviewText(props.data?.summary||'');setPanel('review');})}>本周复盘</button></div></details>;
  return <div className="calendar-workspace work-board" data-calendar-workspace>
    <Suspense fallback={<p role="status">正在打开时间轴…</p>}><ScheduleWeekPane key={`${target.calendar_id||''}/${target.event_id||''}/${target.day||''}`} info={props.info} debug={props.debug} transport={demo?.schedule} target={target} revision={revision} view={view}
      onNavigationGuard={next=>{guard.current=next;}} closeLabel="" onClose={()=>{}} onReturn={props.onItem} onDay={setDaily} onChange={changed} viewTabs={tabs} extraActions={more} onDeleteEvent={deleteEvent} deleteError={deleteError||Object.values(props.deleteErrors||{}).join('；')}/></Suspense>
    {daily&&<SchedulePane info={props.info} debug={props.debug} transport={demo?.schedule} target={daily} onClose={()=>setDaily(null)} onReturn={id=>{setDaily(null);props.onItem(id);}} onChange={changed} onWeek={next=>{setDaily(null);setTarget(next);setView('week');}}/>}
    {panel==='tasks'&&<WorkspaceDialog title="飞书待办" onClose={()=>setPanel(null)}><button type="button" disabled={props.loading} onClick={props.onRefresh}>刷新待办</button>
      {props.loadError&&<p role="alert">{props.loadError}</p>}{tasks?.taskError&&<p role="alert">{tasks.taskError}</p>}
      {!tasks&&<p>正在读取待办；读取失败可重新刷新。</p>}{tasks?.taskEmpty&&<p>今天没有待处理的飞书待办。</p>}
      {tasks?.tasks.map(task=><section className="sw-sync-row" key={task.guid}><strong>{task.title}</strong><span>{task.when}</span>{task.guid&&<button type="button" disabled={props.deleting} onClick={()=>{if(window.confirm('真实删除此飞书待办？这不是标记完成。'))props.onDeleteTask?.(task.guid);}}>删除待办</button>}{props.taskErrors?.[task.guid]&&<p role="alert">{props.taskErrors[task.guid]}</p>}</section>)}
    </WorkspaceDialog>}
    {panel==='review'&&<WorkspaceDialog title="本周复盘" onClose={()=>{if(!reviewBusy)setPanel(null);}}>{reviewText?<Markdown text={reviewText}/>:<p>尚无已生成的复盘。</p>}
      <div className="sc-actions"><button type="button" disabled={reviewBusy||props.debug||!props.info} onClick={()=>void reviewCall('generate_week_review')}>{reviewBusy?'处理中…':'生成本周复盘'}</button><button type="button" disabled={reviewBusy||props.debug||!props.info||!reviewText} onClick={()=>void reviewCall('write_today_summary_doc')}>新建飞书文档</button></div>{reviewError&&<p role="status">{reviewError}</p>}{props.debug&&<p>隔离演示不调用模型或发布飞书文档。</p>}
    </WorkspaceDialog>}
  </div>;
}
