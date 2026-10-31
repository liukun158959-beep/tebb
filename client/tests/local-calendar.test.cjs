const {test}=require('node:test'),assert=require('node:assert/strict');
const React=require('react'),{act,create}=require('react-test-renderer');
const load=require('./load-client.cjs'),state=load('schedule-state.ts'),week=load('schedule-week-state.ts',{'./schedule-state':state});
const {LocalCalendarController}=load('local-calendar-state.ts');
const {CalendarEventEditor}=load('calendar-event-editor.tsx');
const storage=()=>{const data=new Map();return {data,getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};};
const page=extra=>({ok:true,events:[],operations:[],batches:[],...extra});
const day=state.addCalendarDays(state.beijingDay(),1);
const event=(id,extra={})=>({calendar_id:'tebb-local',event_id:id,local_id:id,local_version:1,summary:id,start:day+'T09:00:00+08:00',end:day+'T10:00:00+08:00',eligible:true,editable:true,verification:'confirmed',status:'confirmed',checked_at:'2000-01-01',sync_state:'local',...extra});
test('local storage failure prevents submission and a lost ack keeps the same identity across restart',async()=>{
 let writes=0,saved;const memory=storage(),transport=async(method,args)=>{if(method==='load_local_calendar')return page(saved?{events:[event(saved.local_id)],operation:{id:saved.request_id,type:'save',state:'saved'}}:{});writes++;saved=args.action;throw Error('closed');};
 let c=new LocalCalendarController(transport,memory,()=> 'fixed-request');
 assert.equal((await c.change({type:'create',summary:'保留'})).ok,false);assert.equal(writes,1);assert.equal(saved.local_id,'fixed-request');
 c=new LocalCalendarController(transport,memory);await c.read();assert.equal(c.unknown,null);assert.equal(memory.data.size,0);assert.equal(writes,1);
 const broken=new LocalCalendarController(transport,{getItem:()=>null,setItem:()=>{throw Error('disk');},removeItem(){}});await broken.change({type:'create'});assert.equal(writes,1);
});
test('missing receipt never permits a second save until explicit reconciliation seals the original',async()=>{
 let writes=0;const memory=storage();const c=new LocalCalendarController(async(method,args)=>{if(method==='load_local_calendar')return page();writes++;if(args.action.type==='resolve_save')return {ok:true,change_state:'committed'};throw Error('closed');},memory,()=> 'fixed-request');
 await c.change({type:'create',local_id:'stable-event'});assert(c.unknown);await c.read();await c.change({type:'create'});assert.equal(writes,1);
 await c.change({type:'resolve_save',local_id:'stable-event',operation_id:'fixed-request',confirmed:true});assert.equal(writes,2);assert.equal(c.unknown,null);
});
test('bulk uses a fixed version manifest, reports partial results and does not retry unknown entries',async()=>{
 const events=[event('a'),event('b')],calls=[];let batch;
 const c=new LocalCalendarController(async(method,args)=>{calls.push([method,args]);if(method==='load_local_calendar')return page({events,batches:batch?[batch]:[]});
  if(method==='change_local_calendar'){assert.deepEqual(args.action.events,[{local_id:'a',version:1},{local_id:'b',version:1}]);batch={id:'batch',entries:events.map(e=>({local_id:e.local_id,version:1,request_id:e.local_id,state:'queued'}))};return {ok:true,batch};}
  const row=batch.entries.find(e=>e.local_id===args.local_id);row.state=row.local_id==='a'?'synced':'unconfirmed';return {ok:true,batch:structuredClone(batch)};
 },storage(),()=> 'batch-request');
 await c.runBatch(events);assert.deepEqual(c.batch.entries.map(e=>e.state),['synced','unconfirmed']);
 await c.continueBatch(c.batch);assert.equal(calls.filter(([method])=>method==='calendar_sync_step').length,2);
});
test('visible permission preparation includes the unselected second event and preserves readonly reasons',async()=>{
 const inspected=[];const c=new week.ScheduleWeekController(async(method,args)=>{assert.equal(method,'inspect_schedule_event');inspected.push(args.event_id);return {ok:true,event:event(args.event_id,{sync_state:'synced',editable:args.event_id!=='meeting',edit_reason:args.event_id==='meeting'?'其他参与人':'',edit_checked_at:new Date().toISOString()})};},day,storage());
 c.page={...c.page,events:[event('first',{sync_state:'synced',editable:false}),event('second',{sync_state:'synced',editable:false}),event('meeting',{sync_state:'synced',editable:false})]};
 await c.prepareVisible();assert.deepEqual(inspected,['first','second','meeting']);assert(week.timeEditable(c.event('tebb-local/second')));assert.equal(c.event('tebb-local/meeting').edit_reason,'其他参与人');
 await c.prepareVisible();assert.equal(inspected.length,3);assert(week.timeEditable(event('offline')));
});
test('editor is nonmodal, updates its draft and cannot remove an established Feishu binding',async()=>{
 let draft={id:'draft',summary:'',start:day+'T09:00',end:day+'T10:00',item_id:'',sync:false},changed,closed=0,view;
 await act(async()=>{view=create(React.createElement(CalendarEventEditor,{value:draft,items:[],busy:false,blocked:false,error:'',onChange:v=>changed=v,onSave(){},onClose:()=>closed++}));});
 assert.equal(view.root.findByProps({role:'dialog'}).props['aria-modal'],'false');
 act(()=>view.root.findByProps({'aria-label':'日程标题'}).props.onChange({target:{value:'输入留在草稿'}}));assert.equal(changed.summary,'输入留在草稿');assert.equal(draft.summary,'');
 act(()=>view.root.findByProps({'aria-label':'关闭日程表单'}).props.onClick());assert.equal(closed,1);
 await act(async()=>view.update(React.createElement(CalendarEventEditor,{value:{...draft,existing:event('synced',{sync_state:'synced',remote:{calendar_id:'cal',event_id:'remote'}})},items:[],busy:false,blocked:false,error:'',onChange(){},onSave(){},onClose(){}})));
 assert.equal(view.root.findByProps({type:'checkbox'}).props.disabled,true);assert.equal(view.root.findByProps({'aria-label':'日程标题'}).props.readOnly,undefined);await act(async()=>view.unmount());
});

test('a refreshed local title and version replace an old inspection without unnecessary local permission calls',async()=>{
 let current=event('local'),calls=0;
 const c=new week.ScheduleWeekController(async(method)=>{calls++;assert.equal(method,'load_schedule_week');return {...c.page,events:[current]};},day,storage());
 c.page={...c.page,events:[current]};c.inspections.set('tebb-local/local',current);
 await c.prepareVisible();assert.equal(calls,0);
 current={...current,summary:'改过的标题',local_version:2};await c.read();
 assert.equal(c.event('tebb-local/local').summary,'改过的标题');assert.equal(c.event('tebb-local/local').local_version,2);
});

test('drag preview never registers a calendar event, does not rerender the grid, and permits repeated lifting',async()=>{
 const saved=global.document;global.document={body:{}};let callbacks,gridRenders=0,commits=[];
 const {CalendarDragSurface}=load('calendar-drag.tsx',{'./schedule-state':state,'react-dom':{createPortal:node=>node},'@dnd-kit/core':{
  DndContext:props=>{callbacks=props;return React.createElement('div',{},props.children);},DragOverlay:({children})=>React.createElement('aside',{},children),
  PointerSensor(){},useSensor:()=>({}),useSensors:()=>[],useDraggable:()=>({})}});
 function Grid(){gridRenders++;return React.createElement('div',{'data-grid':true});}
 const source={kind:'event',event:event('same')},children=React.createElement(Grid);let view;
 await act(async()=>{view=create(React.createElement(CalendarDragSurface,{children,reduce:true,onStart(){},project:(source,delta)=>({time:{start:source.event.start,end:source.event.end},style:{top:delta.y,left:0}}),onCommit:(source,time)=>commits.push([source,time]),onFinish(){}}));});
 for(let i=0;i<3;i++){
  act(()=>callbacks.onDragStart({active:{data:{current:{source}}}}));
  for(let y=0;y<8;y++)act(()=>callbacks.onDragMove({delta:{x:0,y}}));
  assert.equal(view.root.findAllByProps({'data-calendar-drop-preview':true}).length,1);assert.equal(gridRenders,1);
  act(()=>callbacks.onDragEnd({delta:{x:0,y:8}}));assert.equal(view.root.findAllByProps({'data-calendar-drop-preview':true}).length,0);
 }
 assert.equal(commits.length,3);assert(commits.every(([e])=>e===source));await act(async()=>view.unmount());global.document=saved;
});

test('item source searches unarchived titles and offers the same item through drag and keyboard arrangement',async()=>{
 const registered=[];let arranged;
 const {CalendarItemSource}=load('calendar-item-source.tsx',{'./calendar-item-source.css':{},'@dnd-kit/core':{useDraggable:options=>{
   registered.push(options);return {setNodeRef(){},setActivatorNodeRef(){},attributes:{},listeners:{},isDragging:false};}}});
 const items=[{id:'active-a',title:'Node 项目',version:1,archived:false},{id:'active-b',title:'Python 项目',version:2,archived:false},{id:'old',title:'归档项目',version:1,archived:true}];let view;
 await act(async()=>{view=create(React.createElement(CalendarItemSource,{items,disabled:false,loading:false,onArrange:item=>arranged=item}));});
 assert.equal(view.root.findAllByType('li').length,2);assert(registered.every(row=>row.data.source.kind==='item'&&row.data.source.item.id!=='old'));
 act(()=>view.root.findByProps({'aria-label':'搜索可安排事项'}).props.onChange({target:{value:'NODE'}}));
 assert.equal(view.root.findAllByType('li').length,1);act(()=>view.root.findByProps({'aria-label':'安排时间：Node 项目'}).props.onClick());assert.equal(arranged,items[0]);
 await act(async()=>view.update(React.createElement(CalendarItemSource,{items,disabled:true,loading:false,onArrange(){}})));
 assert(view.root.findAllByType('button').every(button=>button.props.disabled));assert.equal(registered.at(-1).disabled,true);
 await act(async()=>view.unmount());
});

test('interaction suspends stale reads and inspection, while pending saves cannot trigger a permission downgrade',async()=>{
 let resolveRead,resolveSave,reads=0,inspects=0;const current=event('moving');
 const c=new week.ScheduleWeekController(async(method,args)=>{if(method==='load_schedule_week'){reads++;return new Promise(resolve=>resolveRead=resolve);}
  if(method==='inspect_schedule_event'){inspects++;return {ok:true,event:{...current,editable:false}};}
  return new Promise(resolve=>resolveSave=()=>resolve({ok:true,change_state:'committed',operation:{id:args.action.request_id,state:'retimed',event:{...current,start:args.action.start,end:args.action.end}}}));
 },day,storage());c.page={...c.page,events:[current]};
 const old=c.read();c.beginInteraction();assert.equal(await c.read(),false);assert.equal(await c.inspect(current),false);
 resolveRead({...c.page,events:[]});await old;assert.equal(c.page.events.length,1);assert.equal(reads,1);assert.equal(inspects,0);
 c.endInteraction();const saving=c.retime(current,{start:day+'T11:00:00+08:00',end:day+'T12:00:00+08:00'});
 assert.equal(await c.inspect(current),false);assert.equal(inspects,0);resolveSave();assert.equal(await saving,true);assert(week.timeEditable(c.event('tebb-local/moving')));
});
