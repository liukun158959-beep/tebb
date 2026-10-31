"""Actual adapter attempts; never store prompts, arguments, outputs or credentials."""
from contextvars import ContextVar
import hashlib
import logging
import time
import uuid

from .usage_store import UsageStore,estimate,normalized_usage

_MODEL=ContextVar('usage_model_attempt',default=None)
_LAST=ContextVar('usage_last_model_attempt',default=None)
log=logging.getLogger(__name__)


def _safe(function,*args,**kwargs):
    try:return function(*args,**kwargs)
    except Exception as exc:
        log.warning('Usage metadata unavailable (%s)',type(exc).__name__)
        return None


def _metadata():
    from .task_budgets import current
    from .usage_sources import scope_metadata
    return scope_metadata(current())


class ModelAttempt:
    def __init__(self,model,model_id='',endpoint='',ident=None,*,source=None,store=None):
        self.id=ident or uuid.uuid4().hex;self.store=store or UsageStore();self.model=model;self.model_id=model_id
        self.endpoint=endpoint;self.source=source;self.network=[];self.finished=False;self.prices=None

    def __enter__(self):
        from .agent_debug import install
        _safe(install)
        self.metadata=_safe(_metadata)or dict(root='',task='',project_id=None,channel='unknown',source='unknown')
        if self.source:self.metadata.update(source=self.source,channel='diagnostic')
        endpoint=self.endpoint.rstrip('/')
        for suffix in('/chat/completions','/responses'):
            if endpoint.endswith(suffix):endpoint=endpoint[:-len(suffix)];break
        route=hashlib.sha256(endpoint.encode()).hexdigest()
        self.metadata.update(model=self.model,model_id=self.model_id,route=route,
            model_key=(self.model_id or self.model)+':'+route[:16])
        from .state import model_price_snapshot
        self.prices=_safe(model_price_snapshot,self.model)
        self.metadata['price_snapshot']=self.prices
        _safe(self.store.begin,self.id,'model',self.metadata)
        self.token=_MODEL.set(self)
        _LAST.set((self.metadata['root'],self.metadata['task'],self.id))
        return self

    def returned(self,usage,finish_reason=None):
        normalized=normalized_usage(usage)
        _safe(self.store.update,self.id,state='returned',ended=time.time(),usage=normalized,
            cost_cny=estimate(normalized,self.prices),finish_reason=finish_reason if isinstance(finish_reason,str)and len(finish_reason)<=64 else None)
        self.finished=True

    def __exit__(self,kind,exc,traceback):
        try:
            if not self.finished:
                partial=getattr(exc,'partial',None);usage=partial.get('usage')if isinstance(partial,dict)else None
                normalized=normalized_usage(usage)
                error=kind.__name__ if kind else ''
                state='cancelled'if error in{'ChatCancelledError','CancelledError','KeyboardInterrupt'}else'failed'if kind else'unknown'
                _safe(self.store.update,self.id,state=state,ended=time.time(),error_kind=error,
                    usage=normalized,cost_cny=estimate(normalized,self.prices))
        finally:_MODEL.reset(self.token)

    def sending(self):
        ordinal=len(self.network)+1
        self.network.append(dict(id=self.id+':'+str(ordinal),state='sending',status=None))
        _safe(self.store.update,self.id,network_attempts=self.network)
        return self,ordinal-1


def http_sending(request):
    current=_MODEL.get()
    if current and request.method=='POST'and request.url.path.endswith(('/chat/completions','/responses')):
        return current.sending()


def http_returned(handle,status=None,error=None):
    if not handle:return
    attempt,index=handle
    attempt.network[index].update(state='failed'if error or status is not None and status>=400 else'headers',status=status)
    if error:attempt.network[index]['error_kind']=type(error).__name__
    _safe(attempt.store.update,attempt.id,network_attempts=attempt.network)


def invoke_tool(name,function,*args,**kwargs):
    """Wrap only the real adapter invocation, after guards/cache/locks."""
    store=UsageStore();ident=uuid.uuid4().hex;metadata=_safe(_metadata)or dict(root='',task='',project_id=None,channel='unknown',source='unknown')
    prior=_LAST.get();parent=prior[2]if prior and prior[:2]==(metadata['root'],metadata['task'])and metadata['root']else None
    _safe(store.begin,ident,'tool',{**metadata,'tool':name,'parent_call':parent})
    try:
        result=function(*args,**kwargs)
    except BaseException as exc:
        state='cancelled'if type(exc).__name__ in {'CancelledError','KeyboardInterrupt'}else'failed'
        _safe(store.update,ident,state=state,ended=time.time(),error_kind=type(exc).__name__)
        raise
    else:
        kind='returned'
        if isinstance(result,dict):
            if result.get('ok')is False:kind='rejected'
            operation=result.get('operation')
            if isinstance(operation,dict)and operation.get('state')in{'unknown','failed','cancelled'}:kind=operation['state']
            if result.get('cache_hit')is True:kind='cached'
            if result.get('replayed')is True:kind='replayed'
        _safe(store.update,ident,state=kind,ended=time.time(),result_kind=kind)
        return result
    finally:_LAST.set(prior)
