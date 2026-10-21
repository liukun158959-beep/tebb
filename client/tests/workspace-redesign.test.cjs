const {test}=require('node:test'),assert=require('node:assert/strict');
const React=require('react'),{act,create}=require('react-test-renderer');
const load=require('./load-client.cjs');
const state=load('schedule-state.ts');
const text=node=>typeof node==='string'?node:(node.children||[]).map(text).join('');
const button=(view,label)=>view.root.findAllByType('button').find(n=>text(n)===label);
const dialog={WorkspaceDialog:({title,children,onClose})=>React.createElement('section',{'role':'dialog','aria-label':title},children,React.createElement('button',{onClick:onClose},'关闭弹窗'))};
test('note reading keeps the same question stream and composer mounted, and source adding is explicit',async()=>{
  const tags={};const motion=new Proxy({},{get:(_,tag)=>tags[tag]||=(React.forwardRef(({children,...props},ref)=>React.createElement(tag,{...props,ref},children)))});
  const {NoteMode}=load('note.tsx',{'framer-motion':{motion},'./workspace-dialog':dialog,'./side-workspace':{previewQuote(){}},'./loading-text':{LoadingText:()=>null},'./Markdown':{Markdown:({text})=>React.createElement('p',{},text)}});
  let view,add=0,mounts=0;function Composer(){React.useEffect(()=>{mounts++;},[]);return React.createElement('textarea',{defaultValue:'保留输入'});}
  await act(async()=>{view=create(React.createElement(NoteMode,{sessionId:'s',book:{docs:[],turns:[{role:'user',text:'问答仍然存在'}],notes:[{id:'n',question:'成果',answer:'正文',saved:'2026-10-22',cites:[],files:[]}]},checked:[],busy:false,error:'',status:'',saveStatus:'',saveBad:false,docUrl:'',onAddSources:()=>add++,onCheck(){},onSave(){}},React.createElement(Composer)));});
  const stream=view.root.findByProps({'data-note-thread':''}),composer=view.root.findByType('textarea');
  await act(async()=>view.root.findByProps({'data-note-answer-toggle':'n'}).props.onClick({preventDefault(){}}));
  assert.equal(view.root.findByProps({'data-note-thread':''}),stream);assert.match(text(stream),/问答仍然存在/);assert.equal(view.root.findByType('textarea'),composer);assert.equal(mounts,1);
  await act(async()=>button(view,'关闭弹窗').props.onClick());assert.equal(view.root.findAllByProps({'data-note-reader':'n'}).length,0);assert.equal(view.root.findByType('textarea'),composer);
  await act(async()=>button(view,'＋ 添加资料').props.onClick());assert.equal(add,1);await act(async()=>view.unmount());
});
test('independent calendar creation preserves future date and never requires or mutates an item',async()=>{
  global.window={localStorage:{getItem(){return null},setItem(){},removeItem(){}}};let made,located,view;
  const {CalendarCreate}=load('calendar-create.tsx',{'./api':{},'./board':load('board.tsx',{'reend-components':{},'./status':{},'./datetime-field':{}}),'./schedule-state':state,'./workspace-dialog':dialog});
  await act(async()=>{view=create(React.createElement(CalendarCreate,{info:null,day:'2026-11-01',items:[],busy:false,error:'',onCreate:async(...args)=>{made=args;return {ok:true,event_id:'real-result-id'};},onSaved:target=>located=target,onClose(){}}));});
  act(()=>view.root.findByProps({'aria-label':'日程标题'}).props.onChange({target:{value:'独立安排'}}));
  await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));
  assert.deepEqual(made,['独立安排','2026-11-01T09:00:00+08:00','2026-11-01T10:00:00+08:00']);assert.deepEqual(located,{day:'2026-11-01',event_id:'real-result-id'});assert.equal(view.root.findByProps({'aria-label':'关联事项'}).props.value,'');await act(async()=>view.unmount());
});
test('linked creation uses the existing receipt controller and a lost response prevents a second write',async()=>{
  global.crypto=require('node:crypto').webcrypto;const records=new Map();global.window={localStorage:{getItem:key=>records.get(key)||null,setItem:(k,v)=>records.set(k,v),removeItem:key=>records.delete(key)}};let calls=0,view;
  const {CalendarCreate}=load('calendar-create.tsx',{'./api':{},'./board':load('board.tsx',{'reend-components':{},'./status':{},'./datetime-field':{}}),'./schedule-state':state,'./workspace-dialog':dialog});
  await act(async()=>{view=create(React.createElement(CalendarCreate,{info:null,day:'2026-11-01',initialItem:'item',items:[{id:'item',title:'关联事项',version:7,archived:false}],transport:async(method,args)=>{assert.equal(method,'change_schedule');calls++;assert.equal(args.action.version,7);throw Error('closed');},busy:false,error:'',onCreate:()=>assert.fail('must use scheduling service'),onSaved(){},onClose(){}}));});
  await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));assert.equal(calls,1);assert.match(text(view.root),/原操作可能已经执行/);assert.equal(button(view,'创建日程').props.disabled,true);
  await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));assert.equal(calls,1);assert(records.size);await act(async()=>view.unmount());
});
test('calendar context derives from actual tool events and never from a generated promise',()=>{
  const {hasCalendarContext}=load('run-progress.tsx',{'./chat-prototype-state':{}});
  assert.equal(hasCalendarContext({events:['tool_end: get_today_agenda  succeeded']}),true);
  assert.equal(hasCalendarContext({events:['已帮你创建日程','tool_end: web_search succeeded']}),false);
  assert.equal(hasCalendarContext(undefined),false);
});
test('source import preserves a rejected link and closes only after a confirmed result',async()=>{
  let close=0,accept=false,calls=[];const {NotebookSources}=load('notebook-sources.tsx',{'./api':{},'./workspace-dialog':dialog});let view;
  await act(async()=>{view=create(React.createElement(NotebookSources,{info:null,docs:[],checked:[],busy:false,debug:false,onCheck(){},onLocal(){},onClose:()=>close++,onAdd:async(...args)=>{calls.push(args);return {ok:accept,error:'写入结果未确认，先核对'};}}));});
  await act(async()=>button(view,'飞书文档').props.onClick());await act(async()=>view.root.findByProps({'aria-label':'飞书文档链接'}).props.onChange({target:{value:'https://example.feishu.cn/docx/offline'}}));
  await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));assert.equal(close,0);assert.equal(calls.length,1);assert.equal(view.root.findByProps({'aria-label':'飞书文档链接'}).props.value,'https://example.feishu.cn/docx/offline');assert.match(text(view.root),/先核对/);
  accept=true;await act(async()=>view.root.findByType('form').props.onSubmit({preventDefault(){}}));assert.equal(close,1);assert.equal(calls.length,2);await act(async()=>view.unmount());
});
