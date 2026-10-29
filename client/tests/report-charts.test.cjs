const {test,assert,React,create,act,loader,text,button,registerMarkdown}=require('./component-fixture.cjs');
test.before(registerMarkdown);
test('saved chart block renders real asset and exposes numeric source table',async()=>{
 const id='a'.repeat(32),calls=[];const chart={ok:true,image:'data:image/png;base64,iVBORw0KGgo=',spec:{title:'测量比较',type:'bar',unit:'ms',comparability:'同一测试口径',labels:['A','B'],series:[{name:'耗时',values:[3,null]}]},sources:[{id:'S1',title:'原始数据',url:'https://example.org/',truncated:true}]};
 const load=loader({'./api':{backendInfo:async()=>({port:1,token:'t'}),rpc:async(_info,method,args)=>{calls.push({method,args});return chart;}}});const {Markdown}=load('Markdown');let v;
 await act(async()=>{v=create(React.createElement(Markdown,{text:'技术报告\n\n```tebb-chart\n'+id+'\n```'}));});
 assert.equal(calls[0].method,'get_report_chart');assert.equal(v.root.findByType('img').props.alt,'测量比较');assert(text(v.toJSON()).includes('数据与来源'));await act(async()=>button(v,'数据与来源').props.onClick());assert(text(v.toJSON()).includes('缺失'));assert(text(v.toJSON()).includes('正文节选'));
 await act(async()=>v.unmount());
});

test('invalid chart id cannot read arbitrary paths or become executable markup',async()=>{
 let calls=0;const load=loader({'./api':{backendInfo:async()=>({}),rpc:async()=>{calls++;throw Error('must not run');}}});const {Markdown}=load('Markdown');let v;
 await act(async()=>{v=create(React.createElement(Markdown,{text:'```tebb-chart\n../../secret\n```\n\n<script>alert(1)</script>'}));});
 assert.equal(calls,0);assert(text(v.toJSON()).includes('图表标识无效'));assert.equal(v.root.findAllByType('script').length,0);await act(async()=>v.unmount());
});

test('flow snippet sources stay visibly unverified and never appear as body excerpts',async()=>{
 const load=loader({'./api':{backendInfo:async()=>({}),rpc:async()=>({ok:true,image:'data:image/png;base64,iVBORw0KGgo=',spec:{title:'关系示意',type:'flow',nodes:['输入','输出'],edges:[[0,1]]},sources:[{id:'S1',title:'搜索结果',url:'https://example.org/',level:'snippet',truncated:true}]})}});const {ReportChart}=load('report-charts');let v;
 await act(async()=>{v=create(React.createElement(ReportChart,{id:'e'.repeat(32)}));});
 assert(text(v.toJSON()).includes('结构示意未按原文核实'));await act(async()=>button(v,'结构与依据').props.onClick());
 assert(text(v.toJSON()).includes('搜索摘要，未读取正文'));assert(!text(v.toJSON()).includes('正文节选'));await act(async()=>v.unmount());
});

test('streamed text keeps the chart, image and expanded sources mounted and reads once',async()=>{
 const id='b'.repeat(32);let calls=0;const info={port:1,token:'t'};
 const load=loader({'./api':{rpc:async()=>{calls++;return {ok:true,image:'data:image/png;base64,iVBORw0KGgo=',render_version:2,spec:{type:'flow',title:'稳定流程',nodes:['读取','核对'],edges:[[0,1]]},sources:[]};}}});
 const {StreamText}=load('stream-text'),{ReportAssetProvider}=load('report-charts');let v,body='```tebb-chart\n'+id+'\n```';const view=active=>React.createElement(ReportAssetProvider,{info:{...info}},React.createElement(StreamText,{text:body,active}));
 await act(async()=>{v=create(view(true));});const image=v.root.findByType('img');await act(async()=>button(v,'结构与依据').props.onClick());
 for(let i=0;i<6;i++){body+='\n\n继续补充第'+i+'段。';await act(async()=>v.update(view(true)));assert.equal(v.root.findByType('img'),image);assert(text(v.toJSON()).includes('读取 → 核对'));}
 await act(async()=>v.update(view(false)));assert.equal(calls,1);assert.equal(v.root.findByType('img'),image);assert(text(v.toJSON()).includes('读取 → 核对'));await act(async()=>v.unmount());
});

test('partial chart id and open fence wait without RPC or invalid-id flashes',async()=>{
 const id='c'.repeat(32);let calls=0;
 const load=loader({'./api':{backendInfo:async()=>({port:1,token:'t'}),rpc:async()=>{calls++;return {ok:true,image:'data:image/png;base64,iVBORw0KGgo=',spec:{type:'flow',title:'图',nodes:['A'],edges:[]},sources:[]};}}});const {StreamText}=load('stream-text');let v;
 for(const body of ['```tebb-chart\n','```tebb-chart\n'+id.slice(0,8),'```tebb-chart\n'+id,'```tebb-chart\n'+id+'\n']){
  await act(async()=>{const n=React.createElement(StreamText,{text:body,active:true});if(v)v.update(n);else v=create(n);});assert.equal(calls,0);assert.equal(v.root.findAllByProps({role:'alert'}).length,0);assert(text(v.toJSON()).includes('图表内容正在生成'));
 }
 await act(async()=>v.update(React.createElement(StreamText,{text:'```tebb-chart\n'+id+'\n```',active:true})));assert.equal(calls,1);assert.equal(v.root.findAllByType('img').length,1);await act(async()=>v.unmount());
});

test('closed chart fences in quoted and indented containers load during streaming',async()=>{
 const id='d'.repeat(32);let calls=0;const load=loader({'./api':{backendInfo:async()=>({port:1,token:'t'}),rpc:async()=>{calls++;return {ok:true,image:'data:image/png;base64,iVBORw0KGgo=',spec:{type:'flow',title:'图',nodes:['A'],edges:[]},sources:[]};}}});const {StreamText}=load('stream-text');
 for(const body of ['> ```tebb-chart\n> '+id+'\n> ```','> > ~~~~tebb-chart\n> > '+id+'\n> > ~~~~','- 图表\n\n  ```tebb-chart\n  '+id+'\n  ```']){let v;const previous=calls;await act(async()=>{v=create(React.createElement(StreamText,{text:body,active:true}));});assert.equal(calls,previous+1);assert.equal(v.root.findAllByType('img').length,1);await act(async()=>v.unmount());}
});
