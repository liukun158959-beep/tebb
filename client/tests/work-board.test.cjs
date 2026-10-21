const {test}=require('node:test');
const assert=require('node:assert/strict');
const React=require('react');const {act,create}=require('react-test-renderer');
const load=require('./load-client.cjs');
const scheduleState=load('schedule-state.ts');
const state=load('work-board-state.ts',{'./schedule-state':scheduleState});
const clone=value=>structuredClone(value);
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(){const page=state.workBoardFixture();page.items=page.items.slice(0,2);return page;}
function updated(page,id,status,revision){const next=clone(page);const item=next.items.find(i=>i.id===id);item.state=status;item.version++;next.revision=revision;return next;}
function setup(transport){let n=0;const controller=new state.WorkBoardController(transport,()=>`request-${++n}`);controller.page=fixture();controller.loaded=true;return controller;}

test('rapid re-grab saves only the newest queued intent after the first confirmed version',async()=>{
  const calls=[],resolvers=[];const c=setup((method,args)=>{calls.push(args.action);return new Promise(resolve=>resolvers.push(resolve));});
  const id=c.items[0].id;const waiting=c.move(id,'doing');c.move(id,'review');c.move(id,'done');
  assert.equal(c.items[0].state,'done');assert.equal(calls.length,1);
  resolvers[0](updated(c.page,id,'doing',2));await tick();assert.equal(calls.length,2);assert.equal(calls[1].state,'done');assert.equal(calls[1].version,2);
  resolvers[1](updated(c.page,id,'done',3));await waiting;
  assert.equal(c.items[0].state,'done');assert.equal(c.pending.size,0);assert.equal(c.intents.size,0);
});
test('old definite failure does not roll back a later user move',async()=>{
  const calls=[],resolvers=[];const c=setup((_method,args)=>{calls.push(args.action);return new Promise(resolve=>resolvers.push(resolve));});const id=c.items[0].id;
  const waiting=c.move(id,'doing');c.move(id,'review');resolvers[0]({ok:false,error:'old failure',change_state:'not_changed'});await tick();
  assert.equal(c.items[0].state,'review');assert.equal(calls[1].state,'review');resolvers[1](updated(c.page,id,'review',2));await waiting;assert.equal(c.items[0].state,'review');assert.equal(c.rowErrors.size,0);
});
test('a latest definite failure returns to confirmed state and keeps a row error',async()=>{
  const c=setup(async()=>({ok:false,error:'disk failure',change_state:'not_changed'}));const id=c.items[0].id;
  await c.move(id,'done');assert.equal(c.items[0].state,'todo');assert.match(c.rowErrors.get(id),/disk/);
});
test('conflict pauses newer intentions until an explicit read, without automatic write retry',async()=>{
  let resolve,writes=0;const c=setup(async(method)=>method==='load_work_board'?updated(fixture(),c.items[0].id,'doing',2):(++writes,new Promise(r=>resolve=r)));
  const id=c.items[0].id;const waiting=c.move(id,'doing');c.move(id,'done');resolve({ok:false,conflict:true,error:'conflict',change_state:'not_changed'});await waiting;
  assert.equal(writes,1);assert.equal(c.items[0].state,'done');await c.load(true);assert.equal(c.items[0].state,'doing');assert.equal(writes,1);
});
test('disconnect freezes just that card, keeps the latest intention, and reconciles by request identity',async()=>{
  const base=fixture(),id=base.items[0].id;let reject,writes=0;let acknowledged;
  const c=setup(async(method,args={})=>{
    if(method==='change_work_item'){writes++;acknowledged=args.action;return new Promise((_r,j)=>reject=j);}
    return {...updated(base,id,'doing',2),receipt:args.request_id?{request_id:acknowledged.request_id,item_id:id,version:2,operation:'move'}:null};
  });
  const waiting=c.move(id,'doing');c.move(id,'done');reject(Error('disconnected'));await waiting;
  assert.equal(c.uncertain.size,1);assert.equal(c.items[0].state,'done');await c.move(id,'review');assert.equal(writes,1);
  await c.load(true);assert.equal(c.uncertain.size,0);assert.equal(c.items[0].state,'doing');assert.match(c.notice,/尚未保存/);assert.equal(writes,1);
});
test('unconfirmed creation cannot be duplicated; manual retry sends exactly the same action',async()=>{
  const calls=[];const c=setup(async(method,args)=>{
    if(method==='load_work_board')return {...fixture(),receipt:null};
    calls.push(clone(args.action));if(calls.length===1)throw Error('no acknowledgement');
    return {...fixture(),receipt:{request_id:args.action.request_id,item_id:'accepted',version:1,operation:'create'}};
  });const fields={title:'retained',description:'draft',project_id:'',priority:'normal',due:''};
  assert.equal(await c.change({type:'create',fields}),false);assert.equal(await c.change({type:'create',fields}),false);assert.equal(calls.length,1);
  await c.load(true);assert.equal(c.uncertain.size,1);await c.retry('create');assert.deepEqual(calls[1],calls[0]);assert.equal(c.uncertain.size,0);
});
test('out-of-order independent card replies and stale reads preserve every newer card version',async()=>{
  const base=fixture(),first=base.items[0].id,second=base.items[1].id;const resolvers=[];const c=setup(()=>new Promise(resolve=>resolvers.push(resolve)));
  const read=c.load();const one=c.move(first,'doing'),two=c.move(second,'done');
  resolvers[2](updated(base,second,'done',3));await two;
  resolvers[1](updated(base,first,'doing',2));await one;
  resolvers[0](base);await read;
  assert.equal(c.items.find(i=>i.id===first).state,'doing');assert.equal(c.items.find(i=>i.id===second).state,'done');assert.equal(c.page.revision,3);
});
test('reconciling multiple uncertain operations retains the matching form receipt for each card',async()=>{
  const receipts=new Map();const c=setup(async(method,args={})=>{
    if(method==='load_work_board')return {...fixture(),receipt:receipts.get(args.request_id)||null};
    const action=args.action;receipts.set(action.request_id,{request_id:action.request_id,item_id:action.id||'new-item',version:2,operation:action.type});
    throw Error('lost acknowledgement');
  });
  const id=c.items[0].id;
  await c.change({type:'create',fields:{title:'draft',description:'',project_id:'',priority:'normal',due:''}});
  await c.change({type:'archive',id,archived:true});
  const creation=c.uncertain.get('create').action.request_id;
  await c.load(true);
  assert.equal(c.uncertain.size,0);assert.equal(c.completed.get('create').request_id,creation);
  assert.equal(c.completed.get(id).item_id,id);
});
test('editing preserves the version at form-open, not the latest polling version',async()=>{
  const calls=[];const c=setup(async(_m,args)=>{calls.push(args.action);return {ok:false,conflict:true,error:'stale draft',change_state:'not_changed'};});
  const original=clone(c.page.items[0]);c.page=updated(c.page,original.id,'doing',2);
  await c.change({type:'save',id:original.id,version:original.version,fields:{title:'edited',description:'',project_id:'',priority:'normal',due:''}});
  assert.equal(calls[0].version,1);assert.equal(c.items[0].state,'doing');assert.match(c.error,/stale/);
});
test('plan execution success does not disguise unfinished delivery',()=>{
  assert.equal(state.runStateLabel({kind:'plan',state:'succeeded',delivery_state:'running'}),'已委派 · 交付进行中');
  assert.equal(state.runStateLabel({kind:'plan',state:'succeeded',delivery_state:'failed'}),'交付未完整完成');
});

test('real component creates, moves, archives, restores and preserves its edit form',async()=>{
  global.crypto=require('node:crypto').webcrypto;global.window={setInterval:()=>1,clearInterval(){}};global.document={activeElement:null};
  let dragProps,scheduleProps,weekProps;
  const wrapper=tag=>React.forwardRef((props,ref)=>React.createElement(tag,{...props,ref},props.children));
  const dnd={DndContext:props=>{dragProps=props;return React.createElement('div',{},props.children);},DragOverlay:()=>null,
    KeyboardSensor(){},PointerSensor(){},closestCenter(){},pointerWithin:()=>[],useDraggable:()=>({setNodeRef(){},setActivatorNodeRef(){},attributes:{},listeners:{},isDragging:false}),useDroppable:()=>({setNodeRef(){},isOver:false}),useSensor(){},useSensors:()=>[]};
  let projectRemoved=false;
  const componentState={...state,WorkBoardController:class extends state.WorkBoardController {
    constructor(transport){super(async(method,args)=>{const page=await transport(method,args);return projectRemoved?{...page,projects:[],items:page.items.map(item=>({...item,project_id:'',project_name:'',version:item.version+1}))}:page;});}
  }};
  const {WorkBoardPane}=load('work-board.tsx',{'./api':{},'./page-stack':{usePageActive:()=>true},'./work-board-state':componentState,'./schedule-state':scheduleState,'./schedule':{SchedulePane:props=>{scheduleProps=props;return null;}},
    './schedule-week':{ScheduleWeekPane:props=>{weekProps=props;return null;}},
    './Markdown':{Markdown:({text})=>React.createElement('p',{},text)},'./work-board.css':{},'@dnd-kit/core':dnd,'framer-motion':{motion:{article:wrapper('article')},useReducedMotion:()=>true}});
  let view;await act(async()=>{view=create(React.createElement(WorkBoardPane,{info:null,debug:true,onOpen(){throw Error('real navigation forbidden');}}));});
  const button=label=>view.root.findAllByType('button').find(b=>(b.children||[]).join('')===label);
  act(()=>button('＋ 新增事项').props.onClick());
  await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));
  const rendered=()=>JSON.stringify(view.toJSON());assert(rendered().includes('请输入事项标题'));
  act(()=>view.root.findByProps({'aria-label':'事项标题'}).props.onChange({target:{value:'真实组件新事项'}}));
  await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));assert(rendered().includes('真实组件新事项'));
  const article=()=>view.root.findAllByType('article').find(a=>a.props['data-work-card']?.startsWith('demo-new'));
  const id=article().props['data-work-card'];await act(async()=>{dragProps.onDragEnd({active:{id},over:{id:'review'}});});
  assert.equal(article().props['data-state'],'review');
  await act(async()=>button('归档事项').props.onClick());assert(rendered().includes('已归档事项'));
  await act(async()=>button('恢复事项').props.onClick());assert.equal(article().props['data-state'],'review');
  act(()=>button('编辑事项').props.onClick());assert.equal(view.root.findByProps({'aria-label':'事项标题'}).props.value,'真实组件新事项');
  act(()=>view.root.findByProps({'aria-label':'关闭事项详情'}).props.onClick());
  act(()=>button('真实组件新事项').props.onClick());
  await act(async()=>button('归档事项').props.onClick());
  act(()=>view.root.findByProps({'aria-label':'关闭事项详情'}).props.onClick());
  act(()=>view.root.findByProps({'aria-label':'搜索事项'}).props.onChange({target:{value:'挡住目标的搜索'}}));
  act(()=>view.root.findByProps({'aria-label':'筛选项目'}).props.onChange({target:{value:'Atlas'}}));
  await act(async()=>button('日程安排').props.onClick());
  assert(weekProps);
  await act(async()=>weekProps.onDay({day:'2026-11-03'}));assert(scheduleProps);
  await act(async()=>scheduleProps.onWeek({day:'2026-11-04',calendar_id:'cal',event_id:'event_0'}));
  assert.equal(weekProps.target.day,'2026-11-04');
  await act(async()=>weekProps.onReturn(id));
  assert.equal(view.root.findByProps({'aria-label':'搜索事项'}).props.value,'');
  assert.equal(view.root.findByProps({'aria-label':'筛选项目'}).props.value,'all');
  assert(rendered().includes('已归档事项'));assert(rendered().includes('真实组件新事项'));
  act(()=>view.root.findByProps({'aria-label':'关闭事项详情'}).props.onClick());
  act(()=>button('查看日常事项').props.onClick());
  act(()=>view.root.findByProps({'aria-label':'筛选项目'}).props.onChange({target:{value:'tebb'}}));
  projectRemoved=true;await act(async()=>button('刷新核对').props.onClick());
  assert.equal(view.root.findByProps({'aria-label':'筛选项目'}).props.value,'none');
  assert(rendered().includes('补齐工作看板键盘操作'));
  act(()=>view.unmount());
});
