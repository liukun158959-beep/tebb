import { useEffect, useRef, useState } from 'react';
import { rpc,type BackendInfo } from './api';
import { usePageActive } from './page-stack';
import { UiButton,UiFeedback,UiField } from './ui-controls';
import preview from './usage-preview';
import './usage-center.css';

type Filters={period:string;model:string;project:string;channel:string;kind:string};
type Counts={model_calls:number;diagnostic_model_calls?:number;tool_calls:number;network_attempts:number;network_unobserved_calls:number;input_tokens_known:number;output_tokens_known:number;cached_tokens_known:number;total_tokens_known:number;input_tokens_unknown_calls:number;output_tokens_unknown_calls:number;cached_tokens_unknown_calls:number;usage_incomplete_calls:number;cost_cny_known:number;cost_unknown_calls:number;model_states:Record<string,number>;tool_states:Record<string,number>};
type UsageRow={id:string;kind:string;started:number;state:string;source?:string;root?:string;task?:string;model?:string;tool?:string;channel?:string|null;parent_call?:string|null;feedback_ok?:boolean|number|null;completion?:string;usage?:{input_tokens:number|null;output_tokens:number|null;cached_tokens:number|null};cost_cny?:number|null};
type Report={ok:boolean;error?:string;filters:Filters;range:{since:number;until:number;timezone:string};totals:Counts&{tasks:number;diagnostics:number;physical_attempts:number;task_states:Record<string,number>;physical_states:Record<string,number>};by_model:(Counts&{key:string;model:string;model_id:string;route:string})[];by_day:(Counts&{day:string;tasks:number})[];options:{models:{key:string;model:string;model_id:string;route:string}[];projects:string[];channels:string[]};items:UsageRow[];next_cursor:string;coverage:{first_observation:number|null;unobserved_model_admissions:number;unattributed_physical_attempts?:number};legacy:{ok:boolean;records:number|null;input_tokens_known?:number;output_tokens_known?:number;cost_cny_known?:number;cost_unknown_records?:number;error?:string}};
const initial:Filters={period:'30',model:'*',project:'*',channel:'*',kind:'model'};
const names:Record<string,string>={running:'进行中',queued:'排队',pending:'等待执行',waiting:'等待',succeeded:'已完成',returned:'已返回',failed:'失败',timed_out:'超时',cancelled:'已取消',cancelling:'取消中',interrupted:'已中断',needs_input:'待补充',paused:'已暂停',waiting_approval:'待确认',unknown:'结果未知',rejected:'未执行／拒绝',cached:'缓存复用',replayed:'原回执复用'};
const completionNames:Record<string,string>={checked:'已核对',assessed:'模型自评',incomplete:'验收未完成',unverified:'尚未核实'};
const kinds:Record<string,string>={model:'模型调用',tool:'工具调用',task:'主任务',physical:'执行尝试',diagnostic:'诊断操作'};
const channels:Record<string,string>={desktop:'桌面',local:'本机请求',feishu:'飞书',automation:'自动化',video:'视频',debug:'诊断',diagnostic:'连接测试',operation:'独立操作'};
const count=(value:number|null|undefined)=>value==null?'未知':value.toLocaleString('zh-CN');
const money=(value:number|null|undefined)=>value==null?'未知':'约 ¥'+value.toLocaleString('zh-CN',{maximumFractionDigits:6});
const knownMoney=(value:number|null|undefined,unknown:number,total:number)=>total>0&&unknown===total?'未知':money(value);
const origin=(row:UsageRow)=>row.root||(['task','diagnostic'].includes(row.kind)?row.id:row.task)||'独立操作';
const stamp=(value:number)=>new Date(value*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
function dimensionName(key:string,kind:'project'|'channel'){
  if(key==='unknown')return '归属未知';const value=key.replace(/^value:/,'');
  return kind==='channel'?(channels[value]||value||'未指定'):value||'未归属项目';
}

export function PersonalUsagePane({info,debug=false,visible=true}:{info:BackendInfo|null;debug?:boolean;visible?:boolean}){
  const active=usePageActive();const [filters,setFilters]=useState(initial),[cursor,setCursor]=useState(''),[revision,refresh]=useState(0);
  const [page,setPage]=useState<Report|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');const request=useRef(0);
  useEffect(()=>{
    if(!active||!visible)return;let live=true;const serial=++request.current;setError('');setBusy(true);
    const load=debug?Promise.resolve(preview as unknown as Report):info?rpc<Report>(info,'load_personal_usage',{filters,cursor,limit:50}):Promise.reject(Error('后端尚未连接。'));
    void load.then(result=>{if(!live||serial!==request.current)return;if(!result.ok)throw Error(result.error||'使用记录读取未完成。');
      setPage(prior=>cursor&&prior?{...result,items:[...prior.items,...result.items].filter((row,index,all)=>all.findIndex(r=>r.id===row.id)===index)}:result);
    }).catch(reason=>{if(live&&serial===request.current)setError(String(reason));}).finally(()=>{if(live&&serial===request.current)setBusy(false);});
    return()=>{live=false;};
  },[active,visible,info?.port,info?.token,debug,filters,cursor,revision]);
  function change(key:keyof Filters,value:string){setFilters(prior=>({...prior,[key]:value}));setCursor('');}
  function exportPage(){if(!page)return;const url=URL.createObjectURL(new Blob([JSON.stringify({...page,sample:debug},null,2)],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download=`personal-usage-${Date.now()}.json`;link.click();window.setTimeout(()=>URL.revokeObjectURL(url),1000);}
  const t=page?.totals;const old=page&&JSON.stringify(page.filters)!==JSON.stringify(filters)&&!debug;
  const partial=t?.usage_incomplete_calls||0;
  const maximum=Math.max(1,...(page?.by_day||[]).map(day=>day.model_calls));
  return <section className="usage-center" aria-label="个人使用中心"><header><div><h1>个人使用</h1><p>任务、模型调用与工具执行分别查看。记录保存在本机。</p></div><div className="uc-actions"><UiButton busy={busy&&!cursor} busyLabel="正在读取…" disabled={debug} onClick={()=>{setCursor('');refresh(n=>n+1);}}>刷新统计</UiButton><UiButton disabled={!page||busy} onClick={exportPage}>导出统计与已载入记录</UiButton></div></header>
    {debug&&<UiFeedback state="empty" title="静态样板，未连接后端">数据由隔离样本生成；筛选已禁用。正式页面使用认证查询。</UiFeedback>}
    <div className="uc-filters"><UiField label="日期范围"><select value={filters.period} disabled={debug} onChange={e=>change('period',e.target.value)}><option value="7">近7天</option><option value="30">近30天</option><option value="all">全部记录</option></select></UiField>
      <UiField label="模型条目与路由（仅模型用量）"><select value={filters.model} disabled={debug} onChange={e=>change('model',e.target.value)}><option value="*">全部模型</option>{page?.options.models.map(m=><option key={m.key} value={m.key}>{m.model} · {m.model_id.slice(0,8)||'未归属条目'} / {m.route.slice(0,6)}</option>)}</select></UiField>
      <UiField label="项目"><select value={filters.project} disabled={debug} onChange={e=>change('project',e.target.value)}><option value="*">全部项目</option>{page?.options.projects.map(p=><option key={p} value={p}>{dimensionName(p,'project')}</option>)}</select></UiField>
      <UiField label="渠道"><select value={filters.channel} disabled={debug} onChange={e=>change('channel',e.target.value)}><option value="*">全部渠道</option>{page?.options.channels.map(p=><option key={p} value={p}>{dimensionName(p,'channel')}</option>)}</select></UiField></div>
    {error&&<UiFeedback state="error" title={error}>{page?'下方保留上次读取结果，当前筛选尚未确认。':undefined}</UiFeedback>}
    {busy&&!page&&<UiFeedback state="loading" title="正在读取使用记录…"/>}
    {old&&<p className="uc-note" role="status">{busy?'正在读取本次筛选，暂保留上次结果。':'显示上次确认结果；当前筛选读取未完成。'}</p>}
    {page&&t&&<><p className="uc-note">{page.range.since?stamp(page.range.since):'全部记录'} 至 {stamp(page.range.until)} · 北京时间；状态取当前记录。模型筛选只影响模型调用与用量。</p>
      <div className="uc-cards"><article><h2>主任务</h2><strong>{count(t.tasks)}</strong><p>{count(t.physical_attempts)} 次物理执行 · {count(t.diagnostics)} 项诊断任务</p></article>
        <article><h2>模型调用</h2><strong>{count(t.model_calls)}</strong><p>{count(t.network_attempts)} 次网络尝试 · {count(t.network_unobserved_calls)} 次发送未观测 · {t.diagnostic_model_calls||0} 次连接测试</p></article>
        <article><h2>工具调用</h2><strong>{count(t.tool_calls)}</strong><p>实际进入适配器的尝试，结果分别核对</p></article>
        <article><h2>已知 token</h2><strong>{t.model_calls&&t.input_tokens_unknown_calls===t.model_calls&&t.output_tokens_unknown_calls===t.model_calls?'未知':count(t.total_tokens_known)}</strong><p>输入 {count(t.input_tokens_known)} / 输出 {count(t.output_tokens_known)} · {partial} 次用量不完整</p></article>
        <article><h2>已知缓存 token</h2><strong>{t.model_calls&&t.cached_tokens_unknown_calls===t.model_calls?'未知':count(t.cached_tokens_known)}</strong><p>输入的子集 · {t.cached_tokens_unknown_calls} 次未回报</p></article>
        <article><h2>已知费用估算</h2><strong>{knownMoney(t.cost_cny_known,t.cost_unknown_calls,t.model_calls)}</strong><p>{t.cost_unknown_calls} 次缺用量或价格</p></article></div>
      <p className="uc-note">费用按每次调用时的用户单价估算，缓存优惠未计入，不能代替供应商账单。主任务按首次登记日期，模型和工具按尝试时间；阶段、子伙伴和恢复不重复算主任务。</p>
      <div className="uc-states" aria-label="主任务状态">{Object.entries(t.task_states).map(([state,n])=><span key={state}>{names[state]||state} {n}</span>)}</div>
      {!!page.coverage.unobserved_model_admissions&&<UiFeedback state="unconfirmed" title={`${page.coverage.unobserved_model_admissions} 条原准入记录的执行尚未观测`}>范围为当前匹配的主任务。它们不计作实际模型调用；历史缺失无法据此补成零。</UiFeedback>}
      <p className="uc-note">模型与工具仅统计已落盘的实际尝试；最早观测：{page.coverage.first_observation?stamp(page.coverage.first_observation):'尚无记录'}。未记录的历史不回填。渠道用于归属，不代表消息发送成功。{page.coverage.unattributed_physical_attempts?` ${page.coverage.unattributed_physical_attempts} 次物理执行缺少主任务关联，未据此补造主任务。`:null}</p>
      <section className="uc-panel"><h2>每日模型调用</h2><div className="uc-trend" role="img" aria-label="每日模型调用趋势">{page.by_day.map(day=><div key={day.day} title={`${day.day}：${day.model_calls} 次调用，${day.tasks} 项主任务`}><span>{day.model_calls}</span><i style={{height:80*day.model_calls/maximum}}/><small>{day.day.slice(5)}</small></div>)}</div>{!page.by_day.length&&<UiFeedback state="empty" title="当前范围还没有已记录活动"/>}</section>
      <section className="uc-panel"><h2>模型与路由</h2><div className="uc-table"><table><thead><tr><th scope="col">模型条目 / 路由</th><th scope="col">调用</th><th scope="col">已知输入 / 输出</th><th scope="col">已知费用</th><th scope="col">未知费用</th></tr></thead><tbody>{page.by_model.map(m=><tr key={m.key}><th scope="row">{m.model}<small>{m.model_id.slice(0,8)||'未归属条目'} / {m.route.slice(0,6)}</small></th><td>{m.model_calls}</td><td>{count(m.input_tokens_known)} / {count(m.output_tokens_known)}</td><td>{knownMoney(m.cost_cny_known,m.cost_unknown_calls,m.model_calls)}</td><td>{m.cost_unknown_calls} 次</td></tr>)}</tbody></table></div></section>
      <section className="uc-panel"><div className="uc-row"><h2>记录与关联编号</h2><UiField label="记录类型"><select value={filters.kind} disabled={debug} onChange={e=>change('kind',e.target.value)}>{[['all','所有记录'],['model','模型调用'],['tool','工具调用'],['task','主任务'],['physical','执行尝试'],['diagnostic','诊断操作']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></UiField></div>
        <div className="uc-table"><table><thead><tr><th scope="col">时间 / 编号</th><th scope="col">类型 / 状态</th><th scope="col">模型或工具</th><th scope="col">关联任务 / 模型调用</th><th scope="col">输入 / 输出</th><th scope="col">估算</th></tr></thead><tbody>{page.items.map(row=><tr key={row.id}><td>{stamp(row.started)}<small title={row.id}>{row.id.slice(0,18)}</small></td><td>{row.source==='model_test'?'连接测试':kinds[row.kind]||row.kind}<small>{names[row.state]||row.state}{row.completion?' · '+(completionNames[row.completion]||row.completion):''}</small></td><td>{row.model||row.tool||'—'}</td><td><code title={origin(row)}>{origin(row).slice(0,22)}</code>{row.parent_call&&<small title={row.parent_call}>调用 {row.parent_call.slice(0,12)}</small>}</td><td>{row.kind==='model'?`${count(row.usage?.input_tokens)} / ${count(row.usage?.output_tokens)}`:'—'}</td><td>{row.kind==='model'?money(row.cost_cny):'—'}</td></tr>)}</tbody></table></div>
        {!page.items.length&&<UiFeedback state="no_results" title="没有匹配的已记录条目"/>}{page.next_cursor&&<UiButton busy={busy&&!!cursor} busyLabel="正在读取…" disabled={busy||debug} onClick={()=>setCursor(page.next_cursor)}>加载更早记录</UiButton>}</section>
      <details className="uc-panel"><summary>历史用量汇总（不与上方相加）</summary>{page.legacy.ok?<p>共 {count(page.legacy.records)} 条历史汇总；已知输入 {count(page.legacy.input_tokens_known)}、输出 {count(page.legacy.output_tokens_known)}，{money(page.legacy.cost_cny_known)}，{count(page.legacy.cost_unknown_records)} 条费用未知。历史汇总无法准确换算成任务或调用次数，也不套用项目、渠道及路由筛选。</p>:<UiFeedback state="error" title={page.legacy.error||'历史汇总读取未完成。'}/>}</details>
    </>}
  </section>;
}
