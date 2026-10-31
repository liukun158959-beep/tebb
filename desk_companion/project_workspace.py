"""Explicit local project grants, optimistic file writes and sandboxed verification."""
from __future__ import annotations
import base64
from contextlib import contextmanager, closing
import difflib
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
from pathlib import Path, PurePosixPath
import threading
import time
import uuid

from .paths import data_root
from .resource_lock import ResourceLock
from .video import atomic_write

CONFIG = ResourceLock('project-config')
LOCKS = {}
LOCKS_GUARD = threading.Lock()
MAX_FILE = 1024 * 1024
SKIP = {'.git', '.svn', 'node_modules', '.venv', 'venv', '__pycache__', 'target', 'dist'}
IMAGES = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


def selected_project():
    path = data_root() / 'memory' / 'collaboration' / 'workspace.sqlite3'
    if not path.exists(): return None
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        state = json.loads(db.execute('SELECT data FROM workspace WHERE id=1').fetchone()[0])
    return next((c.get('project_id','') for c in state['conversations'] if c['id']==state['selected']), '')


def config_path(project_id=None):
    if project_id:
        if not isinstance(project_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', project_id): raise ValueError('项目编号无效')
        return data_root() / 'memory' / 'project-workspaces' / (project_id+'.json')
    return data_root() / 'memory' / 'project-workspace.json'


def load(project_id=None):
    with CONFIG:
        pid = selected_project() if project_id is None else project_id
        if pid is not None and not isinstance(pid,str): raise ValueError('项目编号无效')
        empty = dict(revision='', primary='', references=[], edit=False, commands=False)
        if pid is not None: empty['project_id'] = pid
        # An independent chat has no folder grant. Keep the old global file for migration only.
        if pid == '': return empty
        path = config_path(pid)
        return json.loads(path.read_text('utf-8')) if path.exists() else empty


def clean_root(value):
    from .local_sources import _linked
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError('工作目录需要本机绝对路径')
    path = Path(value)
    if not path.is_dir(): raise ValueError('工作目录不存在或不是文件夹')
    for part in [path, *path.parents]:
        if _linked(part): raise ValueError('工作目录不能经过符号链接或目录联接')
    path = path.resolve(strict=True)
    if not path.is_dir() or path == Path(path.anchor): raise ValueError('请选择具体项目文件夹')
    return path


@contextmanager
def lease(primary):
    key = hashlib.sha256(str(Path(primary).resolve()).casefold().encode()).hexdigest()
    with LOCKS_GUARD:
        lock = LOCKS.setdefault(key, threading.Lock())
    if not lock.acquire(False): raise ValueError('工作目录正在被任务修改，请等待任务完成或停止后再编辑')
    file = None
    try:
        folder = data_root() / 'memory' / 'project-locks'; folder.mkdir(parents=True, exist_ok=True)
        file = (folder / (key + '.lock')).open('a+b')
        file.seek(0)
        if not file.read(1): file.write(b'0'); file.flush()
        file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('工作目录已有编码任务，请等待完成或停止后重试') from None
        jobs = data_root() / 'memory' / 'terminal' / 'jobs'
        for metadata in jobs.glob('*/meta.json') if jobs.exists() else []:
            try:
                job = json.loads(metadata.read_text('utf-8'))
                heartbeat = metadata.with_name('lease')
                if job.get('session') == 'project:'+str(primary) and job.get('state') in {'starting','running','stopping'} and heartbeat.exists() and time.time()-heartbeat.stat().st_mtime < 12:
                    raise ValueError('主目录命令尚在结束中，请稍后再编辑')
            except (OSError, json.JSONDecodeError): pass
        yield
    finally:
        if file: file.close()
        lock.release()


def save(primary, references=None, edit=False, commands=False, project_id=None):
    if type(edit) is not bool or type(commands) is not bool: raise ValueError('权限参数无效')
    refs = references or []
    if not isinstance(refs, list) or len(refs) > 4: raise ValueError('最多4个只读参考目录')
    main = clean_root(primary) if primary else None
    paths = [clean_root(p) for p in refs]
    all_paths = ([main] if main else []) + paths
    for i, a in enumerate(all_paths):
        if any(a.is_relative_to(b) or b.is_relative_to(a) for b in all_paths[:i]):
            raise ValueError('主目录与参考目录不能重叠')
    if not main and (edit or commands): raise ValueError('请先指定主目录')
    pid = selected_project() if project_id is None else project_id
    if pid == '': raise ValueError('请先打开文件夹创建项目，再配置目录与权限')
    old = load(pid)
    with lease(old['primary'] or str(config_path().parent)), CONFIG:
        value = dict(revision=uuid.uuid4().hex, primary=str(main) if main else '', references=[str(p) for p in paths], edit=edit, commands=commands)
        if pid is not None: value['project_id'] = pid
        atomic_write(config_path(pid), value)
    return dict(ok=True, workspace=value)


def resolve(root_id='main', relative='', config=None):
    from .terminal import inside
    cfg = config or load()
    roots = {'main': cfg['primary'], **{f'ref-{i}': p for i, p in enumerate(cfg['references'])}}
    if root_id not in roots or not roots[root_id]: raise ValueError('工作目录尚未配置')
    if not isinstance(relative, str) or len(relative) > 1000: raise ValueError('文件路径无效')
    rel = PurePosixPath(relative.replace('\\', '/'))
    if rel.is_absolute() or '..' in rel.parts or any(':' in p or p in SKIP or p.startswith('.env') for p in rel.parts):
        raise ValueError('路径超出工作区或属于受保护目录')
    base = clean_root(roots[root_id])
    path = inside(base.joinpath(*rel.parts), base)
    if path.exists() and path.is_file() and path.stat().st_nlink > 1:
        raise ValueError('不支持硬链接文件')
    return path, cfg


def tree(root_id='main', relative='', config=None):
    path, cfg = resolve(root_id, relative, config)
    if not path.is_dir(): raise ValueError('目录不存在')
    rows = []
    from itertools import islice
    candidates = list(islice(path.iterdir(), 2000))
    for child in sorted(candidates, key=lambda p: (not p.is_dir(), p.name.casefold())):
        if child.name in SKIP or child.name.startswith('.env'): continue
        rel = (PurePosixPath(relative) / child.name).as_posix()
        try:
            safe, _ = resolve(root_id, rel, cfg)
            rows.append(dict(name=child.name, path=rel, directory=safe.is_dir(), size=0 if safe.is_dir() else safe.stat().st_size))
        except (ValueError, OSError): continue
        if len(rows) >= 400: break
    return dict(ok=True, entries=rows, limited=len(rows) >= 400 or len(candidates) >= 2000)


def read(root_id, relative, config=None):
    path, cfg = resolve(root_id, relative, config)
    if not path.is_file(): raise ValueError('文件不存在')
    if path.stat().st_size > MAX_FILE: raise ValueError('预览上限1 MiB，请使用附件提取或本地应用打开')
    raw = path.read_bytes()
    if len(raw)>MAX_FILE: raise ValueError('文件超过预览大小限制')
    revision = hashlib.sha256(raw).hexdigest()
    result = dict(ok=True, path=relative, root=root_id, revision=revision, editable=root_id == 'main' and cfg['edit'], name=path.name, workspace_revision=cfg["revision"])
    if path.suffix.lower() in IMAGES:
        result.update(kind='image', data='data:'+mimetypes.guess_type(path.name)[0]+';base64,'+base64.b64encode(raw).decode(), editable=False)
    else:
        try: text = raw.decode('utf-8')
        except UnicodeError:
            from .local_sources import extract
            text = extract(path)
            result['editable'] = False
        if '\0' in text: raise ValueError('此二进制格式不支持预览')
        result.update(kind='text', text=text)
    return result


def write(relative, text, revision, *, config=None, locked=False, workspace_revision=None):
    cfg = load(config.get('project_id')) if config else load()
    if workspace_revision is not None and cfg["revision"] != workspace_revision: raise ValueError("工作目录已切换，请重新打开文件")
    if config and cfg['revision'] != config['revision']: raise ValueError('工作目录授权已变化，请重新提交任务')
    if not cfg['edit']: raise ValueError('工作目录尚未授权文件编辑')
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_FILE: raise ValueError('编辑内容超过1 MiB')
    path, _ = resolve('main', relative, cfg)
    if path.exists() and not path.is_file(): raise ValueError('目标不是文件')
    current = path.read_bytes() if path.exists() else None
    digest = hashlib.sha256(current).hexdigest() if current is not None else 'new'
    if revision != digest: raise ValueError('文件已被其他操作修改，请重新读取并合并，未覆盖原文件')
    old = current.decode('utf-8') if current is not None else ''
    diff = ''.join(difflib.unified_diff(old.splitlines(True), text.splitlines(True), fromfile=relative+' (before)', tofile=relative+' (after)'))
    def commit():
        path.parent.mkdir(parents=True, exist_ok=True)
        # Recheck after acquiring the write lease.
        latest = path.read_bytes() if path.exists() else None
        if latest != current: raise ValueError('文件已变化，请重新读取')
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_bytes(text.encode('utf-8')); temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    if locked: commit()
    else:
        with lease(cfg['primary']): commit()
    return dict(ok=True, path=relative, revision=hashlib.sha256(text.encode()).hexdigest(), diff=diff[:40000], diff_truncated=len(diff)>40000)


def current(snapshot):
    cfg = load(snapshot.get('project_id')) if snapshot else load()
    if not snapshot or cfg['revision'] != snapshot.get('revision'): raise ValueError('工作目录授权已变化，请重新提交任务')
    return cfg


def source_snapshot(config):
    records, total, limited, scanned = {}, 0, False, 0
    main = Path(config['primary'])
    for parent, dirs, files in os.walk(main, followlinks=False):
        safe_dirs = []
        for directory in dirs:
            try:
                resolve('main', (Path(parent)/directory).relative_to(main).as_posix(), config)
                safe_dirs.append(directory)
            except (ValueError, OSError): pass
        dirs[:] = safe_dirs
        scanned += len(dirs) + len(files)
        if scanned>10000: limited=True; break
        for name in sorted(files):
            if name.startswith('.env'): continue
            rel = (Path(parent)/name).relative_to(main).as_posix()
            try:
                path, _ = resolve('main', rel, config)
                if path.stat().st_size>MAX_FILE: continue
                raw = path.read_bytes()
                text = raw.decode('utf-8')
                if '\0' in text: continue
            except (OSError, UnicodeError, ValueError): continue
            total += len(raw)
            if total>16*MAX_FILE or len(records)>=2000: limited=True; break
            records[rel] = text
        if limited: break
    return records, limited


def run(command, snapshot, run_id, timeout=90):
    """Run in the existing network-isolated WSL/bubblewrap supervisor."""
    from .terminal import manager
    cfg = current(snapshot)
    if not cfg['commands']: raise ValueError('工作目录尚未授权本地命令')
    before, limited = source_snapshot(cfg)
    service = manager()
    if not isinstance(command,str) or not command.strip(): raise ValueError('验证命令不能为空')
    strict_command = 'set -e\nset -o pipefail\n'+command
    result = service.start(strict_command, session='project:'+cfg['primary'], timeout=min(120, max(1, int(timeout))), owner='agent', task_id=run_id, request_id=uuid.uuid4().hex, project=cfg)
    job = result['job']; ident = job['id']
    try:
        while job['state'] in {'starting','running','stopping'}:
            time.sleep(.2); job = service.get(ident)
        output, offset = bytearray(), 0
        while offset < job['output_bytes']:
            chunk = service.read(ident, offset); output.extend(base64.b64decode(chunk['data'])); offset = chunk['offset']
        after, after_limited = source_snapshot(cfg)
        changes = []
        for path in sorted(set(before) | set(after)):
            if before.get(path) == after.get(path): continue
            old, new = before.get(path, ''), after.get(path, '')
            diff = ''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile=path+' (before)',tofile=path+' (after)'))
            changes.append(dict(path=path,diff=diff[:40000],diff_truncated=len(diff)>40000))
            if len(changes)>=50: limited=True; break
        return dict(changes=changes, diff_limited=limited or after_limited, command=command, state=job['state'], exit_code=job['exit_code'], output=output.decode('utf-8','replace')[-20000:], truncated=len(output)>20000, environment=job.get('distro'), error=job.get('error',''))
    finally:
        if job['state'] in {'starting','running','stopping'}: service.cancel(ident)


def tool_specs():
    def spec(name, description, properties, required):
        return dict(type='function', function=dict(name=name, description=description, parameters=dict(type='object', properties=properties, required=required)),read_only=name in {'workspace_list','workspace_read'})
    string = {'type':'string'}
    return [
        spec('workspace_list', '列出授权工作目录，参考目录只读。root为main或ref-0等。', {'root':string,'path':string}, []),
        spec('workspace_read', '读取工作区文本，返回revision；创建新文件时revision用new。', {'root':string,'path':string}, ['path']),
        spec('workspace_write', '编辑主目录UTF-8文件，必须带上读取时的revision；新文件用new。', {'path':string,'text':string,'revision':string}, ['path','text','revision']),
        spec('workspace_run', '在主目录WSL无网络沙箱运行命令。开启失败立即退出和pipefail，直接执行构建或测试，不用echo或||true掩盖退出码。/references/N只读。', {'command':string,'timeout':{'type':'integer'}}, ['command']),
    ]
