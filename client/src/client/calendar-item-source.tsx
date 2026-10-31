import { useDraggable } from '@dnd-kit/core';
import { useState } from 'react';
import type { CalendarItem, CalendarDragSource } from './calendar-drag';
import './calendar-item-source.css';

function ItemCard({item,disabled,onArrange}:{item:CalendarItem;disabled:boolean;onArrange:(item:CalendarItem)=>void}){
  const drag=useDraggable({id:'work-item/'+item.id,data:{source:{kind:'item',item} satisfies CalendarDragSource},disabled});
  return <li ref={drag.setNodeRef} data-calendar-item={item.id} className={drag.isDragging?'sw-item-card sw-source-lifted':'sw-item-card'}>
    <button type="button" ref={drag.setActivatorNodeRef} {...drag.attributes} {...drag.listeners} tabIndex={-1} disabled={disabled}
      className="sw-item-grip" aria-label={`拖动事项：${item.title}`} title="拖入日/周时间轴">⠿</button>
    <strong>{item.title}</strong><button type="button" disabled={disabled} aria-label={`安排时间：${item.title}`} onClick={()=>onArrange(item)}>安排时间</button>
  </li>;
}

export function CalendarItemSource({items,disabled,loading,onArrange}:{items:CalendarItem[];disabled:boolean;loading:boolean;onArrange:(item:CalendarItem)=>void}){
  const [query,setQuery]=useState('');
  const available=items.filter(item=>!item.archived),visible=available.filter(item=>item.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <aside className="sw-item-source" aria-label="安排事项">
    <header><h2>安排事项</h2><span>{available.length}</span></header>
    <p>{disabled?'请先结束当前编辑或核对原操作。':'拖动抓手放入时间轴，或点击安排时间；保存前都是草稿。'}</p>
    <label>搜索事项<input type="search" value={query} onChange={event=>setQuery(event.target.value)} aria-label="搜索可安排事项" placeholder="按事项标题搜索"/></label>
    {!available.length?<p role="status">{loading?'正在读取事项…':'暂无未归档事项。'}</p>:!visible.length?<p role="status">没有匹配的事项。</p>:<ul>{visible.map(item=><ItemCard key={item.id} item={item} disabled={disabled} onArrange={onArrange}/>)}</ul>}
  </aside>;
}
