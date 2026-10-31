export type TaskBudget = {
  root_budget_id:string;tier:string;status:string;policy_version:number;
  limits:Record<string,number>;
  consumed:Record<string,number>;
  remaining:Record<string,number|null>;
  input_mode:string;output_mode:string;waiting_user:boolean;no_progress_calls:number;
  prior_usage_unknown?:boolean;
  finish_reserve:{model_calls:number;output_tokens:number};
  extensions:{trigger:string;progress:{kind:string;reference:string}[]}[];
  stop_reason:null|{dimension:string;message:string};cost:null|number;
};
const tiers:Record<string,string>={simple:'简单',research:'研究',coding:'编码',chart:'图表',clarify:'澄清',probe:'验证'};
const count=(value:number|null|undefined)=>value==null?'未知':value.toLocaleString();

export function TaskBudgetView({budget}:{budget?:TaskBudget|null}){
  if(!budget)return null;
  const used=budget.consumed;
  return <details className="mt-3 rounded-lg border border-current/15 p-3 text-xs" aria-label="整个目标的任务预算">
    <summary>任务预算 · {tiers[budget.tier]||budget.tier} · 请求 {used.model_calls}/{budget.limits.model_calls} · 工具 {used.tool_calls}/{budget.limits.tool_calls}{budget.waiting_user?' · 等待补充，执行计时暂停':budget.status==='active'?' · 执行中':' · 已结束'}</summary>
    <dl className="mt-3 grid grid-cols-2 gap-2">
      <div><dt>已回报输入 / 输出</dt><dd>{count(used.input_tokens)} / {count(used.output_tokens)} tokens</dd></div>
      <div><dt>缺用量的请求（输入 / 输出）</dt><dd>{used.input_unknown_calls} / {used.output_unknown_calls}</dd></div>
      <div><dt>输入 / 输出预算</dt><dd>{count(budget.limits.input_tokens)} / {count(budget.limits.output_tokens)}</dd></div>
      <div><dt>保守占用输出 / 剩余</dt><dd>{count(used.output_occupied)} / {count(budget.remaining.output_tokens)}</dd></div>
      <div><dt>模型请求 / 工具剩余</dt><dd>{count(budget.remaining.model_calls)} / {count(budget.remaining.tool_calls)}</dd></div>
      <div><dt>执行时间 / 上限</dt><dd>{Math.round(used.time_ms/1000)} / {Math.round(budget.limits.time_ms/1000)} 秒</dd></div>
    </dl>
    <p className="mt-2">输入{budget.input_mode==='exact'?'按绑定分词准入':'仅回报统计，未声明精确硬上限'}；输出{budget.output_mode==='requested_hard'?'已向验证过的服务参数请求上限':'仅计量，服务硬上限未验证'}。费用{budget.cost==null?'未知':budget.cost}。</p>
    {budget.prior_usage_unknown&&<p>旧阶段没有预算记录；以上仅为启用后的计量，历史累计未知。</p>}
    <p className="mt-2">收尾保留 {budget.finish_reserve.model_calls} 次请求 / {budget.finish_reserve.output_tokens} 输出 tokens；未结算请求继续占用，不按零计。分工、审阅和继续共用此预算。</p>
    {!!budget.extensions.length&&<p className="mt-2">已受控扩展一次 · 依据：{budget.extensions.flatMap(e=>e.progress.map(p=>p.kind+' · '+p.reference)).join('；')}</p>}
    {budget.stop_reason&&<p role="status" className="mt-2">{budget.stop_reason.message}</p>}
  </details>;
}
