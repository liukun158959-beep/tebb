"""Fixed-source evaluation data and checks, over the normal collaboration owner."""
import hashlib
import json
from pathlib import Path
import re
import uuid

URLS={8259:'https://www.rfc-editor.org/rfc/rfc8259.txt',7159:'https://www.rfc-editor.org/rfc/rfc7159.txt'}
YEARS={8259:2017,7159:2014}
MODES={'serial','legacy_parallel','atlas_parallel'}


def load_corpus(folder):
    folder=Path(folder).resolve();manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version')!=1 or manifest.get('scope')not in{'frozen_real_public_sources','synthetic_fixture'}:raise ValueError('unsupported research corpus')
    rows={}
    for item in manifest['sources']:
        if item.get('url')not in URLS.values()or item['url']in rows:raise ValueError('unexpected or duplicate corpus URL')
        path=(folder/item['file']).resolve();path.relative_to(folder)
        if not 0<path.stat().st_size<=1500000:raise ValueError('corpus file size invalid')
        body=path.read_bytes().decode('utf-8')
        if hashlib.sha256(body.encode()).hexdigest()!=item['sha256']or len(body)!=item['characters']:raise ValueError('corpus hash mismatch')
        rows[item['url']]=dict(item,text=body)
    if set(rows)!=set(URLS.values()):raise ValueError('two fixed sources required')
    return manifest['scope'],rows


def prepare_corpus(profile,source=None,*,live=False):
    folder=Path(profile)/'memory/eval_research_corpus';folder.mkdir(parents=True,exist_ok=True)
    if source is None:
        if live:raise ValueError('live readonly comparison requires explicit frozen corpus')
        rows=[]
        for number,url in URLS.items():
            text=f'Controlled synthetic source.\nRequest for Comments: {number}\nPublication year: {YEARS[number]}\n'+'Fixture paragraph. '*15
            file=f'rfc{number}.txt';(folder/file).write_text(text,encoding='utf-8',newline='')
            rows.append(dict(url=url,file=file,sha256=hashlib.sha256(text.encode()).hexdigest(),characters=len(text),fetched_at='2040-01-01T00:00:00+00:00'))
        manifest=dict(version=1,scope='synthetic_fixture',sources=rows)
    else:
        scope,records=load_corpus(source)
        if live and scope!='frozen_real_public_sources':raise ValueError('live corpus must contain frozen actual public sources')
        rows=[]
        for index,item in enumerate(records.values()):
            file=f'source-{index}.txt';(folder/file).write_text(item['text'],encoding='utf-8',newline='')
            rows.append({**{k:v for k,v in item.items()if k!='text'},'file':file})
        manifest=dict(version=1,scope=scope,sources=rows)
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    load_corpus(folder)
    return dict(scope=manifest['scope'],sources=[{k:item[k]for k in('url','sha256','characters','fetched_at')}for item in manifest['sources']])


def configure(spec,service,model_id,profile,proof):
    mode=spec['research_mode']
    if mode not in MODES:raise ValueError('unsupported research mode')
    agents=service.snapshot()['state']['agents']
    writer=next(a for a in agents if a['id']=='writer')
    service.action(dict(type='agent',agent={**writer,'model':model_id,'tools':['网页读取']},request_id=uuid.uuid4().hex))
    other=next((a for a in agents if a['name']=='快照B'),None)
    state=service.action(dict(type='agent',agent={**(other or{}),'name':'快照B','role':'只读核对指定归档文档的编号和年份',
        'memory':'','model':model_id,'tools':['网页读取']},request_id=uuid.uuid4().hex))['state']
    other=next(a for a in state['agents']if a['name']=='快照B')
    assignments={'writer':URLS[8259],other['id']:URLS[7159]};spec['readonly_readers']=assignments
    scope,corpus=load_corpus(Path(profile)/'memory/eval_research_corpus')
    proof.update(research_mode=mode,execution_strategy=spec.get('execution_strategy','auto'),corpus_scope=scope,corpus_hashes={url:item['sha256']for url,item in corpus.items()},reader_assignments=assignments)
    instruction=('这是固定归档来源的只读研究对照，不执行外部写入。必须实际提交两个互不依赖的collect分工：'
        +'；'.join(f'agent_id={agent}只读source_url={url}，仅核对文档头部RFC编号及正式发布日期年份，子任务只返回JSON number/year/citation'for agent,url in assignments.items())
        +'。不要将子任务设为deliver，不要互相依赖，不搜索其他材料。citation字段提交给系统时必须用"[完整实际证据id]"（保留方括号，变量须替换为实际id），不能只填裸id。系统会把它渲染为最终citation中的实际Markdown链接，最终成品不用保留内部编号；不能把系统渲染后的链接判为缺内部编号。'
        '负责人等待两份真实成果，核对来源后只返回JSON对象rfc8259和rfc7159，各含number、year、citation；不能凭记忆填年份，不添加其他键。'
        '只需头部实际段落，不要求全文。归档URL仅定位原来源，不表示实时查询。')
    instruction=' '.join('@'+ident for ident in assignments)+' '+instruction
    spec['prompt']=instruction
    state=service.action(dict(type='group',title='只读归档对照',goal='两个独立文档采集后合并',members=['research','writer',other['id']],request_id=uuid.uuid4().hex))['state']
    return state['selected'],instruction


def judge(root,runs,proof):
    from .agent_evals import json_answer
    from .web_sources import evidence_sources,check_citations
    proof.pop('research_failure',None)
    def reject(reason):proof['research_failure']=reason;return False
    if root['state']!='succeeded'or not proof.get('accepted_delivery')or not proof.get('workers_exited_before_shutdown'):return reject('root_delivery_or_worker_cleanup')
    children=[r for r in runs if r.get('parent')==root['id']]
    if len(children)!=2:return reject('two_actual_children_required')
    if proof.get('execution_strategy')=='delegate'and proof.get('actual_requested_strategy')!='delegate':return reject('explicit_delegation_not_observed')
    if not proof.get('child_intervals_complete'):return reject('actual_worker_intervals_missing')
    expected_runtime='atlas_readonly'if proof['research_mode']=='atlas_parallel'else'legacy'
    if proof.get('worker_overlap')!=(proof['research_mode']!='serial'):return reject('actual_worker_overlap_mismatch')
    try:
        merged=json_answer(root['answer'])
        if set(merged)!={'rfc8259','rfc7159'}:return reject('root_output_keys')
        for child in children:
            if child['state']!='succeeded'or child.get('dependencies')or child.get('execution_runtime')!=expected_runtime:return reject('child_state_dependencies_or_runtime')
            url=proof['reader_assignments'][child['agent']];number=next(n for n,u in URLS.items()if u==url)
            value=json_answer(child['answer']);combined=merged['rfc'+str(number)]
            for answer in(value,combined):
                if set(answer)!={'number','year','citation'}or type(answer['number'])is not int or type(answer['year'])is not int:return reject('child_or_root_field_types')
                if answer['number']!=number or answer['year']!=YEARS[number]:return reject('child_or_root_values')
            sources=evidence_sources(child.get('evidence',[]))
            actual=[s for s in sources if s.get('url')==url and s.get('level')=='body'and s.get('sha256')==proof['corpus_hashes'][url]]
            if not actual or not check_citations(value['citation'],actual)['ok']or not check_citations(combined['citation'],actual)['ok']:return reject('actual_source_hash_or_citation')
            if not any(re.search(r'Request for Comments:\s*'+str(number),s['text'])and str(YEARS[number])in s['text']for s in actual):return reject('required_header_not_actually_read')
            if not child.get('feedback_check',{}).get('ok'):return reject('child_independent_review')
        return True
    except (ValueError,TypeError,KeyError):return reject('invalid_or_missing_verification_material')


def fixture_delta(request,spec,tool):
    names={item['function']['name']for item in request.get('tools',[])}
    if 'request_clarification'in names:
        goal=next(m['content']for m in reversed(request['messages'])if m.get('role')=='user')
        return tool('start_background_task',dict(instructions=goal,strategy='delegate'))
    if 'submit_plan'in names:
        return tool('submit_plan',dict(tasks=[dict(agent_id=agent,title='归档头部核对',instructions='只读取source_url='+url+'，实际核对编号和年份，只返回JSON number/year/citation。',
            depends_on=None,stage='collect',required_sources=[])for agent,url in spec['readonly_readers'].items()]))
    receipts=[m for m in request['messages']if m.get('role')=='tool']
    if 'web_fetch'in names:
        if not receipts:
            goal=next(m['content']for m in reversed(request['messages'])if m.get('role')=='user'and str(m.get('content','')).startswith('本轮当前任务职责'))
            assigned=json.loads(goal.split('\n',1)[1])['task'];url=next(url for url in URLS.values()if url in assigned)
            return tool('web_fetch',dict(url=url))
        from .web_sources import source_catalog
        body=json.loads(receipts[-1]['content']);number=int(re.search(r'Request for Comments:\s*(\d+)',body['text'])[1]);year=int(re.search(r'20\d\d',body['text'])[0])
        return dict(content=json.dumps(dict(number=number,year=year,citation='['+source_catalog([body])[0]['id']+']')))
    found={}
    def visit(value):
        if isinstance(value,dict):
            if set(value)=={'number','year','citation'}:found[value['number']]=value
            for child in value.values():visit(child)
        elif isinstance(value,list):
            for child in value:visit(child)
        elif isinstance(value,str):
            try:decoded=json.loads(value)
            except (ValueError,TypeError):return
            if decoded!=value:visit(decoded)
    for row in request['messages']:
        content=row.get('content','')
        visit(content)
        if isinstance(content,str)and'\n'in content:visit(content.split('\n',1)[1])
    result={'rfc'+str(n):found[n]for n in URLS}
    return tool('submit_deliverable',dict(report=json.dumps(result),complete=True,missing=[],limitations=[]))if 'submit_deliverable'in names else dict(content=json.dumps(result))
