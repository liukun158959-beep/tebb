"""Bounded, observable draft correction around the existing chart service."""
import copy
import hashlib
import json
import uuid
from . import report_charts as charts
from .chart_schema import ChartInputError
from .resource_lock import ResourceLock


SPEC=copy.deepcopy(charts.SPEC)
SPEC['function']['parameters']['properties']['draft_id']=dict(type='string',pattern='^[a-f0-9]{32}$',description='预检返回的图稿编号；修正同一图稿时必须原样带回。不修改标题来绕过两轮修正限制。')
SPEC['function']['description']+='先预检；同图首次提交后最多两轮定向修正。修正必须带回draft_id；只修图稿的具体字段，保留其他内容，不删除真实关系以通过校验。成功回执可复用，不能重建已成功的图。'


class ChartBuildError(RuntimeError):
    def __init__(self,message,**details):
        super().__init__(message);self.details={'repairable':False,**details}


def build(arguments,sources,run_id,emit):
    spec={k:v for k,v in arguments.items() if k!='draft_id'}
    alias=hashlib.sha256(json.dumps([spec.get('type'),spec.get('title')],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    requested=arguments.get('draft_id')
    if requested is not None:
        try:charts.valid_id(requested)
        except ValueError:raise ChartInputError([dict(path='/draft_id',message='须使用本次预检返回的32位图稿编号。')])
    def resolve(value):
        drafts=value.setdefault('drafts',{})
        if requested:
            if requested not in drafts:raise ChartBuildError('图稿不属于当前任务链。',stage='preflight',draft_id=requested)
            return requested
        existing=next((i for i,r in drafts.items() if r['alias']==alias),None)
        if existing:return existing
        if len(drafts)>=8:raise ChartBuildError('本任务已登记8份图稿，请修正已有图稿，不继续扩展。',stage='preflight')
        ident=uuid.uuid4().hex;drafts[ident]=dict(alias=alias,submissions=0);return ident
    draft_id=charts.change_budget(run_id,resolve)
    with ResourceLock('chart-draft-'+run_id+'-'+draft_id).hold(blocking=False):
        record=charts.change_budget(run_id,lambda v:dict(v['drafts'][draft_id]))
        def event(stage,**extra):emit('chart_build',dict(draft_id=draft_id,title=str(spec.get('title','未命名图稿'))[:120],stage=stage,attempt=record['submissions'],spec=spec if len(json.dumps(spec,ensure_ascii=False))<=24000 else None,**extra))
        if record.get('inflight'):
            try:
                meta,_=charts.load(record['pending_asset'])
                if meta['run_id']!=run_id or meta['spec']!=record['pending_spec']:raise ValueError('登记不匹配')
                record.update(asset_id=record['pending_asset'],fingerprint=record['pending_fingerprint'],inflight=False)
                charts.change_budget(run_id,lambda v:v['drafts'][draft_id].update(record))
            except (OSError,ValueError,KeyError):
                event('unconfirmed',asset_id=record.get('pending_asset',''))
                raise ChartBuildError('此前构建中断且登记未核实，不自动重建；请核对原编号。',stage='registration',draft_id=draft_id,chart_id=record.get('pending_asset',''))
        if record.get('blocked'):
            event('unconfirmed',asset_id=record.get('asset_id',''))
            raise ChartBuildError('此前构建保存阶段未确认，不能自动重建；请核对原图表编号。',stage='registration',draft_id=draft_id,chart_id=record.get('asset_id',''))
        try:references=charts.validate(spec,sources)
        except ChartInputError as exc:references=None;problems=exc.details['issues']
        fingerprint=hashlib.sha256(json.dumps(dict(spec=spec,sources=references),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if references is not None and record.get('fingerprint')==fingerprint and record.get('asset_id'):
            public=charts.public(record['asset_id']);ident=public['id'];event('reused',asset_id=ident)
            return dict(ok=True,id=ident,draft_id=draft_id,reused=True,markdown='```tebb-chart\n'+ident+'\n```',sources=public['sources'],note='复用当前任务中已成功图表，不增加渲染次数。')
        if record['submissions']>=3:
            event('exhausted',issues=problems if references is None else [])
            raise ChartBuildError('同一图稿已使用首次提交及两轮定向修正；保留已有正文和参数，未继续构建。',stage='preflight',draft_id=draft_id,issues=problems if references is None else [])
        record['submissions']+=1
        charts.change_budget(run_id,lambda v:v['drafts'][draft_id].update(record))
        if references is None:
            event('preflight_failed',issues=problems)
            raise ChartInputError(problems,draft_id=draft_id,repairs_remaining=3-record['submissions'],repairable=record['submissions']<3)
        record.update(inflight=True,pending_asset=uuid.uuid4().hex,pending_spec=spec,pending_fingerprint=fingerprint)
        charts.change_budget(run_id,lambda v:v['drafts'][draft_id].update(record))
        event('validated')
        try:result=charts.create(spec,sources,run_id,ident=record['pending_asset'])
        except Exception as exc:
            details=getattr(exc,'details',{});unconfirmed=details.get('stage') in {'save_png','save_svg','registration'}
            record['inflight']=False
            if unconfirmed:record.update(blocked=True,asset_id=details.get('chart_id',''))
            charts.change_budget(run_id,lambda v:v['drafts'][draft_id].update(record))
            event('unconfirmed' if unconfirmed else 'exhausted' if details.get('stage')=='budget' else 'render_failed',error=str(exc),asset_id=details.get('chart_id',''))
            raise ChartBuildError(str(exc),**{**details,'draft_id':draft_id,'repairable':details.get('repairable',not unconfirmed and record['submissions']<3)}) from exc
        record.update(fingerprint=fingerprint,asset_id=result['id'],inflight=False);charts.change_budget(run_id,lambda v:v['drafts'][draft_id].update(record))
        event('built',asset_id=result['id'])
        return {**result,'draft_id':draft_id,'repairs_remaining':3-record['submissions']}
