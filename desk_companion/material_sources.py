"""Explicit material identities and immutable text versions, independent of indexes."""
from contextlib import contextmanager
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from .paths import data_root
from .resource_lock import ResourceLock
from .video import atomic_write

STORE = ResourceLock('material-sources')
BAD = {'missing', 'error', 'revoked', 'stale'}


class MaterialChangeError(RuntimeError):
    def __init__(self, message):
        super().__init__(message)
        self.details = dict(change_state='unconfirmed', stage='material_save')


class SourceScopeError(ValueError):
    pass


def root():
    return data_root() / 'memory' / 'materials'


def _load():
    path = root() / 'sources.json'
    data = json.loads(path.read_text('utf-8')) if path.exists() else {'schema_version': 1, 'sources': []}
    if not isinstance(data, dict) or data.get('schema_version') != 1 or not isinstance(data.get('sources'), list):
        raise ValueError('资料登记格式或版本不受支持，未改写原文件')
    return data


def _save(data):
    try:
        atomic_write(root() / 'sources.json', data)
    except Exception as exc:
        raise MaterialChangeError('资料登记写入结果未确认，请查询原来源核对，不要直接重放操作') from exc


def _id(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
        raise ValueError('资料来源编号无效')
    return value


def scope(project_id):
    if not isinstance(project_id, str):
        raise ValueError('资料范围无效')
    if project_id:
        from .project_workspace import config_path, load
        if not config_path(project_id).is_file():
            raise ValueError('项目目录授权不存在')
        return load(project_id)
    return None


def listing(project_id=''):
    scope(project_id)
    with STORE:
        return [dict(row) for row in _load()['sources'] if row['project_id'] == project_id]


def get(source_id, project_id=''):
    _id(source_id)
    with STORE:
        row = next((r for r in _load()['sources'] if r['id'] == source_id), None)
    if row is None or row['project_id'] != project_id:
        raise SourceScopeError('资料不在当前范围')
    return dict(row)


def legacy_id(doc_id):
    return uuid.uuid5(uuid.NAMESPACE_URL, 'tebb-material:' + doc_id).hex


def same_location(row, doc):
    locator = doc.get('local_path') if row['kind'] == 'local' else doc['id']
    return bool(locator) and row['project_id'] == doc.get('project_id', '') and (
        os.path.normcase(row['locator']) == os.path.normcase(locator) if row['kind'] == 'local' else row['locator'] == locator)


def bind_legacy_document(source_id, project_id, doc_id, expected_revision):
    """Attach an older index identity to a registered source that has no managed index."""
    def change(row):
        if row['revision'] != expected_revision or (row.get('operation') or {}).get('state') == 'running':
            raise ValueError('来源正在变化，请重新核对')
        row.update(doc_id=doc_id, revision=uuid.uuid4().hex)
    return _update(source_id, project_id, change)


def _local_locator(value, project_id):
    from .local_sources import _linked
    from .project_workspace import resolve
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('资料需要绝对路径')
    for part in [path, *path.parents]:
        if part.exists() and _linked(part):
            raise ValueError('资料路径不能经过符号链接或目录联接')
    path = path.resolve()
    cfg = scope(project_id)
    grant = {}
    if cfg:
        for key, base in [('main', cfg['primary']), *[(f'ref-{i}', p) for i, p in enumerate(cfg['references'])]]:
            if base and path.is_relative_to(Path(base)):
                relative = path.relative_to(Path(base)).as_posix()
                checked, _ = resolve(key, relative, cfg)
                if checked != path:
                    raise ValueError('资料位置已变化')
                grant = dict(root_id=key, relative=relative, workspace_revision=cfg['revision'])
                break
        if not grant:
            raise ValueError('资料不在当前项目授权目录')
    if path.exists() and (not path.is_file() or path.stat().st_nlink > 1):
        raise ValueError('请选择普通资料文件，不读取硬链接')
    return str(path), grant


def register(*, title, snapshot_id='', locator='', project_id='', doc_id='', source_id=''):
    """Selection/legacy adoption records identity; it neither indexes nor deletes files."""
    scope(project_id)
    if snapshot_id:
        from .local_sources import get as attachment
        selected = attachment(snapshot_id)
        if selected['kind'] != 'file':
            raise ValueError('请逐个登记文件夹内的文件')
        kind = 'local'; locator = selected['path']; title = title or selected['name']
    else:
        kind = 'local' if Path(locator).is_absolute() else 'feishu'
    if not isinstance(locator, str) or not locator.strip():
        raise ValueError('资料位置为空')
    locator = locator.strip(); grant = {}
    if kind == 'local':
        locator, grant = _local_locator(locator, project_id)
    with STORE:
        data = _load()
        previous = next((r for r in data['sources'] if r['project_id'] == project_id and r['kind'] == kind
                         and (os.path.normcase(r['locator']) if kind == 'local' else r['locator']) == (os.path.normcase(locator) if kind == 'local' else locator)), None)
        if previous:
            return dict(previous)
        ident = _id(source_id) if source_id else uuid.uuid4().hex
        if any(r['id'] == ident for r in data['sources']):
            raise ValueError('来源编号已登记')
        row = dict(id=ident, doc_id=doc_id or 'source:' + ident, title=str(title or '未命名资料')[:200], kind=kind,
                   locator=locator, project_id=project_id, revision=uuid.uuid4().hex, observed_version=None,
                   checked_at=None, state='unknown', error='', operation=None, cancelled_operations=[], **grant)
        data['sources'].append(row); _save(data)
        return dict(row)


def _update(source_id, project_id, action):
    with STORE:
        data = _load()
        row = next((r for r in data['sources'] if r['id'] == _id(source_id) and r['project_id'] == project_id), None)
        if row is None:
            raise ValueError('资料不在当前范围')
        action(row); _save(data)
        return dict(row)


def authorize(row):
    if row['kind'] == 'local':
        try:
            locator, grant = _local_locator(row['locator'], row['project_id'])
        except ValueError as exc:
            if row['project_id']:
                raise PermissionError('项目目录授权已变化，请重新定位资料以核对授权') from exc
            raise
        if row['project_id'] and grant.get('workspace_revision') != row.get('workspace_revision'):
            raise PermissionError('项目目录授权已变化，请重新定位资料以核对授权')
        if locator != row['locator']:
            raise PermissionError('资料位置已变化')
    else:
        scope(row['project_id'])


def _body(row):
    authorize(row)
    if row['kind'] == 'local':
        from .local_sources import extract
        if not Path(row['locator']).is_file():
            raise FileNotFoundError('来源文件已缺失，请重新定位')
        return extract(Path(row['locator']))
    from .feishu_docs import fetch_docx_markdown
    return fetch_docx_markdown(row['locator'], row['title'])


def _file_stamp(row):
    if row['kind'] != 'local':
        return None
    stat = Path(row['locator']).stat()
    return dict(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def begin(source_id, project_id, operation_id, kind):
    _id(operation_id)
    def change(row):
        if (row.get('operation') or {}).get('id') == operation_id:
            raise ValueError('此操作号已有状态，请查询原操作，不要直接重放')
        if operation_id in row['cancelled_operations']:
            raise InterruptedError('本次资料操作已取消')
        if row.get('operation') and row['operation']['state'] == 'running':
            raise ValueError('此来源已有操作，请核对或取消原操作')
        row['operation'] = dict(id=operation_id, kind=kind, state='running', stage='reading', started=time.time())
    return _update(source_id, project_id, change)


def cancel(source_id, project_id, operation_id):
    _id(operation_id)
    def change(row):
        row['cancelled_operations'] = [*row['cancelled_operations'][-63:], operation_id]
        if (row.get('operation') or {}).get('id') == operation_id:
            row['operation'].update(state='cancelled', stage='cancelled')
    return _update(source_id, project_id, change)


def _active(row, operation_id):
    operation = row.get('operation') or {}
    if operation_id in row['cancelled_operations'] or operation.get('id') != operation_id or operation.get('state') != 'running':
        raise InterruptedError('资料操作已取消或已被替代')


def _version_body(path, source_id, version):
    data = json.loads(path.read_text('utf-8')); text = data.get('text')
    if data.get('source_id') != source_id or data.get('version') != version or not isinstance(text, str) or hashlib.sha256(text.encode('utf-8')).hexdigest() != version:
        raise ValueError('资料快照来源或摘要不一致')
    return text


def observe(source_id, project_id='', operation_id=''):
    before = get(source_id, project_id)
    try:
        authorize(before)
        stamp = _file_stamp(before)
        body = _body(before)
        if _file_stamp(before) != stamp:
            raise ValueError('读取期间来源文件已变化，请重新核对')
        if not isinstance(body, str) or not body.strip():
            raise ValueError('来源没有正文')
        from .local_sources import MAX_CHARS
        if len(body) > MAX_CHARS:
            raise ValueError('资料正文超过读取上限')
        version = hashlib.sha256(body.encode('utf-8')).hexdigest()
        def change(row):
            if row['revision'] != before['revision']:
                raise ValueError('读取期间来源位置已变化，未覆盖')
            if operation_id:
                _active(row, operation_id)
            version_path = root() / 'versions' / source_id / (version + '.json')
            if not version_path.exists():
                atomic_write(version_path, dict(source_id=source_id, version=version, text=body, observed_at=time.time()))
            else:
                _version_body(version_path, source_id, version)
            row.update(observed_version=version, file_stamp=stamp, checked_at=time.time(), state='available', error='')
            if row.get('operation'):
                row['operation']['stage'] = 'observed'
        row = _update(source_id, project_id, change)
        return row, body
    except Exception as exc:
        if not isinstance(exc, InterruptedError):
            def failed(row):
                if row['revision'] == before['revision']:
                    row.update(state='missing' if isinstance(exc, FileNotFoundError) else 'revoked' if isinstance(exc, PermissionError) else 'error',
                               checked_at=time.time(), error=str(exc))
            _update(source_id, project_id, failed)
        raise


def finish(source_id, project_id, operation_id, *, error=''):
    def change(row):
        if (row.get('operation') or {}).get('id') == operation_id and operation_id not in row['cancelled_operations']:
            row['operation'].update(state='failed' if error else 'completed', stage='finished', error=error, ended=time.time())
    return _update(source_id, project_id, change)


@contextmanager
def commit_guard(source_id, project_id, revision, version, operation_id):
    """Lock order is source catalog then index; no locks during extraction/embedding."""
    with STORE:
        row = get(source_id, project_id); _active(row, operation_id); authorize(row)
        if row['revision'] != revision or row['observed_version'] != version or row['state'] != 'available':
            raise ValueError('计算期间来源已变化，保留原索引')
        if _file_stamp(row) != row.get('file_stamp'):
            raise ValueError('计算期间原文件已变化，保留原索引，请重新核对')
        yield


def rebind(source_id, project_id, snapshot_id, expected_revision):
    from .local_sources import get as attachment
    selected = attachment(snapshot_id)
    if selected['kind'] != 'file':
        raise ValueError('请重新选择一个文件')
    locator, grant = _local_locator(selected['path'], project_id)
    def change(row):
        if row['kind'] != 'local' or row['revision'] != expected_revision:
            raise ValueError('来源已变化，请重新读取后定位')
        row.update(locator=locator, title=selected['name'], revision=uuid.uuid4().hex, state='unknown',
                   checked_at=None, observed_version=None, error='', operation=None, **grant)
    return _update(source_id, project_id, change)


def read_version(source_id, version, project_id='', offset=0, limit=12000):
    row = get(source_id, project_id); authorize(row)
    if not isinstance(version, str) or not re.fullmatch('[a-f0-9]{64}', version):
        raise ValueError('资料版本未知，请核对来源')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 24000:
        raise ValueError('资料读取区间无效')
    path = root() / 'versions' / source_id / (version + '.json')
    if not path.is_file():
        raise ValueError('此版本快照不存在')
    text = _version_body(path, source_id, version)
    end = min(len(text), offset + limit)
    return dict(source_id=source_id, version=version, text=text[offset:end], offset=offset,
                next_offset=end if end < len(text) else None, chars=len(text), complete=offset == 0 and end == len(text))


def eligible(doc, project_id=''):
    if doc.get('project_id', '') != project_id:
        return False
    if not doc.get('source_id'):
        registered = next((r for r in listing(project_id) if r['doc_id'] == doc['id'] or same_location(r, doc)), None)
        if registered is None:
            return True  # Legacy global indexes remain available, explicitly unverified.
        return availability(registered) not in BAD and registered.get('observed_version') is None
    try:
        row = get(doc['source_id'], project_id)
        return availability(row) not in BAD and row.get('observed_version') == doc.get('source_version')
    except (ValueError, PermissionError, OSError):
        return False


def availability(row):
    """Cheap metadata/known-grant invalidation; content refresh remains explicit."""
    try:
        authorize(row)
        if row['kind'] == 'local':
            stamp = _file_stamp(row)
            if row.get('file_stamp') and stamp != row['file_stamp']:
                return 'stale'
        return row['state']
    except FileNotFoundError:
        return 'missing'
    except PermissionError:
        return 'revoked'
    except (ValueError, OSError):
        return 'error'
