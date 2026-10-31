const {test}=require('node:test'),assert=require('node:assert/strict');
const React=require('react'),{act,create}=require('react-test-renderer');
const load=require('./load-client.cjs');
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
test('calendar context derives from actual tool events and never from a generated promise',()=>{
  const {hasCalendarContext}=load('run-progress.tsx',{'./chat-prototype-state':{},'./chart-building':{ChartBuildProgress:()=>null},'./task-budget':load('task-budget.tsx')});
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
