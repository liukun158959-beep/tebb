const {test}=require('node:test');const assert=require('node:assert/strict');
const React=require('react');const {act,create}=require('react-test-renderer');const load=require('./load-client.cjs');
const {UiButton,UiField,UiFeedback}=load('ui-controls.tsx');
const text=node=>typeof node==='string'?node:(node.children||[]).map(text).join('');

test('busy shared button has visible text, native disabled semantics and no invented retry action',()=>{
 let view;act(()=>{view=create(React.createElement(UiButton,{busy:true,busyLabel:'正在保存原操作…'},'保存'));});
 const button=view.root.findByType('button');assert.equal(button.props.disabled,true);assert.equal(button.props['aria-busy'],true);assert.match(text(button),/正在保存原操作/);
 assert.equal(view.root.findAllByType('button').length,1);act(()=>view.unmount());
});

test('shared field binds label, existing description, help and error without replacing input',()=>{
 let view;const render=error=>React.createElement(UiField,{label:'标题',help:'输入帮助',error},React.createElement('input',{'aria-describedby':'existing',value:'保留输入',readOnly:true}));
 act(()=>{view=create(render('标题有误'));});const input=view.root.findByType('input'),id=input.props.id;
 assert.equal(view.root.findByType('label').props.htmlFor,id);assert.equal(input.props['aria-invalid'],true);
 assert.equal(input.props['aria-describedby'],`existing ${id}-help ${id}-error`);
 act(()=>view.update(render(undefined)));assert.equal(view.root.findByType('input'),input);assert.equal(input.props.id,id);assert.equal(input.props.value,'保留输入');assert.equal(input.props['aria-describedby'],`existing ${id}-help`);act(()=>view.unmount());
});

test('conflict and unconfirmed stay distinct from failure and never add an automatic retry',()=>{
 for(const state of ['loading','empty','no_results','success','error','conflict','unconfirmed']){
  let view;act(()=>{view=create(React.createElement(UiFeedback,{state,title:'状态文本'},'业务控制器提供的说明'));});
  const root=view.root.findByType('section');assert.equal(root.props['data-ui-feedback'],state);
  assert.equal(root.props.role,['error','conflict','unconfirmed'].includes(state)?'alert':'status');
  assert.equal(view.root.findAllByType('button').length,0);assert.match(text(root),/状态文本/);act(()=>view.unmount());
 }
});
