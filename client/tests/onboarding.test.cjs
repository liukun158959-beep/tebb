const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const React=require('react'),{create,act}=require('react-test-renderer');
function load(name,mocks={}){
  const file=path.resolve(__dirname,'../src/client',name),mod=new Module(file,module);
  mod.filename=file;mod.paths=Module._nodeModulePaths(path.dirname(file));const original=mod.require.bind(mod);
  mod.require=id=>Object.hasOwn(mocks,id)?mocks[id]:id.endsWith('.css')?{}:original(id);
  mod._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText,file);return mod.exports;
}
const state=load('onboarding-state.ts');
const empty={hasBase:false,hasModel:false,hasKey:false,saved:false,tested:false,busy:false,error:''};
const text=node=>typeof node==='string'?node:(node.children||[]).map(text).join('');
const button=(view,label)=>view.root.findAllByType('button').find(n=>text(n)===label);
function mount(rpc=async()=>({ok:true}),override={}){
  global.window={innerWidth:1024,innerHeight:768,addEventListener(){},removeEventListener(){}};
  global.ResizeObserver=class{observe(){}disconnect(){}};
  const props={info:{port:1,token:'isolated'},status:{ok:true,show:true,configured:false,checks:{}},modelState:{...empty},configured:false,feishuConnected:false,modelBusy:false,onClose(value){props.closed=value===true?'completed':'paused';},onNavigate(value){props.destination=value;},...override};
  const guide=load('onboarding.tsx',{'./api':{rpc},'./onboarding-state':state,'./onboarding-anchor':{useGuideAnchor:()=>null,guideAnchor:()=>null}}).SetupGuide;
  let view;act(()=>{view=create(React.createElement(guide,props));});
  return {view,props,update(values){Object.assign(props,values);act(()=>view.update(React.createElement(guide,props)));},close(){act(()=>view.unmount());}};
}
test('missing configuration targets the first actual field; saved and tested stay distinct',()=>{
  let value={...empty};assert.equal(state.modelGuideTarget(value).anchor,'model-base');
  value.hasBase=true;assert.equal(state.modelGuideTarget(value).anchor,'model-name');
  value.hasModel=true;assert.equal(state.modelGuideTarget(value).anchor,'model-key');
  value.hasKey=true;value.tested=true;assert.equal(state.modelGuideTarget(value).anchor,'model-save');
  value.saved=true;value.tested=false;assert.match(state.modelGuideTarget(value).title,/没有验证/);
  value.tested=true;assert.match(state.modelGuideTarget(value).title,/已验证/);
  assert.equal(state.initialGuidePhase(false,'terminal'),'model');
  assert.equal(state.initialGuidePhase(true,'terminal'),'terminal');
});
test('guide navigates to the real page and never owns configuration inputs or RPC writes',async()=>{
  const calls=[],app=mount(async(_i,method,args)=>{calls.push({method,args});return {ok:true};});
  assert.equal(app.props.destination,'settings');assert.equal(app.view.root.findAllByType('input').length,0);
  assert.equal(button(app.view,'继续，稍后测试').props.disabled,true);
  app.update({modelState:{...empty,hasBase:true,hasModel:true,hasKey:true,saved:true},configured:true});
  await act(async()=>button(app.view,'继续，稍后测试').props.onClick());
  assert.equal(app.props.destination,'feishu');assert.deepEqual(calls,[{method:'save_onboarding_progress',args:{step:'feishu'}}]);
  await act(async()=>button(app.view,'跳过飞书').props.onClick());assert.equal(app.props.destination,'terminal');
  await act(async()=>button(app.view,'完成引导').props.onClick());
  assert.equal(app.props.closed,'completed');assert.equal(app.props.destination,'collaboration');
  assert.equal(calls.at(-1).method,'complete_onboarding');app.close();
});
test('pause persists only a stage and resumes; failed persistence keeps the guide visible',async()=>{
  let fail=true;const calls=[],app=mount(async(_i,method,args)=>{calls.push({method,args});return fail?{ok:false,error:'disk unavailable'}:{ok:true};},{configured:true,status:{step:'terminal'}});
  assert.equal(app.props.destination,'terminal');
  await act(async()=>button(app.view,'稍后继续').props.onClick());
  assert.equal(app.props.closed,undefined);assert.match(text(app.view.root),/disk unavailable/);
  fail=false;await act(async()=>button(app.view,'稍后继续').props.onClick());
  assert.equal(app.props.closed,'paused');assert.deepEqual(calls.at(-1),{method:'save_onboarding_progress',args:{step:'terminal'}});app.close();
});
test('saved model can continue untested, but pending save/test blocks dismissal and completion',()=>{
  const app=mount(undefined,{modelState:{...empty,saved:true},modelBusy:true});
  assert.equal(button(app.view,'继续，稍后测试').props.disabled,true);
  assert.equal(button(app.view,'稍后继续').props.disabled,true);
  app.update({modelBusy:false,modelState:{...empty,saved:true,tested:true,error:'401 check key'}});
  assert.equal(button(app.view,'继续').props.disabled,false);assert.match(text(app.view.root),/401 check key/);app.close();
});
test('narrow, scrolled and resized viewport placement stays inside the window',()=>{
  for(const [width,height,rect] of [[320,600,{left:30,top:500,width:250,height:36}],[1024,768,{left:20,top:20,width:600,height:34}],[320,240,null]]){
    const box=state.guidePlacement(rect,width,height,180);
    assert(box.left>=12);assert(box.left+box.width<=width-12);assert(box.top>=12);assert(box.top+180<=height-12);
  }
});
test('actual settings produce a secret-free guide snapshot and invalidate successful tests on edit',async()=>{
  const {SettingsPane}=load('settings.tsx');let view,snapshot,releaseTest;
  const props={model:{ok:true,base_url:'https://example.com/v1',model:'test',has_key:true},models:[],editingId:'m1',modelError:'',notice:'',persona:'',personaError:'',personaNotice:'',busy:false,keyEpoch:0,dark:true,photo:false,bgError:'',onToggleTheme(){},onPickFile(){},onClearBackground(){},onSave(){},onTest:()=>new Promise(resolve=>releaseTest=resolve),onEdit(){},onDelete(){},onSavePersona(){},onOpenGuide(){},onSetupChange:value=>snapshot=value};
  act(()=>{view=create(React.createElement(SettingsPane,props));});assert.equal(snapshot.saved,true);assert.equal(snapshot.tested,false);
  let pending;act(()=>{pending=view.root.findByProps({'data-model-test':true}).props.onClick();});
  await act(async()=>{releaseTest(true);await pending;});assert.equal(snapshot.tested,true);
  act(()=>view.root.findByProps({'data-model-key':true}).props.onChange({target:{value:'secret-never-in-guide'}}));
  assert.equal(snapshot.saved,false);assert.equal(snapshot.tested,false);assert(!JSON.stringify(snapshot).includes('secret-never'));
  act(()=>{view.root.findByProps({'data-model-test':true}).props.onClick();});
  act(()=>view.root.findByProps({'data-model-name':true}).props.onChange({target:{value:'changed'}}));
  await act(async()=>{releaseTest(true);});assert.equal(snapshot.tested,false);assert.equal(snapshot.saved,false);
  act(()=>view.unmount());
});
