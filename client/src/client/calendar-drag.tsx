import { DndContext, DragOverlay, PointerSensor, useDraggable, useSensor, useSensors, type DragStartEvent, type DragMoveEvent, type DragEndEvent } from '@dnd-kit/core';
import { useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { beijingStamp, calendarKey, type CalendarEvent, type SchedulePage } from './schedule-state';
import type { TimeDraft } from './schedule-week-state';

type Delta={x:number;y:number};
type Projection={time:TimeDraft;style:CSSProperties}|null;
export type CalendarItem=SchedulePage['items'][number];
export type CalendarDragSource={kind:'event';event:CalendarEvent}|{kind:'item';item:CalendarItem};
export function CalendarDragSurface({children,reduce,onStart,project,onCommit,onFinish,onPending}:{children:ReactNode;reduce:boolean;onPending?:(pending:boolean)=>void;
  onStart:(source:CalendarDragSource,event:DragStartEvent)=>void;project:(source:CalendarDragSource,delta:Delta)=>Projection;
  onCommit:(source:CalendarDragSource,time:TimeDraft|null)=>void;onFinish:(cancelled:boolean)=>void}){
  const sensors=useSensors(useSensor(PointerSensor,{activationConstraint:{delay:250,tolerance:7},
    bypassActivationConstraint:({activeNode})=>activeNode.data.current?.source?.kind==='item'}));
  const active=useRef<CalendarDragSource|null>(null),preparing=useRef(false),[dragging,setDragging]=useState<CalendarDragSource|null>(null),[preview,setPreview]=useState<Projection>(null);
  function start(event:DragStartEvent){const source=event.active.data.current?.source as CalendarDragSource|undefined;
    if(!source||!['event','item'].includes(source.kind)){onFinish(true);return;}
    active.current=source;onStart(source,event);setDragging(source);setPreview(project(source,{x:0,y:0}));}
  function move(event:DragMoveEvent){if(!active.current)return;const next=project(active.current,event.delta);
    setPreview(old=>old&&next&&old.time.start===next.time.start&&old.time.end===next.time.end
      &&old.style.top===next.style.top&&old.style.left===next.style.left?old:next);}
  function finish(event:DragEndEvent){const source=active.current;const next=source?project(source,event.delta):null;
    active.current=null;preparing.current=false;setDragging(null);setPreview(null);if(source)onCommit(source,next?.time||null);onFinish(false);}
  function cancel(){active.current=null;preparing.current=false;setDragging(null);setPreview(null);onFinish(true);}
  return <DndContext sensors={sensors} onDragPending={()=>{if(!preparing.current){preparing.current=true;onPending?.(true);}}}
    onDragAbort={()=>{preparing.current=false;onPending?.(false);}} onDragStart={start} onDragMove={move} onDragEnd={finish} onDragCancel={cancel}>
    {preview&&createPortal(<div className="sw-drop-target" data-calendar-drop-preview style={preview.style} aria-hidden="true"/>,document.body)}
    {children}<DragOverlay dropAnimation={reduce?null:{duration:190,easing:'cubic-bezier(.2,.8,.2,1)'}}>{dragging&&<div className="sw-lift-card"><strong>{dragging.kind==='item'?dragging.item.title:dragging.event.summary||'新日程'}</strong>
      <span>{preview?`${beijingStamp(preview.time.start).slice(11)} – ${beijingStamp(preview.time.end).slice(11)}`:dragging.kind==='event'?`${beijingStamp(dragging.event.start).slice(11)} – ${beijingStamp(dragging.event.end).slice(11)}`:'拖入时间轴，放下后确认'}</span></div>}</DragOverlay>
  </DndContext>;
}

export function CalendarDragContent({event,enabled,children,style}:{event:CalendarEvent;enabled:boolean;children:ReactNode;style?:CSSProperties}){
  const drag=useDraggable({id:calendarKey(event),data:{source:{kind:'event',event} satisfies CalendarDragSource},disabled:!enabled});
  return <div ref={drag.setNodeRef} {...drag.attributes} {...drag.listeners} className={`sw-drag-content${drag.isDragging?' sw-source-lifted':''}`} style={style}
    onPointerDown={e=>{e.stopPropagation();drag.listeners?.onPointerDown?.(e);}}>{children}</div>;
}
