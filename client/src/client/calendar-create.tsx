import { useEffect, useMemo, useReducer, useState } from 'react';
import { rpc, type BackendInfo } from './api';
import { eventRange } from './board';
import type { CreatedAgenda } from './board-actions';
import type { WorkPage } from './work-board-state';
import { ScheduleController, unresolved, type SchedulePage, type ScheduleTransport } from './schedule-state';
import type { ScheduleTarget } from './schedule';
import { WorkspaceDialog } from './workspace-dialog';

export function CalendarCreate({info,day,items,transport,busy,error,onCreate,onSaved,onClose,initialItem=''}:{info:BackendInfo|null;day:string;items:WorkPage['items'];transport?:ScheduleTransport;busy:boolean;error:string;initialItem?:string;
  onCreate:(summary:string,start:string,end:string)=>Promise<CreatedAgenda|undefined>;onSaved:(target:ScheduleTarget)=>void;onClose:()=>void}){
  const [itemId,setItem]=useState(initialItem),[title,setTitle]=useState(items.find(item=>item.id===initialItem)?.title||''),[start,setStart]=useState(day+'T09:00'),[end,setEnd]=useState(day+'T10:00');
  const [localError,setError]=useState(''),[saving,setSaving]=useState(false),[leave,setLeave]=useState(false);
  const [,redraw]=useReducer(n=>n+1,0);
  const controller=useMemo(()=>new ScheduleController(transport||((method,args)=>{if(!info)throw Error('后端尚未连接。');return rpc<SchedulePage>(info,method,args,{timeoutMs:120000});}),itemId,day,window.localStorage),[transport,info?.port,info?.token,itemId,day]);
  useEffect(()=>controller.subscribe(redraw),[controller]);
  const item=items.find(row=>row.id===itemId),uncertain=!!itemId&&(!!controller.unknown||controller.page.operations.some(op=>op.item_id===itemId&&op.type==='create'&&unresolved(op)));
  const dirty=!!title||!!itemId||start!==day+'T09:00'||end!==day+'T10:00';
  function close(){if(saving||busy)return;if(dirty)setLeave(true);else onClose();}
  async function save(){if(saving||busy||uncertain)return;setError('');let fields;
    try{fields=eventRange(title,start,end);}catch(e){setError(String(e));return;}
    setSaving(true);
    try{
      if(itemId){if(!item||item.archived)throw Error('请先选择可用的未归档事项。');
        const ok=await controller.change({type:'create',item_id:itemId,version:item.version,day:start.slice(0,10),...fields});
        if(ok){const event=controller.page.operation?.event;onSaved({item_id:itemId,day:start.slice(0,10),...(event?{calendar_id:event.calendar_id,event_id:event.event_id}:{})});}
      }else{const result=await onCreate(fields.summary,fields.start,fields.end);if(result)onSaved({day:start.slice(0,10),event_id:result.event_id});}
    }catch(e){setError(String(e));}finally{setSaving(false);}
  }
  return <WorkspaceDialog title="新建日程" onClose={close}><form className="calendar-create" onSubmit={e=>{e.preventDefault();void save();}}>
    <p className="text-xs text-muted-foreground">飞书主日历 · 北京时间 UTC+8 · 单次定时日程</p>
    <label>关联事项（可选）<select aria-label="关联事项" value={itemId} disabled={saving||busy||uncertain} onChange={e=>{setItem(e.target.value);const selected=items.find(row=>row.id===e.target.value);if(!title&&selected)setTitle(selected.title);}}><option value="">不关联 · 独立个人日程</option>{items.filter(row=>!row.archived).map(row=><option value={row.id} key={row.id}>{row.title}</option>)}</select></label>
    <label>日程标题<input aria-label="日程标题" value={title} disabled={saving||busy||uncertain} onChange={e=>setTitle(e.target.value)} required maxLength={120}/></label>
    <label>开始时间<input type="datetime-local" aria-label="日程开始时间" value={start} disabled={saving||busy||uncertain} onChange={e=>setStart(e.target.value)} required/></label>
    <label>结束时间<input type="datetime-local" aria-label="日程结束时间" value={end} disabled={saving||busy||uncertain} onChange={e=>setEnd(e.target.value)} required/></label>
    {(localError||error||controller.error)&&<p role="alert" className="text-destructive">{localError||controller.error||error}</p>}
    {uncertain&&<div className="sc-warning"><p>原操作可能已经执行。请先查看安排和原编号核对，不要重复新建。</p><button type="button" disabled={saving} onClick={()=>onSaved({day:start.slice(0,10),item_id:itemId})}>查看安排并核对</button></div>}
    <div className="sc-actions"><button type="submit" className="wb-primary" disabled={saving||busy||uncertain}>{saving?'保存中…':'创建日程'}</button><button type="button" disabled={saving||busy} onClick={close}>取消</button></div>
  </form>{leave&&<section className="sc-warning" role="alertdialog" aria-label="未保存的日程"><p>有未保存的输入。放弃只关闭本地表单，已发出的操作仍需核对。</p><button type="button" onClick={onClose}>放弃输入</button><button type="button" onClick={()=>setLeave(false)}>继续编辑</button></section>}</WorkspaceDialog>;
}
