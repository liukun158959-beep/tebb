const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
module.exports=function load(name,mocks={}){
  const filename=path.resolve(__dirname,'../src/client',name);
  const code=ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2022}}).outputText;
  const mod=new Module(filename,module);mod.filename=filename;mod.paths=Module._nodeModulePaths(path.dirname(filename));
  const original=mod.require.bind(mod);mod.require=id=>Object.hasOwn(mocks,id)?mocks[id]:original(id);mod._compile(code,filename);return mod.exports;
};
