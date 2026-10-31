"""Bounded project control receipts for the existing task event/debug stream."""
import hashlib
import re
from urllib.parse import urlsplit


def selected(value,keys):
    return {key:value[key] for key in keys if key in value} if isinstance(value,dict) else {}


def receipt(tool,call_id,arguments,result):
    result=result if isinstance(result,dict) else {}
    raw_environment=result.get('environment')
    raw_environment=raw_environment if isinstance(raw_environment,dict) else dict(id=arguments.get('environment',''))
    environment=selected(raw_environment,('id','repository','ref','commit','state'))
    operation=selected(result.get('operation'),('id','kind','state','stage','error','owner_task'))
    service=selected(result.get('service') or raw_environment.get('service'),
                     ('id','state','url','log_id','operation','health','guest_port','host_port'))
    value=dict(tool=tool,call_id=call_id,ok=bool(result.get('ok')),environment=environment,
               operation=operation,service=service,error=str(result.get('error',''))[:1000])
    if isinstance(arguments.get('command'),str):value['command_sha256']=hashlib.sha256(arguments['command'].encode()).hexdigest()
    for key in ('log_id','exit_code','service_ready','stop_confirmed','replayed'):
        if key in result:value[key]=result[key]
    if tool=='project_integrity' and isinstance(result.get('integrity'),dict):
        value['integrity']=selected(result['integrity'],('complete','unchanged','scope','untracked_excluded','baseline_commit',
            'current_commit','tracked_changes','head_matches_baseline','owner','environment','sandbox_id','service_id','start_operation',
            'observed_at','observation_finished_at','received_at','request_id','guest_session','transport','new_exec','reason'))
    return value


def preview_source(result):
    """Only the parent service's verified loopback receipt can authorize a preview link."""
    if not isinstance(result,dict) or result.get('ok') is not True or result.get('service_ready') is not True:return None
    environment=result.get('environment') or {}
    service=result.get('service') or environment.get('service') or {}
    health=result.get('health') or service.get('health') or {}
    url=service.get('url','')
    try:
        parsed=urlsplit(url)
        if (parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.username or parsed.password
            or parsed.path!='/' or parsed.query or parsed.fragment or parsed.port!=service.get('host_port')
            or not 1024<=parsed.port<=65535 or health.get('ok') is not True or health.get('status')!=200):return None
    except (ValueError,TypeError):return None
    return dict(kind='project_preview',level='service_preview',url=url,title='项目本机预览',
                environment_id=environment.get('id'),service_id=service.get('id'),truncated=False,
                scope='verified service endpoint; not research body evidence')


def checkout_source(result):
    if not isinstance(result,dict) or result.get('ok') is not True:return None
    environment=result.get('environment') or {}
    repository=environment.get('repository','');commit=environment.get('commit','')
    if not isinstance(repository,str) or not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):return None
    if not isinstance(commit,str) or not re.fullmatch('[a-f0-9]{40,64}',commit):return None
    return dict(kind='repository_checkout',level='checkout',url=repository+'/tree/'+commit,requested_url=repository,
                title='实际拉取的仓库提交',commit=commit,truncated=False,scope='checkout provenance; not research body evidence')
