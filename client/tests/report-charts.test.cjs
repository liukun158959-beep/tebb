const {test,assert,React,create,act,loader,text,button,registerMarkdown}=require('./component-fixture.cjs');
test.before(registerMarkdown);
test('saved chart block renders real asset and exposes numeric source table',async()=>{
 const id='a'.repeat(32),calls=[];const chart={ok:true,image:'data:image/png;base64,iVBORw0KGgo=',spec:{title:'测量比较',type:'bar',unit:'ms',comparability:'同一测试口径',labels:['A','B'],series:[{name:'耗时',values:[3,null]}]},sources:[{id:'S1',title:'原始数据',url:'https://example.org/',truncated:true}]};
 const load=loader({'./api':{backendInfo:async()=>({port:1,token:'t'}),rpc:async(_info,method,args)=>{calls.push({method,args});return chart;}}});const {Markdown}=load('Markdown');let v;
 await act(async()=>{v=create(React.createElement(Markdown,{text:'技术报告\n\n```tebb-chart\n'+id+'\n```'}));});
 assert.equal(calls[0].method,'get_report_chart');assert.equal(v.root.findByType('img').props.alt,'测量比较');assert(text(v.toJSON()).includes('数据与来源'));assert(text(v.toJSON()).includes('缺失'));assert(text(v.toJSON()).includes('正文节选'));
 await act(async()=>v.unmount());
});

test('invalid chart id cannot read arbitrary paths or become executable markup',async()=>{
 let calls=0;const load=loader({'./api':{backendInfo:async()=>({}),rpc:async()=>{calls++;throw Error('must not run');}}});const {Markdown}=load('Markdown');let v;
 await act(async()=>{v=create(React.createElement(Markdown,{text:'```tebb-chart\n../../secret\n```\n\n<script>alert(1)</script>'}));});
 assert.equal(calls,0);assert(text(v.toJSON()).includes('图表标识无效'));assert.equal(v.root.findAllByType('script').length,0);await act(async()=>v.unmount());
});
