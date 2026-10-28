// Keep background progress separate from chat-window lifetime and gesture lines.
export type FeedbackRun = { id:string; state:string; parent?:string; created?:number; kind?:string; title?:string; status?:string; answer?:string; error?:string; events?:string[];chain?:{state:string;error:string;answer:string} };
export type FeedbackProgress = { enabled:boolean; task?:FeedbackRun & {text?:string} | null };
export type Feedback = { key:string; text:string; error:boolean; working:boolean };
const active = (run:FeedbackRun) => ['pending','queued','running','waiting','cancelling'].includes(run.state);
const labels:Record<string,string> = {pending:'已收到',queued:'排队中',running:'处理中',waiting:'等待前序任务',cancelling:'正在停止',succeeded:'已完成',failed:'失败',timed_out:'超时',cancelled:'已停止',interrupted:'已中断',needs_input:'待补充 · 尚未开始执行'};
const brief = (value:string, limit=150) => value.length>limit ? value.slice(0,limit)+'…' : value;

export class PetFeedback {
  private seen = new Set<string>();
  private eligible = new Set<string>();
  private dismissed = new Set<string>();
  private started = false;
  current:Feedback | null = null;
  dismiss() { if(this.current)this.dismissed.add(this.current.key);this.current=null; }
  update(progress:FeedbackProgress, runs:FeedbackRun[], chatVisible:boolean):Feedback | null {
    const root = (run:FeedbackRun) => {
      const visited=new Set<string>();let current=run;
      while(current.parent && !visited.has(current.id)) {visited.add(current.id);const parent=runs.find(r=>r.id===current.parent);if(!parent)break;current=parent;}
      return current;
    };
    const roots=[...new Map(runs.map(run=>{const ancestor=root(run);return [ancestor.id,ancestor];})).values()];
    const candidates:{run:FeedbackRun;key:string;order:number;step:number}[]=roots.map(ancestor=>{
      const chain=runs.filter(run=>root(run).id===ancestor.id);
      const failed=chain.find(run=>['failed','timed_out','interrupted'].includes(run.state));
      const waiting=chain.find(run=>run.state==='needs_input');
      const state=ancestor.chain?.state || (waiting?'needs_input':chain.some(active)?'running':failed?.state || (chain.some(run=>run.state==='cancelled')?'cancelled':ancestor.state));
      const run={...ancestor,state,error:ancestor.chain?.error||failed?.error||ancestor.error,answer:ancestor.chain?.answer||ancestor.answer};
      return {run,key:'collab:'+ancestor.id,order:ancestor.created||runs.indexOf(ancestor),step:0};
    });
    if(progress.task)candidates.push({run:{...progress.task,title:progress.task.text},key:'task:'+progress.task.id,order:progress.task.created || 0,step:0});
    for(const {run,key} of candidates){
      if((this.started && !this.seen.has(key)) || active(run))this.eligible.add(key);
    }
    for(const {key} of candidates)this.seen.add(key);
    this.started=true;
    const available=candidates.filter(({key})=>this.eligible.has(key)&&!this.dismissed.has(key));
    available.sort((a,b)=>b.order-a.order || Number(active(b.run))-Number(active(a.run)) ||
      Number(a.run.state==='waiting')-Number(b.run.state==='waiting') || b.step-a.step);
    const chosen=available[0];
    if(!progress.enabled || chatVisible || !chosen){this.current=null;return null;}
    const {run,key}=chosen, working=active(run), error=['failed','timed_out','interrupted'].includes(run.state);
    // Routing/plan JSON and private reasoning are never a user-facing answer.
    const answer=!['plan','compress'].includes(run.kind || '') && (run.kind!=='route' || run.state==='succeeded') ? run.answer : '';
    const detail=error ? run.error || run.events?.at(-1) : (answer || (working ? run.events?.at(-1) || run.status : run.error));
    this.current={key,working,error,text:brief(`${labels[run.state] || run.state}${run.title?' · '+brief(run.title,35):''}\n${detail || (working?'正在准备…':'打开对话框查看详情。')}`)};
    return this.current;
  }
}
