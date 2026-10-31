const {test,assert,React,create,act,loader,text,button}=require('./component-fixture.cjs');
const budget=()=>({root_budget_id:'one-goal',tier:'research',status:'active',policy_version:1,
 limits:{model_calls:16,tool_calls:24,input_tokens:192000,output_tokens:48000,time_ms:300000},
 consumed:{model_calls:4,tool_calls:2,input_tokens:1000,output_tokens:100,input_unknown_calls:1,output_unknown_calls:1,output_occupied:8292,time_ms:10000},
 remaining:{model_calls:12,tool_calls:22,input_tokens:null,output_tokens:39708,time_ms:290000},
 input_mode:'measured_or_unknown',output_mode:'measured_or_unknown',waiting_user:false,no_progress_calls:1,
 finish_reserve:{model_calls:2,output_tokens:4096},extensions:[],stop_reason:{dimension:'model_calls',message:'请求额度已到，保留收尾。'},cost:null});
test('root budget distinguishes reported usage, unknown calls and conservative reservations',async()=>{
 const {TaskBudgetView}=loader({})('task-budget');let view;
 await act(async()=>{view=create(React.createElement(TaskBudgetView,{budget:budget()}));});
 const rendered=text(view.toJSON());
 assert(rendered.includes('请求 4/16'));assert(rendered.includes('工具 2/24'));assert(rendered.includes('8,292'));
 assert(rendered.includes('费用未知'));assert(rendered.includes('缺用量的请求'));assert(rendered.includes('仅计量'));
 assert(rendered.includes('请求额度已到'));assert(!rendered.includes('0元'));
 await act(async()=>view.unmount());
});
test('uncertain output probe retries only its stable request id and never starts on opening',async()=>{
 global.window={setInterval:()=>1,clearInterval(){}};const calls=[];let first=true;
 const {ModelOutputLimit}=loader({'./api':{rpc:async(_info,method,args)=>{
   calls.push({method,args});if(method==='load_model_output_limit')return{verified:false};
   if(method==='verify_model_output_limit'){if(first){first=false;throw Error('ack lost');}return{task_id:'original-probe'};}
   return{task:{state:'failed',answer:'',error:'fixture stopped'}};
 }}})('model-output-limit');let view;
 await act(async()=>{view=create(React.createElement(ModelOutputLimit,{info:{port:1,token:'fixture'},modelId:'model'}));});
 assert.equal(calls.filter(c=>c.method==='verify_model_output_limit').length,0);
 await act(async()=>button(view,'验证输出上限（最多2次请求）').props.onClick());
 await act(async()=>button(view,'核对原验证').props.onClick());
 const sent=calls.filter(c=>c.method==='verify_model_output_limit');assert.equal(sent.length,2);
 assert(sent[0].args.request_id);assert.equal(sent[0].args.request_id,sent[1].args.request_id);
 await act(async()=>view.unmount());
});
