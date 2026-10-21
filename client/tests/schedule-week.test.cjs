const {test}=require('node:test'),assert=require('node:assert/strict');
const React=require('react'),{act,create}=require('react-test-renderer');
const load=require('./load-client.cjs'),shared=load('schedule-state.ts');
const state=load('schedule-week-state.ts',{'./schedule-state':shared});
const workState=load('work-board-state.ts',{'./schedule-state':shared});
const day=shared.addCalendarDays(shared.beijingDay(),1),time=h=>`${day}T${h}:00+08:00`;
const event=(id='event_0',extra={})=>({calendar_id:'cal',event_id:id,summary:'Personal work',start:time('09:00'),end:time('10:00'),
  status:'confirmed',eligible:true,reason:'',verification:'confirmed',error:'',checked_at:new Date().toISOString(),
  editable:true,edit_checked_at:new Date().toISOString(),...extra});
const page=(events=[],extra={})=>({ok:true,week:true,week_start:shared.calendarWeekStart(day),week_end:shared.addCalendarDays(shared.calendarWeekStart(day),7),day,
  item_id:'',events,events_valid:true,events_error:'',checked_at:new Date().toISOString(),links:[],items:[],operations:[],...extra});
const memory=()=>{const entries=new Map();return {entries,getItem:key=>entries.get(key)||null,setItem:(key,value)=>entries.set(key,value),removeItem:key=>entries.delete(key)};};
const deferred=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve:v=>resolve(v)};};
const receipt=(action,current)=>page([current],{change_state:'committed',operation:{id:action.request_id,type:action.type,state:action.type==='retime'?'retimed':'observed',stage:'committed',event:current}});

test('Beijing week boundaries and movement preserve duration; resize observes same-day and minimum rules',()=>{
  assert.equal(shared.calendarWeekStart('2026-11-01'),'2026-10-26');
  assert.equal(shared.calendarWeekStart('2027-01-01'),'2026-12-28');
  const short=event('short_0',{start:time('09:02'),end:time('09:07')});
  const moved=state.dragTimeDraft(short,'move',15,1);
  assert.equal(Date.parse(moved.end)-Date.parse(moved.start),300000);
  assert.equal(shared.beijingStamp(moved.start),shared.addCalendarDays(day,1)+'T09:15');
  assert.equal(state.validTimeDraft(short,moved),'');
  const pointer=state.pointerTimeDraft(short,{start:time('10:02'),end:time('10:07')});
  assert.equal(shared.beijingStamp(pointer.start),day+'T10:00');assert.equal(Date.parse(pointer.end)-Date.parse(pointer.start),300000);
  assert.match(state.validTimeDraft(short,{start:time('09:00'),end:time('09:10')}),/最短/);
  assert.match(state.validTimeDraft(short,{start:time('23:30'),end:shared.addCalendarDays(day,1)+'T00:30:00+08:00'}),/同一/);
  assert.equal(state.validTimeDraft(event(),{start:time('06:00'),end:time('07:00')}),'');
  assert.equal(state.overlappingEvents([event(),event('overlap_0'),event('cancelled_0',{status:'cancelled'})],event(),{start:time('09:30'),end:time('10:30')}).length,1);
});

test('unverified permission, expired inspection and unchanged time never submit',async()=>{
  let writes=0;const c=new state.ScheduleWeekController(async()=>{writes++;},day,memory());
  assert.equal(await c.retime(event('e',{editable:false}),{start:time('11:00'),end:time('12:00')}),false);
  assert.equal(await c.retime(event('e',{edit_checked_at:'2000-01-01'}),{start:time('11:00'),end:time('12:00')}),false);
  assert.equal(await c.retime(event(),{start:time('09:00'),end:time('10:00')}),true);
  assert.equal(writes,0);
});

test('per-event pending blocks duplicates while reads and another event remain available',async()=>{
  const waiting=deferred(),calls=[];
  const c=new state.ScheduleWeekController(async(method,args)=>{
    calls.push([method,args]);if(method==='load_schedule_week')return page([event(),event('other_0')]);
    if(args.action.event_id==='event_0')return waiting.promise;
    return receipt(args.action,event('other_0',{start:time('13:00'),end:time('14:00')}));
  },day,memory(),(()=>{let i=0;return()=>`id-${++i}`;})());
  const saving=c.retime(event(),{start:time('11:00'),end:time('12:00')});
  assert(c.blocked('cal/event_0'));assert.equal(await c.retime(event(),{start:time('13:00'),end:time('14:00')}),false);
  assert.equal(await c.read(),true);
  assert.equal(await c.retime(event('other_0'),{start:time('13:00'),end:time('14:00')}),true);
  const action=calls.find(([method])=>method==='change_schedule')[1].action;
  assert.match(action.start,/T\d{2}:\d{2}:\d{2}Z$/);assert(!action.start.includes('.000'));
  waiting.resolve(receipt(action,event('event_0',{start:time('11:00'),end:time('12:00')})));
  assert.equal(await saving,true);assert.equal(c.pending.size,0);
  assert.equal(c.event('cal/other_0').start,time('13:00'));
  assert.equal(calls.filter(([m])=>m==='change_schedule').length,2);
});

test('unknown write survives restart; reads do not replay and a historical receipt does not overwrite current time',async()=>{
  const storage=memory();let writes=0,original;
  const transport=async(method,args)=>{
    if(method==='change_schedule'){writes++;original=args.action;throw Error('closed');}
    return page([event('event_0',{start:time('15:00'),end:time('16:00')})],args.request_id?{operation:{id:original.request_id,type:'retime',state:'retimed',event:event('event_0',{start:time('11:00'),end:time('12:00')})}}:{});
  };
  let c=new state.ScheduleWeekController(transport,day,storage,()=> 'stable-id');
  assert.equal(await c.retime(event(),{start:time('11:00'),end:time('12:00')}),false);
  c=new state.ScheduleWeekController(transport,day,storage);
  assert(c.blocked('cal/event_0'));await c.read();
  assert.equal(c.blocked('cal/event_0'),false);assert.equal(c.event('cal/event_0').start,time('15:00'));
  assert.equal(writes,1);assert.equal(storage.entries.size,0);
});

test('missing receipt stays blocked until explicit current-state reconciliation, without replaying retime',async()=>{
  const storage=memory(),calls=[];let n=0;
  const c=new state.ScheduleWeekController(async(method,args)=>{
    if(method==='load_schedule_week')return page([event()]);
    if(method==='inspect_schedule_event')return {ok:true,event:event()};
    calls.push(args.action);if(args.action.type==='retime')throw Error('closed');
    return receipt(args.action,event());
  },day,storage,()=>`id-${++n}`);
  await c.retime(event(),{start:time('11:00'),end:time('12:00')});await c.read();
  assert(c.missing.has('cal/event_0'));assert(c.blocked('cal/event_0'));
  await c.inspect(event());assert.equal(await c.reconcile('cal/event_0'),true);
  assert.equal(calls.length,2);assert.equal(calls[1].type,'reconcile_time');assert.equal(calls[1].operation_id,'id-1');
  assert.equal(c.blocked('cal/event_0'),false);
});

test('receipt polling adopts the newly committed current cache while ignoring historical event snapshots',async()=>{
  let action;const storage=memory(),current=event('event_0',{start:time('11:00'),end:time('12:00')});
  const c=new state.ScheduleWeekController(async(method,args)=>{
    if(method==='change_schedule'){action=args.action;throw Error('closed');}
    return args.request_id?receipt(action,current):page([event()]);
  },day,storage,()=> 'receipt-later');
  await c.retime(event(),{start:time('11:00'),end:time('12:00')});await c.read();
  assert.equal(c.event('cal/event_0').start,time('11:00'));assert.equal(c.blocked('cal/event_0'),false);
});

test('failed reconciliation retains original identity; definitively rejected write restores latest remote observation',async()=>{
  let action;const c=new state.ScheduleWeekController(async(method,args)=>{
    if(method==='load_schedule_week')return page();
    action=args.action;
    if(args.action.type==='retime')throw Error('lost');
    return {ok:false,change_state:'not_changed',error:'original still active'};
  },day,memory(),(()=>{let i=0;return()=>`id-${++i}`;})());
  await c.retime(event(),{start:time('11:00'),end:time('12:00')});c.inspections.set('cal/event_0',event());
  await c.reconcile('cal/event_0');assert.equal(c.unknown.get('cal/event_0').request_id,'id-1');
  assert.equal(action.operation_id,'id-1');
  const latest=event('event_0',{start:time('08:00')});
  const rejected=new state.ScheduleWeekController(async()=>({ok:false,change_state:'not_changed',error:'changed',operation:{event:latest}}),day,memory(),()=> 'reject');
  await rejected.retime(event(),{start:time('11:00'),end:time('12:00')});
  assert.equal(rejected.event('cal/event_0').start,time('08:00'));assert.equal(rejected.blocked('cal/event_0'),false);
});

test('older reads and inspections cannot undo a completed mutation',async()=>{
  const reading=deferred(),inspecting=deferred();
  const c=new state.ScheduleWeekController(async(method,args)=>method==='load_schedule_week'?reading.promise:method==='inspect_schedule_event'?inspecting.promise:receipt(args.action,event('event_0',{start:time('11:00'),end:time('12:00')})),day,memory(),()=> 'new');
  const oldRead=c.read(),oldInspect=c.inspect(event());
  // Inspection is not a lease: use another observed event to exercise stale callback protection.
  c.checking.clear();await c.retime(event(),{start:time('11:00'),end:time('12:00')});
  reading.resolve(page([event()]));inspecting.resolve({ok:true,event:event()});await oldRead;await oldInspect;
  assert.equal(c.event('cal/event_0').start,time('11:00'));assert.equal(c.loading,false);assert.equal(c.checking.size,0);
});

test('storage failure prevents submission and damaged markers are preserved across reads',async()=>{
  let writes=0;const storage=memory();storage.setItem=()=>{throw Error('quota');};
  const c=new state.ScheduleWeekController(async()=>{writes++;},day,storage);
  await c.retime(event(),{start:time('11:00'),end:time('12:00')});assert.equal(writes,0);assert.equal(c.pending.size,0);
  const damaged=memory();damaged.setItem('tebb.schedule.week.pending.v1','broken');
  const broken=new state.ScheduleWeekController(async()=>page(),day,damaged);
  await broken.read();assert.equal(broken.blocked('cal/event_0'),true);assert.equal(damaged.getItem('tebb.schedule.week.pending.v1'),'broken');
});

test('a detail response cannot undo a newer week observation',async()=>{
  // Declare observation order explicitly; constructing fixtures milliseconds apart
  // must not accidentally give the "newer" week an older checked_at timestamp.
  const old=deferred(),latest=event('event_0',{start:time('08:30'),checked_at:'2026-01-01T00:00:02Z'});
  const c=new state.ScheduleWeekController(async method=>method==='inspect_schedule_event'?old.promise:page([latest]),day,memory());
  c.page=page([event('event_0',{checked_at:'2026-01-01T00:00:00Z'})]);const pending=c.inspect(event());await c.read();
  old.resolve({ok:true,event:event('event_0',{checked_at:'2026-01-01T00:00:01Z'})});await pending;
  assert.equal(c.event('cal/event_0').start,time('08:30'));assert.equal(c.checking.size,0);
});

test('the day and week demo share creations, associations and revised event identity',async()=>{
  const work=workState.workBoardFixture(),transport=shared.createScheduleDemo(()=>work),date=shared.beijingDay();
  const item=work.items[0];
  const made=await transport('change_schedule',{action:{type:'create',request_id:'make',item_id:item.id,version:item.version,day:date,summary:'shared test',start:date+'T22:00:00+08:00',end:date+'T23:00:00+08:00'}});
  const current=made.operation.event;
  const result=await transport('change_schedule',{action:{type:'retime',request_id:'move',calendar_id:current.calendar_id,event_id:current.event_id,day:date,original_start:current.start,original_end:current.end,start:date+'T21:00:00+08:00',end:date+'T22:00:00+08:00'}});
  const single=await transport('load_schedule',{day:date,item_id:item.id}),week=await transport('load_schedule_week',{day:date});
  assert.equal(single.events.find(e=>e.event_id===current.event_id).start,result.operation.event.start);
  assert.equal(week.events.find(e=>e.event_id===current.event_id).start,result.operation.event.start);
  assert.equal(work.items[0].schedules.events.find(e=>e.event_id===current.event_id).start,result.operation.event.start);
});

test('real week component keeps FullCalendar time-zone/gesture gates and guards dirty form navigation',async()=>{
  global.crypto=require('node:crypto').webcrypto;
  global.window={setInterval:()=>1,clearInterval(){},setTimeout,localStorage:memory()};
  const listeners=new Map();global.document={addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)};
  let calendarProps,closed=false,writes=0;
  const Calendar=React.forwardRef((props,ref)=>{calendarProps=props;React.useImperativeHandle(ref,()=>({getApi:()=>({changeView(){},updateSize(){}})}));return React.createElement('div',{'data-calendar-stub':true});});
  const Week=load('schedule-week.tsx',{'./api':{},'./page-stack':{usePageActive:()=>true},'./schedule-state':shared,'./schedule-week-state':state,'./schedule-week.css':{},
    '@fullcalendar/react':{default:Calendar},'@fullcalendar/timegrid':{},'@fullcalendar/interaction':{},'@fullcalendar/luxon3':{},'@fullcalendar/core/locales/zh-cn':{},
    'framer-motion':{useReducedMotion:()=>true},'./datetime-field':{DateTimeField:props=>React.createElement('input',{'aria-label':props.marker,value:props.value,onChange:e=>props.onChange(e.target.value)})}}).ScheduleWeekPane;
  const current=event();let view;
  const transport=async(method,args)=>{if(method==='load_schedule_week')return page([current]);if(method==='inspect_schedule_event')return {ok:true,event:current};writes++;return receipt(args.action,{...current,start:args.action.start,end:args.action.end});};
  await act(async()=>{view=create(React.createElement(Week,{info:null,transport,target:{day},onClose:()=>closed=true,onReturn(){},onDay(){},onChange(){}}));});
  assert.equal(calendarProps.timeZone,'Asia/Shanghai');assert.equal(calendarProps.snapDuration,'00:15:00');assert.equal(calendarProps.eventResizableFromStart,true);
  const button=label=>view.root.findAllByType('button').find(b=>b.children.join('')===label);
  act(()=>button('修改时间').props.onClick());
  act(()=>view.root.findByProps({'aria-label':'start'}).props.onChange({target:{value:day+'T10:30'}}));
  act(()=>button('返回工作看板').props.onClick());assert.equal(closed,false);assert(view.root.findByProps({role:'alertdialog'}));
  act(()=>button('继续编辑').props.onClick());
  // Invalid candidate is rejected before either drag or resize can write.
  assert.equal(calendarProps.eventAllow({allDay:false,start:new Date(time('23:30')),end:new Date(shared.addCalendarDays(day,1)+'T00:30:00+08:00')},{extendedProps:{source:current}}),false);
  act(()=>button('放弃表单').props.onClick());
  let reverted=0;
  act(()=>calendarProps.eventDragStart());
  // Pointer capture does not guarantee keyboard focus inside the component root.
  act(()=>listeners.get('keydown')({key:'Escape',preventDefault(){}}));
  await act(async()=>calendarProps.eventDrop({oldEvent:{extendedProps:{source:current}},event:{start:new Date(time('11:00')),end:new Date(time('12:00'))},revert:()=>reverted++}));
  assert.equal(reverted,1);assert.equal(writes,0);
  act(()=>view.unmount());
});
