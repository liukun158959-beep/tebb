import type { FeedbackReport } from './chat-prototype-state';

const dimensions:Record<string,string>={source_url:'来源链接',reading:'已读范围',claim_support:'结论支持',numeric_comparability:'数值口径',command:'命令退出',test_coverage:'验证范围',artifact:'产物',format:'输出格式',schema:'字段与类型',task_acceptance:'目标要求'};
const labels={incomplete:'尚未完成',unverified:'待核实',assessed:'模型审阅通过',checked:'声明条件已核对'};
export function DeliveryFeedback({report}:{report:FeedbackReport}){
  return <details className={report.ok?'cp-muted':'cp-error'}>
    <summary>交付反馈 · {labels[report.completion]}</summary>
    <p>{report.scope}</p>
    <ul>{report.checks.map(check=><li key={check.id}>
      {dimensions[check.dimension]||'交付条件'} · {check.status==='pass'?'通过':check.status==='fail'?'未通过':'未核实'}
      {' · '}{check.basis==='model_assessment'?'模型判读':check.basis==='model_declaration'?'交付声明':'回执或规则核对'}：{check.message}
    </li>)}</ul>
    {report.unknown_operations.map(operation=><p key={operation.call_id}>操作结果未知 · <code>{operation.operation_id||operation.call_id}</code> · 请核对原回执。</p>)}
  </details>;
}
