"""Compose source lifecycle and the existing knowledge index, without another agent."""
import uuid
from . import knowledge, material_sources as sources


def _legacy(doc):
    locator = doc.get('local_path', '')
    if not locator and doc['id'].startswith('local:'):
        from .local_sources import get
        locator = get(doc['id'][6:])['path']
    return dict(id=sources.legacy_id(doc['id']), doc_id=doc['id'], title=doc['title'], kind='local' if locator else 'feishu',
                locator=locator or doc['id'], project_id='', revision='', observed_version=None,
                checked_at=None, state='unknown', error='', operation=None, legacy=True)


def _resolve(source_id, project_id):
    try:
        row = sources.get(source_id, project_id)
        docs = knowledge.indexed_documents()
        if not any(doc['id'] == row['doc_id'] for doc in docs):
            old = next((doc for doc in docs if not doc.get('source_id') and sources.same_location(row, doc)), None)
            if old:
                row = sources.bind_legacy_document(source_id, project_id, old['id'], row['revision'])
        return row
    except sources.SourceScopeError:
        if project_id:
            raise
        doc = next((d for d in knowledge.indexed_documents() if not d.get('source_id')
                    and not d.get('project_id') and sources.legacy_id(d['id']) == source_id), None)
        if doc is None:
            raise ValueError('资料不在当前范围')
        row = _legacy(doc)
        return {**sources.register(title=row['title'], locator=row['locator'], doc_id=doc['id'], source_id=source_id), '_just_adopted': True}


def listing(project_id=''):
    rows = sources.listing(project_id)
    docs = {doc['id']: doc for doc in knowledge.indexed_documents() if doc.get('project_id', '') == project_id}
    registered = {row['doc_id'] for row in rows}
    for row in rows:
        old = next((doc for doc in docs.values() if not doc.get('source_id') and sources.same_location(row, doc)), None)
        if old:
            row['doc_id'] = old['id']; registered.add(old['id'])
    if not project_id:
        for doc in docs.values():
            if doc['id'] not in registered:
                try:
                    rows.append(_legacy(doc))
                except (ValueError, OSError) as exc:
                    rows.append(dict(id=sources.legacy_id(doc['id']), doc_id=doc['id'], title=doc['title'], project_id='',
                                     state='error', error=str(exc), legacy=True))
    result = []
    for row in rows:
        doc = docs.get(row['doc_id'])
        state = row['state'] if row.get('legacy') else sources.availability(row)
        if state not in sources.BAD:
            state = 'unknown' if not row.get('observed_version') else 'not_indexed' if not doc else 'current' if doc.get('source_version') == row['observed_version'] else 'stale'
        result.append({**{k: v for k, v in row.items() if k != 'cancelled_operations'}, 'freshness': state,
                       'indexed': doc is not None, 'index_version': doc.get('source_version') if doc else None,
                       'indexed_at': doc.get('updated') if doc else None})
    from .project_workspace import config_path, load
    projects = []
    for path in sorted(config_path('material-options').parent.glob('*.json')):
        try:
            cfg = load(path.stem)
            projects.append(dict(id=path.stem, name=cfg.get('primary') or path.stem))
        except (ValueError, OSError):
            continue
    return dict(ok=True, project_id=project_id, sources=result, projects=projects)


def _changed(project_id):
    try:
        return listing(project_id)
    except Exception as exc:
        raise knowledge.KnowledgeChangeError('资料操作已提交，但列表读取失败，请重新查询',
                                            change_state='committed', stage='material_view') from exc


def operation(payload):
    if not isinstance(payload, dict):
        raise ValueError('资料操作参数无效')
    action = payload.get('action', 'list'); project_id = payload.get('project_id', '')
    if not isinstance(action, str) or action not in {'list', 'retrieve', 'register', 'cancel', 'rebind', 'remove_index', 'read_version', 'check', 'refresh', 'read_current'}:
        raise ValueError('不支持的资料操作')
    sources.scope(project_id)
    if action == 'list':
        return listing(project_id)
    if action == 'retrieve':
        found = knowledge.retrieve_sources(payload.get('question', ''), payload.get('doc_ids'), project_id)
        return dict(ok=True, project_id=project_id, retrieved=found, cites=found['cites'])
    if action == 'register':
        if payload.get('snapshot_id'):
            from .local_sources import get
            selected = get(payload['snapshot_id'])
            old = next((doc for doc in knowledge.indexed_documents() if doc.get('project_id', '') == project_id
                        and doc.get('local_path') == selected.get('path') and not doc.get('source_id')), None)
        else:
            old = next((doc for doc in knowledge.indexed_documents() if doc.get('project_id', '') == project_id
                        and doc['id'] == payload.get('locator') and not doc.get('source_id')), None)
        row = sources.register(title=payload.get('title', ''), snapshot_id=payload.get('snapshot_id', ''),
                               locator=payload.get('locator', ''), project_id=project_id,
                               doc_id=old['id'] if old else '', source_id=sources.legacy_id(old['id']) if old else '')
        return {**_changed(project_id), 'source_id': row['id']}
    if action == 'read_version':
        return dict(ok=True, source=sources.read_version(payload.get('source_id'), payload.get('version'), project_id,
                    payload.get('offset', 0), payload.get('limit', 12000)))
    row = _resolve(payload.get('source_id'), project_id)
    if action == 'cancel':
        sources.cancel(row['id'], project_id, payload.get('operation_id'))
        return _changed(project_id)
    if action == 'rebind':
        revision = row['revision'] if row.get('_just_adopted') and payload.get('revision') == '' else payload.get('revision')
        sources.rebind(row['id'], project_id, payload.get('snapshot_id'), revision)
        return _changed(project_id)
    if action == 'remove_index':
        knowledge.delete_doc(row['doc_id'], project_id=project_id)
        return _changed(project_id)
    ident = payload.get('operation_id') or uuid.uuid4().hex
    sources.begin(row['id'], project_id, ident, action)
    committed = False
    try:
        observed, body = sources.observe(row['id'], project_id, ident)
        if action == 'refresh':
            old = next((doc for doc in knowledge.indexed_documents() if doc['id'] == row['doc_id']), {})
            metadata = {key: old[key] for key in ('source', 'space_id', 'space_name', 'folder_name', 'url') if key in old}
            metadata.update(source_id=row['id'], source_version=observed['observed_version'], project_id=project_id)
            if row['kind'] == 'local':
                if old.get('local_path') != observed['locator']:
                    metadata.pop('folder_name', None)
                metadata.update(source='local', space_name=metadata.get('folder_name') or '本地文件')
                metadata['local_path'] = observed['locator']
            else:
                metadata.setdefault('source', 'feishu'); metadata.setdefault('space_name', '飞书文档')
                metadata.setdefault('url', observed['locator'] if observed['locator'].startswith('http') else '')
            knowledge.replace_document(row['doc_id'], observed['title'], body, metadata,
                guard=sources.commit_guard(row['id'], project_id, observed['revision'], observed['observed_version'], ident))
            committed = True
        sources.finish(row['id'], project_id, ident)
        if action == 'read_current':
            return dict(ok=True, source=sources.read_version(row['id'], observed['observed_version'], project_id,
                        payload.get('offset', 0), payload.get('limit', 12000)), change_state='observed')
        return {**listing(project_id), 'change_state': 'committed' if committed else 'observed', 'operation_id': ident}
    except Exception as exc:
        try:
            sources.finish(row['id'], project_id, ident, error=str(exc))
        except Exception:
            pass
        if isinstance(exc, knowledge.KnowledgeChangeError):
            raise
        if committed:
            raise knowledge.KnowledgeChangeError(str(exc), change_state='committed', stage='material_refresh', source_id=row['id'],
                                                index_change_state='committed', source_change_state='unconfirmed') from exc
        if isinstance(exc, sources.MaterialChangeError):
            raise
        raise knowledge.KnowledgeChangeError(str(exc), change_state='cancelled' if isinstance(exc, InterruptedError) else 'partial',
                                            stage='material_operation', index_change_state='not_changed', source_id=row['id']) from exc
