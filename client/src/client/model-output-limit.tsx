import {useEffect,useRef,useState} from 'react';
import {rpc,type BackendInfo} from './api';

type Binding={verified:boolean;parameter:string|null;verified_at:number|null};
type ProbeTask={state:string;answer:string;error:string};
export function ModelOutputLimit({info,modelId}:{info:BackendInfo|null;modelId:string}){
  const [binding,setBinding]=useState<Binding|null>(null),[parameter,setParameter]=useState('max_completion_tokens');
  const [taskId,setTaskId]=useState(''),[task,setTask]=useState<ProbeTask|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const request=useRef(''),generation=useRef(0);
  useEffect(()=>{
    generation.current++;request.current='';setBinding(null);setTaskId('');setTask(null);setError('');
    if(!info||!modelId)return;let live=true;
    rpc<Binding>(info,'load_model_output_limit',{model_id:modelId}).then(value=>{if(live)setBinding(value);}).catch(e=>{if(live)setError(String(e));});
    return()=>{live=false;};
  },[info,modelId]);
  const active=!!taskId&&(!task||['queued','running','cancelling'].includes(task.state));
  useEffect(()=>{
    if(!info||!active)return;let live=true,loading=false;
    async function read(){if(loading)return;loading=true;try{
      const value=await rpc<{task:ProbeTask}>(info!,'get_agent_task',{task_id:taskId,focus:false});
      if(!['queued','running','cancelling'].includes(value.task.state)){const updated=await rpc<Binding>(info!,'load_model_output_limit',{model_id:modelId});if(live){setBinding(updated);setTask(value.task);}}else if(live)setTask(value.task);
    }catch(e){if(live)setError(String(e)+'；保留原任务编号，请在任务监控核对，未自动重发。');}finally{loading=false;}}
    void read();const timer=window.setInterval(()=>void read(),2000);return()=>{live=false;window.clearInterval(timer);};
  },[info,active,taskId,modelId]);
  async function start(){if(!info||!modelId||busy||active)return;setBusy(true);setError('');const version=generation.current;
    if(!request.current||task)request.current=crypto.randomUUID();
    try{
    const result=await rpc<{task_id:string}>(info,'verify_model_output_limit',{model_id:modelId,parameter,request_id:request.current});if(version===generation.current){setTask(null);setTaskId(result.task_id);}
  }catch(e){setError(String(e)+'；提交回执未确认，请在任务监控核对，不自动重发。');}finally{setBusy(false);}}
  return <section aria-label="生成输出上限" className="mt-3 space-y-2 border-t border-current/15 pt-3 text-xs">
    <p>生成输出上限 · {binding?.verified?`已验证 ${binding.parameter}`:'此模型/路由尚未验证'}</p>
    <p>每次验证最多两次短请求，可能计费；仅保存该模型和路由的参数，不声明窗口或费用。服务更换后重新验证。</p>
    <select aria-label="生成上限参数" value={parameter} disabled={busy||active||!!request.current&&!task} onChange={e=>setParameter(e.target.value)}><option>max_completion_tokens</option><option>max_tokens</option></select>
    <button type="button" disabled={!info||!modelId||busy||active} onClick={()=>void start()}>{active?'正在验证…':request.current&&!task?'核对原验证':'验证输出上限（最多2次请求）'}</button>
    {active&&<button type="button" onClick={()=>void rpc(info!,'cancel_agent_task',{task_id:taskId}).catch(e=>setError(String(e)))}>停止验证</button>}
    {task&&<p role="status">{task.answer||task.error||task.state}</p>}{error&&<p role="alert">{error}</p>}
  </section>;
}
