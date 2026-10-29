import { useId, useState } from 'react';
import type { DemoRun } from './chat-prototype-state';
import type { ClarificationAction,ChoiceAnswer } from './clarification-state';
import './clarification.css';

export function ClarificationCard({run,onAction,onText,onOpen,busy=false}:{run:DemoRun;onAction:(action:ClarificationAction)=>Promise<boolean>;onText?:()=>void;onOpen?:()=>void;busy?:boolean}){
  const group=useId();
  const [selected,setSelected]=useState<Record<string,string>>({}),[custom,setCustom]=useState<Record<string,string>>({});
  const [posting,setPosting]=useState(false),[error,setError]=useState('');
  if(run.state!=='needs_input')return null;
  const form=run.clarification_form,version=run.clarification_version||form?.version||'';
  const generating=['pending','queued','running'].includes(run.options_generation?.state||'');
  const disabled=posting||busy||generating;
  const complete=!!form&&form.questions.every(q=>selected[q.id]&&(selected[q.id]!=='custom'||custom[q.id]?.trim()));
  async function submit(generate=false){
    if(disabled||!version||!generate&&!complete)return;setPosting(true);setError('');
    const answers:Record<string,ChoiceAnswer>={};
    for(const q of form?.questions||[])answers[q.id]=selected[q.id]==='custom'?{custom:custom[q.id].trim()}:{option_id:selected[q.id]};
    try{
      const ok=await onAction(generate?{type:'clarification_options',id:run.id,conversation:run.conversation,version}:{type:'clarification_answer',id:run.id,conversation:run.conversation,version,answers});
      if(!ok)setError('尚未确认提交，请核对任务状态；保留当前选择，相同内容可重试。');
    }catch(e){setError(String(e));}finally{setPosting(false);}
  }
  async function cancel(){
    if(posting||busy)return;setPosting(true);setError('');
    try{if(!await onAction({type:'cancel',id:run.id,conversation:run.conversation}))setError('取消结果尚未确认，请重新读取。');}
    catch(e){setError(String(e));}finally{setPosting(false);}
  }
  return <section className="clarification-card" aria-label="补充信息选项"><header><strong>需要你补充</strong><span>任务已暂停</span></header><p>{run.clarification||run.answer}</p>
    {form?<><div>{form.questions.map(q=><fieldset key={q.id} disabled={disabled}><legend>{q.question}</legend><div className="clarification-options">{q.options.map(option=><label key={option.id} className={selected[q.id]===option.id?'is-selected':''}><input type="radio" name={`${group}-${q.id}`} value={option.id} checked={selected[q.id]===option.id} onChange={()=>setSelected(old=>({...old,[q.id]:option.id}))}/><span>{option.label}</span></label>)}<label className={selected[q.id]==='custom'?'is-selected':''}><input type="radio" name={`${group}-${q.id}`} checked={selected[q.id]==='custom'} onChange={()=>setSelected(old=>({...old,[q.id]:'custom'}))}/><span>自定义</span></label></div>{selected[q.id]==='custom'&&<input className="clarification-custom" aria-label={`${q.question}的自定义答案`} maxLength={500} value={custom[q.id]||''} placeholder="填写你的选择…" onChange={e=>setCustom(old=>({...old,[q.id]:e.target.value}))}/>}</fieldset>)}</div><button type="button" className="cp-button cp-primary" disabled={disabled||!complete} onClick={()=>void submit()}>继续执行</button></>:<button type="button" className="cp-button" disabled={disabled||!version} onClick={()=>void submit(true)}>{generating?'正在生成选项…':'生成可选答案'}</button>}
    <footer>{onText&&<button type="button" className="cp-text-button" disabled={disabled} onClick={onText}>改用文字补充</button>}{onOpen&&<button type="button" className="cp-text-button" onClick={onOpen}>查看原对话</button>}<button type="button" className="cp-text-button" disabled={posting||busy} onClick={()=>void cancel()}>取消原任务</button></footer>
    {(error||run.options_generation?.error)&&<p role="alert">{error||run.options_generation?.error}</p>}<small>选择后点击继续才执行；只生成选项不会恢复任务。</small>
  </section>;
}
