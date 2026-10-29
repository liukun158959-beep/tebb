const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const React=require('react'),{create,act}=require('react-test-renderer');
const external={};
async function registerMarkdown(){external['react-markdown']=await import('react-markdown');external['remark-gfm']=await import('remark-gfm');}
function loader(mocks){const cache={};return function load(name){
 if(cache[name])return cache[name].exports;
 const base=path.resolve(__dirname,'../src/client',name),file=fs.existsSync(base+'.tsx')?base+'.tsx':base+'.ts';
 const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache[name]=m;const original=m.require.bind(m);
 m.require=id=>mocks[id]||external[id]||(id.endsWith('.css')?{}:id.startsWith('./')?load(id.slice(2)):original(id));
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;
};}
const text=n=>typeof n==='string'?n:(n.children||[]).map(text).join('');
const button=(v,label)=>v.root.findAllByType('button').find(n=>text(n)===label);

module.exports={test,assert,React,create,act,loader,text,button,registerMarkdown};
