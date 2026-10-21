const {test}=require('node:test'),assert=require('node:assert/strict');
const React=require('react'),{act,create}=require('react-test-renderer');
const load=require('./load-client.cjs');
const state=load('schedule-state.ts');
const workState=load('work-board-state.ts',{'./schedule-state':state});
const memory=()=>{const entries=new Map();return {getItem:key=>entries.get(key)||null,setItem:(key,value)=>entries.set(key,value),removeItem:key=>entries.delete(key),entries};};
const page=(day='2026-10-22')=>({ok:true,day,item_id:'item',calendar:{id:'cal',summary:'offline',writable:true},events:[],events_valid:true,events_error:'',checked_at:'',links:[],items:[{id:'item',title:'work',version:1,archived:false}],operations:[]});

test('date navigation ignores an old read response without erasing the newer selected day',async()=>{
  const resolvers=[];const c=new state.ScheduleController(()=>new Promise(r=>resolvers.push(r)),'item','2026-10-22');
  const old=c.read('2026-10-22',true),recent=c.read('2026-10-23',true);
  resolvers[1](page('2026-10-23'));await recent;resolvers[0](page('2026-10-22'));await old;
  assert.equal(c.page.day,'2026-10-23');assert.equal(c.loading,false);
});
test('unknown create survives a component restart and only reads its original receipt',async()=>{
  const storage=memory();let calls=0,request;
  const transport=async(method,args)=>{if(method==='change_schedule'){calls++;request=args.action;throw Error('lost response');}
    return {...page(),operation:{id:request.request_id,item_id:'item',type:'create',state:'linked',stage:'committed',error:''}};};
  let c=new state.ScheduleController(transport,'item','2026-10-22',storage,()=> 'stable-request');
  assert.equal(await c.change({type:'create',item_id:'item',version:1,day:'2026-10-22',summary:'work'}),false);
  assert.equal(c.unknown.request_id,'stable-request');assert.equal(storage.entries.size,1);
  c=new state.ScheduleController(transport,'item','2026-10-22',storage);
  assert.equal(await c.change({type:'create',item_id:'item',version:1,day:'2026-10-22'}),false);
  await c.read();assert.equal(c.unknown,null);assert.equal(storage.entries.size,0);assert.equal(calls,1);
});
test('a missing receipt keeps the original unknown write blocked and never replays it',async()=>{
  let writes=0;const c=new state.ScheduleController(async(method)=>method==='load_schedule'?page():(writes++,Promise.reject(Error('closed'))),'item','2026-10-22',memory(),()=> 'unknown-request');
  await c.change({type:'create',item_id:'item',version:1,day:'2026-10-22'});await c.read();
  assert(c.unknown);assert.match(c.notice,/尚未查到/);assert.equal(writes,1);
});

test('explicit reconciliation closes an unregistered identity without replaying its create',async()=>{
  const calls=[],storage=memory();let n=0;
  const c=new state.ScheduleController(async(method,args)=>{if(method==='load_schedule')return page();calls.push(args.action);if(calls.length===1)throw Error('closed before registration');return {...page(),operation:{id:args.action.request_id,item_id:'item',type:'resolve',state:'resolved_absent',stage:'committed',error:''}};},'item','2026-10-22',storage,()=>`missing-${++n}`);
  await c.change({type:'create',item_id:'item',version:1,day:'2026-10-22'});await c.read();assert.equal(c.missingReceipt,true);
  assert.equal(await c.reconcile({type:'resolve',item_id:'item',version:1,day:'2026-10-22',operation_id:'missing-1',resolution:'absent',confirmed:true}),true);
  assert.equal(c.unknown,null);assert.equal(calls.length,2);assert.equal(calls[1].type,'resolve');assert.equal(storage.entries.size,0);
});

test('a read started before mutation cannot replace its confirmed event and item version',async()=>{
  let finishRead;const c=new state.ScheduleController(async(method)=>method==='load_schedule'?new Promise(resolve=>finishRead=resolve):{...page(),items:[{id:'item',title:'new',version:2,archived:false}]},'item','2026-10-22',memory(),()=> 'save-new');
  const reading=c.read();await c.change({type:'link',item_id:'item',version:1,day:'2026-10-22'});
  finishRead(page());await reading;assert.equal(c.page.items[0].version,2);assert.equal(c.loading,false);
});
test('definite validation failure retains the snapshot and does not leave a phantom pending create',async()=>{
  const storage=memory();const c=new state.ScheduleController(async()=>({ok:false,error:'invalid time',change_state:'not_changed'}),'item','2026-10-22',storage,()=> 'invalid-request');c.page=page();
  assert.equal(await c.change({type:'create',item_id:'item',version:1,day:'2026-10-22'}),false);
  assert.equal(c.page.items[0].title,'work');assert.equal(c.unknown,null);assert.equal(storage.entries.size,0);assert.match(c.error,/invalid/);
});
test('absolute and display times are explicitly Beijing time across UTC midnight',()=>{
  assert.equal(state.beijingDay('2026-10-21T18:00:00Z'),'2026-10-22');
  assert.equal(state.absoluteTime('2026-10-22T09:30'),'2026-10-22T09:30:00+08:00');
  assert.equal(state.beijingStamp('2026-10-21T18:00:00Z'),'2026-10-22T02:00');
});

function component(){
  global.crypto=require('node:crypto').webcrypto;global.window={setInterval:()=>1,clearInterval(){},localStorage:memory()};global.document={activeElement:null};
  return load('schedule.tsx',{'./api':{},'./page-stack':{usePageActive:()=>true},'./schedule-state':state,'./schedule.css':{},
    './datetime-field':{DateTimeField:props=>React.createElement('input',{'aria-label':props.marker+' time',value:props.value,disabled:props.disabled,onChange:event=>props.onChange(event.target.value)})}}).SchedulePane;
}
test('real schedule component explicitly creates, handles unsaved forms, links and navigates',async()=>{
  const work=workState.workBoardFixture(),transport=state.createScheduleDemo(()=>work),SchedulePane=component();
  let changed=0,returned='',closed=false,view;
  await act(async()=>{view=create(React.createElement(SchedulePane,{info:null,debug:true,transport,target:{item_id:'demo-keyboard'},onClose:()=>closed=true,onReturn:id=>returned=id,onChange:()=>changed++}));});
  const button=label=>view.root.findAllByType('button').find(b=>(b.children||[]).join('')===label);
  const json=()=>JSON.stringify(view.toJSON());
  act(()=>button('＋ 新建并关联安排').props.onClick());
  act(()=>view.root.findByProps({'aria-label':'安排标题'}).props.onChange({target:{value:'explicit work'}}));
  act(()=>view.root.findByProps({'aria-label':'关闭日程安排'}).props.onClick());assert(json().includes('未保存的安排'));assert.equal(closed,false);
  act(()=>button('继续编辑').props.onClick());
  const day=state.beijingDay(new Date(Date.now()+86400000).toISOString());
  act(()=>view.root.findByProps({'aria-label':'start time'}).props.onChange({target:{value:day+'T23:00'}}));
  act(()=>view.root.findByProps({'aria-label':'end time'}).props.onChange({target:{value:state.beijingDay(new Date(Date.now()+2*86400000).toISOString())+'T01:00'}}));
  await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));
  assert(json().includes('explicit work'));assert.equal(work.items[0].schedules.events.length,1);assert.equal(work.items[0].state,'todo');assert(changed>0);
  await act(async()=>button('解除本地关联').props.onClick());assert.equal(work.items[0].schedules.events.length,0);
  await act(async()=>button('关联到本事项').props.onClick());assert.equal(work.items[0].schedules.events.length,1);
  act(()=>button('定位事项').props.onClick());assert.equal(returned,'demo-keyboard');
  act(()=>view.unmount());
});
test('real component keeps a failed creation draft and reconciles without another mutation',async()=>{
  const work=workState.workBoardFixture(),server=state.createScheduleDemo(()=>work);let writes=0,action;
  const transport=async(method,args)=>{if(method==='change_schedule'){writes++;action=args.action;throw Error('disconnected');}
    const result=await server(method,args);if(action){const operation={id:action.request_id,item_id:action.item_id,type:'create',state:'unconfirmed',stage:'remote',error:'unknown'};result.operations=[operation];result.operation=operation;}return result;};
  const SchedulePane=component();let view;
  await act(async()=>{view=create(React.createElement(SchedulePane,{info:null,transport,target:{item_id:'demo-keyboard'},onClose(){},onReturn(){},onChange(){}}));});
  const button=label=>view.root.findAllByType('button').find(b=>(b.children||[]).join('')===label);
  act(()=>button('＋ 新建并关联安排').props.onClick());
  await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));
  assert.equal(view.root.findByProps({'aria-label':'安排标题'}).props.value,'补齐工作看板键盘操作');
  assert(JSON.stringify(view.toJSON()).includes('请求结果未确认'));
  await act(async()=>button('按原编号核对').props.onClick());assert.equal(writes,1);assert.equal(button('确认新建并关联').props.disabled,true);
  act(()=>view.unmount());
});

test('a failed date refresh preserves old times with an explicit unverified label',async()=>{
  const work=workState.workBoardFixture(),server=state.createScheduleDemo(()=>work),SchedulePane=component();let view;
  const transport=async(method,args)=>({...await server(method,args),events_valid:false,events_error:'offline'});
  await act(async()=>{view=create(React.createElement(SchedulePane,{info:null,transport,target:{},onClose(){},onReturn(){},onChange(){}}));});
  const rendered=JSON.stringify(view.toJSON());assert(rendered.includes('offline'));assert(rendered.includes('读取失败 · 未核对'));assert(rendered.includes('阅读与笔记'));
  act(()=>view.unmount());
});

test('opening a card follows the verified event date if Feishu moved it since the cached card',async()=>{
  const SchedulePane=component(),newDay='2026-11-04',oldDay='2026-11-03';let view;
  const event={calendar_id:'cal',event_id:'moved_0',summary:'moved remotely',start:newDay+'T09:00:00+08:00',end:newDay+'T10:00:00+08:00',eligible:true,reason:'',status:'confirmed',verification:'confirmed',error:'',checked_at:new Date().toISOString()};
  const transport=async(_method,args)=>({...page(args.day),links:[{item_id:'item',event}],events:args.day===newDay?[event]:[]});
  await act(async()=>{view=create(React.createElement(SchedulePane,{info:null,transport,target:{item_id:'item',day:oldDay,calendar_id:'cal',event_id:'moved_0'},onClose(){},onReturn(){},onChange(){}}));});
  assert.equal(view.root.findByProps({'aria-label':'查看日程日期'}).props.value,newDay);
  assert(view.root.findAllByType('article').some(a=>a.props['data-calendar-selected']===true));
  act(()=>view.unmount());
});
