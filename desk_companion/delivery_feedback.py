"""Product handoff checks, reusing the real source/chart/receipt validators."""
import re

from . import feedback
from .web_sources import render_citations,check_citations,source_catalog


def assess(request,text,sources,delivery,receipts,*,goal=None):
    if goal is None:goal=request.get('objective',request['text'])if request.get('stage')=='deliver'else request['text']
    declared=request.get('output_contract')
    structured=feedback.output_contract(goal,declared)is not None
    checks=feedback.check_output(text,goal,declared)
    audit=None
    if sources:
        text,unknown=render_citations(text,sources,structured=structured)
        audit=check_citations(text,sources,structured=structured)
        if unknown:audit.update(ok=False,unknown_ids=unknown)
        # A user-selected strict data schema must not gain citation fields.
        required=not structured or bool(re.search(r'citation|reference|引用|来源',goal,re.I))
        checks.append(feedback.check('citation_provenance','source_url','pass'if audit['ok']else 'fail'if required else 'unknown',
            '仅核对引用来自实际工具记录，不证明断言受支持。',required=required,audit=audit))
        catalog=source_catalog(sources)
        partial=[s['id']for s in catalog if s.get('level')!='body'or s.get('truncated')is True or s.get('complete')is False]
        checks.append(feedback.check('reading_coverage','reading','unknown'if partial else 'pass',
            '存在摘要或未读完整材料；只能使用实际取得区间。'if partial else '已记录正文读取；是否支持具体断言另行审阅。',required=False,partial_source_ids=partial))
    from .report_charts import validate_report
    try:
        validate_report(text,request.get('source_scope',request['collab_run']),request.get('requires_charts',False))
        if request.get('requires_charts'):checks.append(feedback.check('required_charts','artifact','pass','明确要求的真实图表资产已核对。'))
    except (ValueError,OSError):checks.append(feedback.check('required_charts','artifact','fail','图表交付未确认：缺少真实图表或资产核对失败。'))
    present=set()
    if any(s.get('kind')=='github'and s.get('level')=='body'and '/blob/'in s.get('url','')for s in sources):present.add('repository')
    if any(s.get('kind')=='page'and s.get('level')=='body'for s in sources):present.add('web')
    for missing in sorted(set(request.get('required_sources',[]))-present):
        checks.append(feedback.check('required_source_'+missing,'reading','fail','未取得本任务要求的一手正文来源：'+missing))
    if request.get('stage')=='deliver':
        accepted=delivery is not None and delivery['complete']and not delivery['missing']
        checks.append(feedback.check('delivery_declaration','task_acceptance','pass'if accepted else 'fail',
            '模型交付声明不独立证明目标完成。'if accepted else '未提交完整交付，或模型明确标记核心目标仍有缺失。',basis='model_declaration',
            missing=delivery.get('missing',[])if delivery else ['缺少submit_deliverable回执']))
    checks.extend(feedback.check_receipts(receipts,request.get('acceptance',[])))
    # Recheck shape after citation compilation; keys/types must stay intact.
    if structured:checks=feedback.check_output(text,goal,declared)+[c for c in checks if c['dimension']not in {'format','schema'}]
    return text,checks,audit
