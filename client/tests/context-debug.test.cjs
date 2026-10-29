const {test,assert,React,create,act,loader,text,button,registerMarkdown}=require('./component-fixture.cjs');
const inspect=()=>({ok:true,items:[{id:'call',created:1,model:'model',agent:'tebb',phase:'route',run_id:'root',state:'succeeded'}],has_more:false,selected:'call',model_id:'m1',recording:true,agents:[{id:'tebb',name:'tebb',memory:'当前偏好'}],budget:{input_tokens:4000,input_source:'provider',window:null,output_reserve:0,remaining:null,over_by:0,capacity_source:'unknown'},detail:{request:{model:'model',messages:[{role:'system',content:'规则'},{role:'user',content:'完整请求的末尾'.repeat(2500)}],tools:[{name:'safe'}]},request_raw:'完整JSON末尾',call:{state:'succeeded'}}});

test('ordinary composer loads actual context only after opening and never invents window percentage',async()=>{
 global.window={setInterval:()=>1,clearInterval(){}};const calls=[];const load=loader({'./api':{rpc:async(_info,method,args)=>{calls.push({method,args});return inspect();}}});
 const {ContextDebug}=load('context-debug');let v;await act(async()=>{v=create(React.createElement(ContextDebug,{info:{port:1,token:'t'},conversation:'dm-tebb'}));});
 assert.equal(calls.length,0);await act(async()=>v.root.findByType('details').props.onToggle({currentTarget:{open:true}}));
 assert.equal(calls[0].method,'load_context_debug');assert.equal(calls[0].args.conversation,'dm-tebb');
 const meter=v.root.find(n=>n.props.role==='progressbar');assert.equal(meter.props['aria-valuenow'],undefined);assert(text(v.toJSON()).includes('容量待配置'));assert(!text(v.toJSON()).includes('128000'));
 assert(v.root.findAllByType('pre').some(n=>text(n).endsWith('完整请求的末尾"\n}')));
 await act(async()=>button(v,'调用参数').props.onClick());assert(text(v.toJSON()).includes('实际有效参数'));
 await act(async()=>button(v,'伙伴记忆').props.onClick());assert(text(v.toJSON()).includes('当前偏好'));assert(!calls.some(c=>c.method==='explain_debug_call'));
 await act(async()=>v.unmount());
});

test('capacity editing cannot submit the surrounding chat form',async()=>{
 global.window={setInterval:()=>1,clearInterval(){}};const calls=[];let submitted=0;
 const load=loader({'./api':{rpc:async(_info,method,args)=>{calls.push({method,args});return method==='save_context_capacity'?{ok:true}:inspect();}}});const {ContextDebug}=load('context-debug');let v;
 await act(async()=>{v=create(React.createElement('form',{onSubmit:()=>submitted++},React.createElement(ContextDebug,{info:{port:1,token:'t'},conversation:'dm-tebb'})));});
 await act(async()=>v.root.findAllByType('details')[0].props.onToggle({currentTarget:{open:true}}));await act(async()=>button(v,'配置模型容量').props.onClick());
 assert.equal(v.root.findAllByType('form').length,1);
 await act(async()=>v.root.findByProps({'aria-label':'模型窗口总量'}).props.onChange({target:{value:'10000'}}));
 await act(async()=>v.root.findByProps({'aria-label':'输出预留tokens'}).props.onChange({target:{value:'1000'}}));
 await act(async()=>button(v,'保存容量配置').props.onClick());
 assert.equal(submitted,0);assert.deepEqual(calls.find(c=>c.method==='save_context_capacity').args,{model_id:'m1',window:10000,output_reserve:1000});await act(async()=>v.unmount());
});
