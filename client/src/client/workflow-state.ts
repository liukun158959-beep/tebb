export type NodeKind = 'trigger'|'search'|'read'|'ai'|'condition'|'document'|'base'|'message';
export type NodeConfig = Record<string,string|number|Record<string,string>>;
export type GraphNode = {id:string;kind:NodeKind;config:NodeConfig;position:{x:number;y:number}};
export type Graph = {nodes:GraphNode[];edges:{source:string;target:string;branch:string}[]};
export type Definition = {id:string;name:string;revision:number;version:string;graph:Graph};
export type Receipt = {node:string;kind:NodeKind;state:string;input:NodeConfig;output:Record<string,unknown>;error:string;receipt_key:string};
export type WorkflowRun = {id:string;workflow:string;version:string;name:string;state:string;mode:'step'|'full';attempt:number;task_id:string;error:string;approved:string[];graph:Graph;receipts:Record<string,Receipt>};
export type CatalogNode = {kind:NodeKind;label:string;defaults:NodeConfig;outputs:string[];writes:boolean};
export const writeKinds = new Set<NodeKind>(['document','base','message']);
export const runLabels:Record<string,string> = {queued:'排队中',running:'执行中',paused:'步骤完成',waiting_approval:'待确认发布',completed:'已完成',failed:'失败',unknown:'结果待核对',interrupted:'已中断',cancelled:'已停止',confirmed:'已确认',pending:'执行中',skipped:'分支跳过'};
export const initialGraph = ():Graph => ({nodes:[{id:'trigger',kind:'trigger',config:{text:''},position:{x:70,y:70}}],edges:[]});
export const newNodeId = (kind:NodeKind) => kind+'_'+crypto.randomUUID().replaceAll('-','').slice(0,8);
export function describeTarget(node:GraphNode){
  if(node.kind==='document')return String(node.config.parent_token||'当前用户云空间');
  if(node.kind==='base')return String(node.config.base_token)+' / '+String(node.config.table_id);
  if(node.kind==='message')return String(node.config.chat_id);
  return '';
}
export function resolvePreview(config:NodeConfig,receipts:Record<string,Receipt>):NodeConfig{
  return Object.fromEntries(Object.entries(config).map(([key,value])=>[key,typeof value==='string'?value.replace(/\{\{\s*([A-Za-z][A-Za-z0-9_-]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g,(_all,node,field)=>{
    const output=receipts[node]?.output[field];return output===undefined?'（上游尚未取得）':typeof output==='string'?output:JSON.stringify(output);
  }):typeof value==='object'?resolvePreview(value,receipts) as Record<string,string>:value]));
}
export const editableRun = (run?:WorkflowRun|null) => !!run&&['paused','failed','interrupted','cancelled','waiting_approval'].includes(run.state);
