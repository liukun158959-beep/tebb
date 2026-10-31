"""Read-only composition of each domain's own task metadata projection."""
from .paths import data_root
import sqlite3


def scope_metadata(scope):
    if scope is None:return dict(root='',task='',project_id=None,channel='operation',source='operation')
    root=scope.ledger.read(scope.member['id']);owner=root['owner'];kind,_,ident=owner.partition(':')
    origin=None
    try:
        if kind=='task':
            from .tasks import usage_origin
            origin=usage_origin(ident)
        elif kind=='collab':
            from .collaboration import usage_origin
            origin=usage_origin(ident)
        elif kind=='workflow':
            from .workflows import usage_origin
            origin=usage_origin(ident)
    except (OSError,ValueError,sqlite3.Error):pass
    return dict(root=owner,task=scope.task,project_id=(origin or{}).get('project_id'),
        channel=(origin or{}).get('channel',None if kind in{'task','collab','workflow'}else'operation'),source={'collab':'collaboration'}.get(kind,kind),purpose=scope.member.get('purpose',''))


def task_metadata():
    from .tasks import usage_metadata as tasks
    from .collaboration import usage_metadata as collaboration
    from .workflows import usage_metadata as workflows
    roots=[];attempts=[]
    for path in [data_root()/'memory/tasks.sqlite3',data_root()/'memory/collaboration/work/tasks.sqlite3',data_root()/'memory/collaboration/chat/tasks.sqlite3']:
        result=tasks(path);roots.extend(result['tasks']);attempts.extend(result['attempts'])
    roots.extend(collaboration());roots.extend(workflows())
    owners={r['id']:r for r in roots}
    for attempt in attempts:
        origin=owners.get(attempt['root'])
        attempt['queue']=attempt['channel']
        if origin:attempt.update(project_id=origin['project_id'],channel=origin['channel'])
        elif attempt['channel']=='collaboration':attempt['channel']=None
    return dict(tasks=list(owners.values()),attempts=list({r['id']:r for r in attempts}.values()))
