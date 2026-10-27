import { DndContext, DragOverlay, PointerSensor, useDraggable, useSensor, useSensors, type DragStartEvent, type DragMoveEvent, type DragEndEvent } from '@dnd-kit/core';
import type { CSSProperties, ReactNode } from 'react';
import { beijingStamp, calendarKey, type CalendarEvent } from './schedule-state';
import type { TimeDraft } from './schedule-week-state';

export function CalendarDragSurface({children,dragging,preview,reduce,onStart,onMove,onEnd,onCancel}:{children:ReactNode;dragging:CalendarEvent|null;preview:TimeDraft|null;reduce:boolean;
  onStart:(event:DragStartEvent)=>void;onMove:(event:DragMoveEvent)=>void;onEnd:(event:DragEndEvent)=>void;onCancel:()=>void}){
  const sensors=useSensors(useSensor(PointerSensor,{activationConstraint:{delay:250,tolerance:7}}));
  return <DndContext sensors={sensors} onDragStart={onStart} onDragMove={onMove} onDragEnd={onEnd} onDragCancel={onCancel}>
    {children}<DragOverlay dropAnimation={reduce?null:{duration:190,easing:'cubic-bezier(.2,.8,.2,1)'}}>{dragging&&<div className="sw-lift-card"><strong>{dragging.summary||'新日程'}</strong><span>{beijingStamp(preview?.start||dragging.start).slice(11)} – {beijingStamp(preview?.end||dragging.end).slice(11)}</span></div>}</DragOverlay>
  </DndContext>;
}

export function CalendarDragContent({event,enabled,children,style}:{event:CalendarEvent;enabled:boolean;children:ReactNode;style?:CSSProperties}){
  const drag=useDraggable({id:calendarKey(event),data:{event},disabled:!enabled});
  return <div ref={drag.setNodeRef} {...drag.attributes} {...drag.listeners} className={`sw-drag-content${drag.isDragging?' sw-source-lifted':''}`} style={style}
    onPointerDown={e=>{e.stopPropagation();drag.listeners?.onPointerDown?.(e);}}>{children}</div>;
}
