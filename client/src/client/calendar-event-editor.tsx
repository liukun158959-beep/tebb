import type { CalendarEvent, SchedulePage } from './schedule-state';

export type CalendarDraft={id:string;summary:string;start:string;end:string;item_id:string;sync:boolean;existing?:CalendarEvent};
export function CalendarEventEditor({value,items,busy,blocked,error,onChange,onSave,onClose}:{value:CalendarDraft;items:SchedulePage['items'];busy:boolean;blocked:boolean;error:string;
  onChange:(value:CalendarDraft)=>void;onSave:()=>void;onClose:()=>void}){
  const synced=value.existing?.sync_state==='synced'||!!value.existing?.remote;
  const change=(fields:Partial<CalendarDraft>)=>onChange({...value,...fields});
  return <section className="sw-event-editor" role="dialog" aria-modal="false" aria-label={value.existing?'编辑日程':'新日程草稿'}>
    <header><div><h2>{value.existing?'编辑日程':'新日程'}</h2><p>{value.existing?(synced?'已同步到飞书':'仅本地保存'):'预览中 · 保存后才会创建'}</p></div><button type="button" disabled={busy} aria-label="关闭日程表单" onClick={onClose}>×</button></header>
    <form onSubmit={e=>{e.preventDefault();onSave();}}>
      <label>标题<input aria-label="日程标题" value={value.summary} maxLength={120} required readOnly={synced} disabled={busy||blocked} onChange={e=>change({summary:e.target.value})} placeholder="这段时间做些什么？"/></label>
      <label>开始时间<input aria-label="日程开始时间" type="datetime-local" value={value.start} required disabled={busy||blocked} onChange={e=>change({start:e.target.value})}/></label>
      <label>结束时间<input aria-label="日程结束时间" type="datetime-local" value={value.end} required disabled={busy||blocked} onChange={e=>change({end:e.target.value})}/></label>
      <label>关联事项 · 可选<select aria-label="关联事项" value={value.item_id} disabled={busy||blocked} onChange={e=>change({item_id:e.target.value})}><option value="">独立日程，不关联事项</option>{items.filter(i=>!i.archived||i.id===value.item_id).map(i=><option key={i.id} value={i.id}>{i.title}{i.archived?' · 已归档':''}</option>)}</select></label>
      <div className="sw-sync-option"><label><input type="checkbox" checked={value.sync||synced} disabled={busy||blocked||synced} onChange={e=>change({sync:e.target.checked})}/>同步到飞书</label><p>{synced?'保持绑定，调整时间将同步更新飞书。':'未勾选时，保存与拖动只影响本地。'}</p></div>
      {error&&<p className="sc-error" role="alert">{error}</p>}
      <footer><small>{value.existing?'北京时间 UTC+8':'尚未创建'}</small><button type="button" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="wb-primary" disabled={busy||blocked}>{busy?'保存中…':value.sync||synced?'保存并同步':'保存日程'}</button></footer>
    </form>
  </section>;
}
