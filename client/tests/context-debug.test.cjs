const {test,assert,React,create,act,loader,text,button,registerMarkdown}=require('./component-fixture.cjs');
const inspect=()=>({ok:true,items:[{id:'call',created:1,model:'model',agent:'tebb',phase:'route',run_id:'root',state:'succeeded'}],has_more:false,selected:'call',model_id:'m1',recording:true,agents:[{id:'tebb',name:'tebb',memory:'当前偏好'}],budget:{input_tokens:4000,input_source:'provider',window:null,output_reserve:0,remaining:null,over_by:0,capacity_source:'unknown'},detail:{request:{model:'model',messages:[{role:'system',content:'规则'},{role:'user',content:'完整请求的末尾'.repeat(2500)}],tools:[{name:'safe'}]},request_raw:'完整JSON末尾',call:{state:'succeeded'}}});

test('history preparation with unknown capacity never invents a percentage or automatic compression',async()=>{
 const load=loader({'./Markdown':{PlainLinks:()=>null}});const {ContextCard}=load('memory');let view;
 const context={budget:null,tool_reserve:null,before:20000,estimate:20000,saved:0,summary:'',covered:[],over:null};
 await act(async()=>{view=create(React.createElement(ContextCard,{context,busy:false,onCompress:async()=>true,variant:'bar'}));});
 assert(text(view.toJSON()).includes('容量未知'));assert(!text(view.toJSON()).includes('%'));
 assert(!text(view.toJSON()).includes('128000'));assert(!text(view.toJSON()).includes('自动压缩'));
 assert.equal(view.root.findByProps({'data-context':''}).props['data-context-over'],'unknown');
 await act(async()=>view.unmount());
});

test('an unsent conversation never queries an old conversation or exposes its memory as the new request',async()=>{
 global.window={setInterval:()=>1,clearInterval(){}};const calls=[];
 const load=loader({'./api':{rpc:async(...args)=>{calls.push(args);return inspect();}}});const {ContextDebug}=load('context-debug');let view;
 await act(async()=>{view=create(React.createElement(ContextDebug,{info:{port:1,token:'t'},conversation:''}));});
 await act(async()=>button(view,'上下文').props.onClick());
 assert.deepEqual(calls,[]);assert.equal(button(view,'记忆').props.disabled,true);
 await act(async()=>view.unmount());
});

test('ordinary composer loads actual context only after opening and never invents window percentage',async()=>{
 global.window={setInterval:()=>1,clearInterval(){}};const calls=[];const load=loader({'./api':{rpc:async(_info,method,args)=>{calls.push({method,args});return inspect();}}});
 const {ContextDebug}=load('context-debug');let v;await act(async()=>{v=create(React.createElement(ContextDebug,{info:{port:1,token:'t'},conversation:'dm-tebb'}));});
 assert.equal(calls.length,0);await act(async()=>button(v,'上下文').props.onClick());
 assert.equal(calls[0].method,'load_context_debug');assert.equal(calls[0].args.conversation,'dm-tebb');
 const meter=v.root.find(n=>n.props.role==='progressbar');assert.equal(meter.props['aria-valuenow'],undefined);assert(text(v.toJSON()).includes('容量待配置'));assert(!text(v.toJSON()).includes('128000'));
 await act(async()=>button(v,'2. 你的问题user').props.onClick());
 assert(v.root.findAllByType('pre').some(n=>text(n).endsWith('完整请求的末尾"\n}')));
 await act(async()=>button(v,'调用参数').props.onClick());assert(text(v.toJSON()).includes('实际有效参数'));
 await act(async()=>button(v,'记忆').props.onClick());assert(text(v.toJSON()).includes('这位伙伴记住的偏好'));assert.equal(v.root.findByProps({'aria-label':'伙伴记忆内容'}).props.value,'当前偏好');assert(!text(v.toJSON()).includes('旧助手'));assert(!calls.some(c=>c.method==='explain_debug_call'));
 await act(async()=>v.unmount());
});

test('capacity editing cannot submit the surrounding chat form',async()=>{
 global.window={setInterval:()=>1,clearInterval(){}};const calls=[];let submitted=0;
 const load=loader({'./api':{rpc:async(_info,method,args)=>{calls.push({method,args});return method==='save_context_capacity'?{ok:true}:inspect();}}});const {ContextDebug}=load('context-debug');let v;
 await act(async()=>{v=create(React.createElement('form',{onSubmit:()=>submitted++},React.createElement(ContextDebug,{info:{port:1,token:'t'},conversation:'dm-tebb'})));});
 await act(async()=>button(v,'上下文').props.onClick());await act(async()=>v.root.findByProps({'aria-label':'配置模型容量与分词器'}).props.onClick());
 assert.equal(v.root.findAllByType('form').length,1);
 await act(async()=>v.root.findByProps({'aria-label':'模型窗口总量'}).props.onChange({target:{value:'10000'}}));
 await act(async()=>v.root.findByProps({'aria-label':'输出预留tokens'}).props.onChange({target:{value:'1000'}}));
 await act(async()=>button(v,'保存容量配置').props.onClick());
 assert.equal(submitted,0);assert.deepEqual(calls.find(c=>c.method==='save_context_capacity').args,{model_id:'m1',window:10000,output_reserve:1000,tokenizer_dir:''});await act(async()=>v.unmount());
});

test('known model capacity with missing usage and tokenizer has no invented progress',async()=>{
 global.window={setInterval:()=>1,clearInterval(){}};const value=inspect();value.budget={...value.budget,window:128000,input_tokens:null,input_source:'unavailable'};
 const load=loader({'./api':{rpc:async()=>value}});const {ContextDebug}=load('context-debug');let v;await act(async()=>{v=create(React.createElement(ContextDebug,{info:{port:1,token:'t'},conversation:'dm-tebb'}));});await act(async()=>button(v,'上下文').props.onClick());assert.equal(v.root.findByProps({role:'progressbar'}).props['aria-valuenow'],undefined);assert(text(v.toJSON()).includes('未取得可靠统计'));await act(async()=>v.unmount());
});

test('partner memory saves only its own field and uncertain writes require rereading',async()=>{
 global.window={setInterval:()=>1,clearInterval(){}};global.crypto=require('node:crypto').webcrypto;const calls=[];let fail=true;
 const load=loader({'./api':{rpc:async(_i,method,args)=>{calls.push({method,args});if(method==='collaboration_action'&&fail)throw Error('断连');return inspect();}}});const {ContextDebug}=load('context-debug');let v;
 await act(async()=>{v=create(React.createElement(ContextDebug,{info:{port:1,token:'t'},conversation:'dm-tebb'}));});await act(async()=>button(v,'记忆').props.onClick());await act(async()=>button(v,'编辑').props.onClick());await act(async()=>v.root.findByProps({'aria-label':'伙伴记忆内容'}).props.onChange({target:{value:'新偏好'}}));await act(async()=>button(v,'保存修改').props.onClick());
 const action=calls.find(c=>c.method==='collaboration_action').args.action;assert.equal(action.type,'partner_memory');assert.equal(action.memory,'新偏好');assert.equal(action.agent_id,'tebb');assert.equal(action.agent,undefined);assert(button(v,'保存修改').props.disabled);assert.equal(calls.filter(c=>c.method==='collaboration_action').length,1);
 fail=false;await act(async()=>button(v,'重新读取核对').props.onClick());assert.equal(v.root.findByProps({'aria-label':'伙伴记忆内容'}).props.value,'当前偏好');await act(async()=>v.unmount());
});
