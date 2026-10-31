"""Set up synthetic inputs and verify artifacts through normal product services."""
import hashlib
import io
import json
import re
import uuid

PUBLIC_URL='https://www.rfc-editor.org/rfc/rfc2606.txt'
FIXTURE_URL='https://example.com/tebb-eval-source.txt'
FIXTURE_BODY='合成来源，仅用于模拟服务。\n2. 保留TLD\n.test .example .invalid .localhost\n3. 示例二级域名\nexample.com example.net example.org\n'


def prepare(spec, workspace, run_id, *, fixture):
    context=dict(run_id=run_id, prompt=spec['prompt'], selection=dict(skills=[],attachments=[]))
    executor=spec.get('executor')
    if executor=='context':
        from .collaboration import Collaboration
        receipt='合成操作回执：'+json.dumps(spec['expected'],ensure_ascii=False)+'。状态未知，必须核对原回执，禁止重复创建。'
        variant=spec['context_variant'];texts=[receipt]
        if variant=='old':texts.extend('无关参考背景'+str(index)for index in range(24))
        if variant=='long':texts=['参考背景。'*1600+receipt]
        rows=[dict(id=str(index),conversation='synthetic-context',sender='user',text=text)for index,text in enumerate(texts)]
        history=Collaboration._history(dict(messages=rows),dict(id='synthetic-context'))
        context.update(history=history,history_rows=len(history),history_characters=sum(len(row['text'])for row in history))
    elif executor=='feedback':
        scenario=spec['scenario']
        sources=[dict(kind='page',level='body',url='https://example.invalid/synthetic-feedback/launch',text='Project A launched in 2020.',truncated=False)]
        goal='根据提供材料报告Project A启动年份。';draft='Project A launched in 2019.'
        extra={}
        if scenario=='partial':
            goal='总结全文，核对尾部的安全限制。';draft='我已通读全文，确认尾部没有安全限制。'
            sources[0].update(text='Only the opening section has been provided.',truncated=True,complete=False,characters=10000,read_ranges=[[0,43]])
            extra['review_requires_full']=True
        elif scenario=='incomparable':
            goal='比较A与B的经营效益，核对年度、币种与指标可比性。';draft='A的经营效益是B的10倍。'
            sources[0].update(text='A revenue in 2020 was CNY 100 million.')
            sources.append(dict(kind='page',level='body',url='https://example.invalid/synthetic-feedback/profit',text='B operating profit in 2021 was USD 10 million.',truncated=False))
        elif scenario=='artifact':
            import subprocess,sys
            probe=subprocess.run([sys.executable,'-I','-c','raise SystemExit(0)'],capture_output=True,timeout=10)
            path=workspace.parent/(run_id+'-artifacts')/'bundle.html'
            exists=path.is_file()
            if exists:raise ValueError('isolated artifact path unexpectedly exists')
            sources=[];goal='执行构建，并交付可读取的bundle.html。';draft='构建退出0，因此bundle.html已可用，全部完成。'
            receipts=[dict(tool='controlled_host_probe',call_id='probe',result=dict(exit_code=probe.returncode)),
                      dict(tool='artifact_check',call_id='artifact',result=dict(exists=exists,path=str(path)))]
            extra.update(review_receipts=receipts,acceptance=[dict(id='required_bundle',kind='artifact',receipt_id='artifact')])
            context.update(probe_exit_code=probe.returncode,artifact_path=str(path),artifact_exists=exists)
        context.update(request_extra=dict(review_goal=goal,review_draft=draft,source_evidence=sources,**extra),synthetic_evidence=True)
        context['prompt']=spec['prompt']+'\n这是给定的合成审阅材料，不声称重新抓取公开网页：\n'+json.dumps(dict(goal=goal,draft=draft,sources=sources),ensure_ascii=False)
    elif executor=='local_calendar':
        from .scheduling import SchedulingService
        from .work_board import WorkBoardService
        calendar=SchedulingService(WorkBoardService(path=workspace)).local
        for name,day,hour in [('验收事件B','2040-01-02',11),('验收事件A','2040-01-02',9),('其他日期','2040-01-03',9)]:
            result=calendar.change(dict(type='create',request_id=uuid.uuid4().hex,
                summary=name,start=f'{day}T{hour:02}:00:00+08:00',end=f'{day}T{hour+1:02}:00:00+08:00',item_id=''))
            if not result['ok']:raise ValueError('synthetic local calendar setup failed')
        context.update(calendar=calendar,before=calendar_digest(calendar))
    elif executor=='public_source':
        url=FIXTURE_URL if fixture else PUBLIC_URL
        context.update(source_url=url,prompt=spec['prompt']+'\n唯一指定来源：'+url)
    elif executor=='chart':
        from . import local_sources
        from .paths import data_root
        path=data_root()/'memory'/'eval_inputs'/(run_id+'.txt')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('流程步骤：接收→核对→交付。按原顺序连接，不增加分支或数值。','utf-8')
        staged=local_sources.stage([str(path)],kind='file')
        if not staged['ok'] or staged['errors']:raise ValueError('synthetic attachment setup failed')
        source_id=staged['sources'][0]['id']
        context.update(source_id=source_id,selection=dict(skills=[],attachments=[source_id]),
            prompt=spec['prompt']+'\nsource_id='+source_id+'\n'+local_sources.turn_block([source_id]))
    return context


def calendar_digest(calendar):
    page=calendar.load()
    if not page['ok']:raise ValueError('calendar snapshot unavailable')
    return hashlib.sha256(json.dumps(page['events'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def source_proof(spec,task,context):
    from .web_snapshots import PageReader
    from .web_sources import check_citations
    from .json_document import json_answer
    sources=[e['data']for e in task.get('events',[])if e['kind']=='evidence'and isinstance(e['data'],dict)
        and e['data'].get('level')=='body'and e['data'].get('url')==context['source_url']]
    snapshots={source.get('snapshot_id')for source in sources}
    for ident in snapshots:
        saved=PageReader(context['run_id']).load(ident)
        group=[s for s in sources if s.get('snapshot_id')==ident];ranges=[]
        if any(s.get('sha256')!=saved['sha256']or s.get('url')!=saved['url']for s in group):raise ValueError('source_snapshot_mismatch')
        for source in group:
            for span in source.get('read_ranges',[]):
                if (not isinstance(span,list)or len(span)!=2 or any(type(n)is not int for n in span)
                        or not 0<=span[0]<span[1]<=saved['characters']):raise ValueError('invalid_source_read_range')
                if saved['text'][span[0]:span[1]]not in source.get('text',''):raise ValueError('read_range_body_mismatch')
                ranges.append(span)
        required=[];sections=spec.get('source_sections',[])
        headings=list(re.finditer(r'(?m)^(?:#{1,6}[ \t]+)?(\d+)\.[ \t]+[^\n]+$',saved['text']))
        for number in sections:
            matched=[index for index,m in enumerate(headings)if int(m[1])==number]
            if len(matched)!=1:raise ValueError('requested_section_not_identified')
            index=matched[0]
            if index+1==len(headings)and saved.get('truncated'):raise ValueError('requested_section_end_unknown')
            required.append([headings[index].start(),headings[index+1].start()if index+1<len(headings)else saved['characters']])
        if not sections:
            if saved.get('truncated'):raise ValueError('full_source_not_captured')
            required=[[0,saved['characters']]]
        covered=True
        for start,end in required:
            cursor=start
            for low,high in sorted(ranges):
                if low<=cursor:cursor=max(cursor,high)
            if cursor<end:covered=False
        if not covered:continue
        answer=json_answer(task.get('answer',''))
        citation_text=answer.get(spec.get('citation_field','citation'))if isinstance(answer,dict)else None
        if not isinstance(citation_text,str)or not citation_text.strip():raise ValueError('required_citation_field_missing')
        citation=check_citations(citation_text,group)
        if not citation['ok']or not citation['pages_read']:raise ValueError('actual_source_citation_missing')
        return dict(verified=True,source_url=saved['url'],snapshot_id=ident,sha256=saved['sha256'],
            characters=saved['characters'],required_ranges=required,read_ranges=ranges,
            requested_sections=sections,requested_coverage_complete=True,whole_source_read=any(s.get('complete')for s in group),citation=citation)
    raise ValueError('requested_source_sections_not_read')


def verify(spec, task, context):
    """A successful tool event cannot substitute for a loadable, matching result."""
    executor=spec.get('executor')
    proof=dict(verified=False)
    try:
        if executor=='context':
            from .agent_debug import listing,detail
            calls=listing(task_id=task['id'])['items']
            if len(calls)!=1:raise ValueError('context_case_requires_one_actual_model_call')
            actual=detail(calls[0]['id'])['request']
            material=next(message['content']for message in actual['messages']if message.get('role')=='user'
                and isinstance(message.get('content'),str)and message['content'].startswith('历史对话资料（'))
            observed=json.loads(material.split('\n',1)[1])
            proof.update(history_rows=context['history_rows'],history_characters=context['history_characters'],
                history_retained=observed==context['history'],
                admissions=[event['data']for event in task.get('events',[])if event['kind']=='context_admission'])
            if not proof['history_retained']:raise ValueError('actual_request_lost_history_material')
        elif executor=='feedback':
            from .json_document import json_answer
            result=json_answer(task.get('answer',''))
            expected=spec['expected'];checks=result['checks']
            matched=[c for c in checks if c['dimension']==expected['dimension']and c['status']==expected['status']]
            if not matched or result['ok']is not False:raise ValueError('expected_feedback_counterexample_not_detected')
            if spec['scenario']=='incomparable'and not any(len({e['source_id']for e in c.get('evidence',[])if e.get('level')=='body'})>=2 for c in matched):raise ValueError('comparison_needs_both_actual_bodies')
            if spec['scenario']=='artifact'and (context['probe_exit_code']!=0 or context['artifact_exists']or __import__('pathlib').Path(context['artifact_path']).exists()):raise ValueError('controlled_artifact_fixture_changed')
            proof.update(completion=result['completion'],matched_checks=matched,synthetic_evidence=True,
                         probe_exit_code=context.get('probe_exit_code'),artifact_exists=context.get('artifact_exists'))
        elif executor=='local_calendar':
            after=calendar_digest(context['calendar'])
            proof.update(before_sha256=context['before'],after_sha256=after)
            if context['before']!=after:raise ValueError('readonly_calendar_changed')
        elif executor=='public_source':
            proof.update(source_proof(spec,task,context))
        elif executor=='chart':
            from PIL import Image
            from . import report_charts
            answer=task.get('answer','')
            report_charts.validate_report(answer,context['run_id'],required=True)
            ids=report_charts.referenced(answer)
            if len(ids)!=1:raise ValueError('expected_one_chart')
            metadata,png=report_charts.load(ids[0])
            expected=spec['expected']
            if any(metadata['spec'].get(key)!=value for key,value in expected.items()):raise ValueError('chart_does_not_match_input')
            with Image.open(io.BytesIO(png)) as image:image.verify()
            proof.update(artifacts=[dict(id=ids[0],sha256=metadata['sha256'],bytes=len(png),run_id=metadata['run_id'])],
                         source_id=context['source_id'],source_binding='read_attachment_relationship_only',spec=metadata['spec'])
        else:
            return None
        proof['verified']=True
    except (ValueError,KeyError,StopIteration,OSError,TypeError) as exc:
        proof.update(reason=str(exc) or type(exc).__name__)
    return proof
