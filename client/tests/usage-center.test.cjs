const {test}=require('node:test'),assert=require('node:assert/strict');
const React=require('react'),{act,create}=require('react-test-renderer');const load=require('./load-client.cjs');
const fixture=load('usage-preview.ts').default;
const copy=()=>structuredClone(fixture);
function pane(rpc){return load('usage-center.tsx',{'./api':{rpc},'./page-stack':{usePageActive:()=>true},'./usage-preview':{default:fixture},'./usage-center.css':{}}).PersonalUsagePane;}
function select(view,label){const id=view.root.findAllByType('label').find(node=>node.children.join('')===label).props.htmlFor;return view.root.findAllByType('select').find(node=>node.props.id===id);}
const button=(view,label)=>view.root.findAllByType('button').find(node=>node.children.join('')===label);
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};

test('usage view keeps unknown costs distinct from zero and exposes trace identifiers',async()=>{
 const requests=[];let free=false;const Pane=pane(async(info,method,args)=>{requests.push([method,args]);const value=copy();if(free){value.totals.cost_cny_known=0;value.totals.cost_unknown_calls=0;}return value;});let view;
 await act(async()=>{view=create(React.createElement(Pane,{info:{port:1234,token:'fixture'}}));});
 assert.equal(requests[0][0],'load_personal_usage');const rendered=JSON.stringify(view.toJSON());
 assert(rendered.includes('次缺用量或价格'));assert(rendered.includes('未知'));assert(rendered.includes('sample-call-1'));
 assert(rendered.includes('不与上方相加'));assert(rendered.includes('缓存优惠未计入'));
 free=true;await act(async()=>button(view,'刷新统计').props.onClick());
 const cost=view.root.findAllByType('article').find(node=>node.findByType('h2').children.join('')==='已知费用估算');
 assert.equal(cost.findByType('strong').children.join(''),'约 ¥0');act(()=>view.unmount());
});

test('main task association uses its stable root identity without a physical task field',async()=>{
 const Pane=pane(async()=>{const value=copy();value.items=[{id:'task:root-125',kind:'task',started:1,state:'succeeded',completion:'incomplete'}];return value;});let view;
 await act(async()=>{view=create(React.createElement(Pane,{info:{port:1234,token:'fixture'}}));});
 const reference=view.root.findByType('code');assert(JSON.stringify(view.toJSON()).includes('验收未完成'));assert.equal(reference.props.title,'task:root-125');assert.equal(reference.children.join(''),'task:root-125');act(()=>view.unmount());
});

test('filter change ignores an older response and does not reset the selected value',async()=>{
 const old=deferred();let calls=0;const Pane=pane(async(info,method,args)=>{calls++;if(calls===2)return old.promise;
  const value=copy();value.filters={...value.filters,...args.filters};if(calls===3)value.totals.tasks=42;return value;});let view;
 await act(async()=>{view=create(React.createElement(Pane,{info:{port:1234,token:'fixture'}}));});
 act(()=>button(view,'刷新统计').props.onClick());
 await act(async()=>{select(view,'项目').props.onChange({target:{value:'value:sample-project'}});});
 await act(async()=>{old.resolve(copy());await old.promise;});
 assert.equal(select(view,'项目').props.value,'value:sample-project');
 assert(view.root.findAllByType('strong').some(node=>node.children.join('')==='42'));
 assert.equal(calls,3);act(()=>view.unmount());
});

test('hidden usage view does not query and a read failure retains the prior data and filters',async()=>{
 let calls=0;const Pane=pane(async()=>{calls++;if(calls>1)throw Error('fixture read failed');return copy();});let view;
 const props={info:{port:1234,token:'fixture'}};
 await act(async()=>{view=create(React.createElement(Pane,{...props,visible:false}));});assert.equal(calls,0);
 await act(async()=>view.update(React.createElement(Pane,{...props,visible:true})));assert.equal(calls,1);
 await act(async()=>select(view,'项目').props.onChange({target:{value:'value:sample-project'}}));
 const rendered=JSON.stringify(view.toJSON());assert(rendered.includes('fixture read failed'));assert(rendered.includes('sample-call-1'));
 assert.equal(select(view,'项目').props.value,'value:sample-project');assert.equal(calls,2);act(()=>view.unmount());
});

test('loading more appends stable records while keeping the same aggregate totals',async()=>{
 const requests=[];const Pane=pane(async(info,method,args)=>{requests.push(args);const value=copy();value.filters={...value.filters,...args.filters};
  value.items=[value.items[args.cursor?1:0]];value.next_cursor=args.cursor?'':'older';return value;});let view;
 await act(async()=>{view=create(React.createElement(Pane,{info:{port:1234,token:'fixture'}}));});
 await act(async()=>button(view,'加载更早记录').props.onClick());
 assert.equal(requests[1].cursor,'older');const rendered=JSON.stringify(view.toJSON());
 assert(rendered.includes('sample-call-1'));assert(rendered.includes('sample-call-2'));assert.equal(button(view,'加载更早记录'),undefined);
 act(()=>view.unmount());
});
