"""Set up synthetic inputs and verify artifacts through normal product services."""
import hashlib
import io
import json
import uuid

PUBLIC_URL='https://www.rfc-editor.org/rfc/rfc2606.txt'
FIXTURE_URL='https://example.com/tebb-eval-source.txt'
FIXTURE_BODY='合成来源，仅用于模拟服务。\n2. 保留TLD\n.test .example .invalid .localhost\n3. 示例二级域名\nexample.com example.net example.org\n'


def prepare(spec, workspace, run_id, *, fixture):
    context=dict(run_id=run_id, prompt=spec['prompt'], selection=dict(skills=[],attachments=[]))
    executor=spec.get('executor')
    if executor=='local_calendar':
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


def verify(spec, task, context):
    """A successful tool event cannot substitute for a loadable, matching result."""
    executor=spec.get('executor')
    proof=dict(verified=False)
    try:
        if executor=='local_calendar':
            after=calendar_digest(context['calendar'])
            proof.update(before_sha256=context['before'],after_sha256=after)
            if context['before']!=after:raise ValueError('readonly_calendar_changed')
        elif executor=='public_source':
            from .web_snapshots import PageReader
            from .web_sources import check_citations
            sources=[e['data'] for e in task.get('events',[]) if e['kind']=='evidence'
                     and isinstance(e['data'],dict) and e['data'].get('level')=='body']
            source=next(s for s in sources if s.get('url')==context['source_url'] and s.get('complete') and not s.get('truncated'))
            saved=PageReader(context['run_id']).load(source['snapshot_id'])
            if saved['sha256']!=source['sha256']:raise ValueError('source_snapshot_mismatch')
            citation=check_citations(task.get('answer',''),sources)
            if not citation['ok'] or not citation['pages_read']:raise ValueError('actual_source_citation_missing')
            proof.update(source_url=source['url'],snapshot_id=source['snapshot_id'],sha256=saved['sha256'],
                         characters=saved['characters'],complete=True,citation=citation)
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
