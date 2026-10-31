"""Explicit installer data cleanup; no RPC and no arbitrary data-directory flag."""
import ctypes
import json
import os
from pathlib import Path
import shutil
import uuid

from .resource_lock import ResourceLock
from .runtime_instance import RUNTIME_LOCK_NAME

OWNED_FOLDERS=('DeskCompanion','cn.deskcompanion.client')


def local_appdata():
    if os.name!='nt':raise RuntimeError('该入口只用于Windows安装生命周期。')
    buffer=ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None,0x1c,None,0,buffer):raise OSError('无法确认Windows当前用户目录。')
    return Path(buffer.value)


def validate_tree(path,base):
    path=Path(path);base=Path(base).resolve()
    if not path.is_absolute()or path.parent.resolve()!=base or path.resolve().parent!=base:
        raise ValueError('应用数据路径越界，未清除。')
    pending=[path]
    while pending:
        node=pending.pop()
        if node.is_symlink()or getattr(node,'is_junction',lambda:False)():raise ValueError('数据中存在重解析点，未清除。')
        if node.exists()and getattr(node.stat(follow_symlinks=False),'st_file_attributes',0)&0x400:
            raise ValueError('数据中存在重解析点，未清除。')
        if node.is_dir():pending.extend(node.iterdir())
    if path.exists()and not path.is_dir():raise ValueError('应用数据根不是目录，未清除。')
    return path


def clear_data(base):
    """Trusted adapter supplies the OS folder; tests supply their owned fixture.

    Validate every tree before moving anything. Quarantine both roots before
    deleting either so a busy browser profile cannot cause an early data loss.
    Never follow junctions, consume DESK_COMPANION_DATA_DIR, or touch projects.
    """
    base=Path(base).resolve()
    if not base.is_absolute()or base==Path(base.anchor):raise ValueError('当前用户目录无效。')
    targets=[validate_tree(base/name,base)for name in OWNED_FOLDERS]
    existing=[p for p in targets if p.exists()]
    main=targets[0]
    if main.exists():
        with ResourceLock(RUNTIME_LOCK_NAME,directory=main/'memory/locks').hold(blocking=False):pass
    result=dict(version=1,ok=False,roots=[dict(name=p.name,state='missing'if not p.exists()else'preserved')for p in targets])
    states={r['name']:r for r in result['roots']};moved=[]
    try:
        for target in existing:
            quarantine=base/('.'+target.name+'.uninstall-'+uuid.uuid4().hex)
            if quarantine.exists():raise FileExistsError('隔离目录已存在。')
            validate_tree(target,base);validate_tree(quarantine,base)
            target.rename(quarantine);moved.append((target,quarantine))
            states[target.name].update(state='quarantined',quarantine=str(quarantine))
    except (OSError,ValueError)as exc:
        for target,quarantine in reversed(moved):
            try:
                if target.exists():raise FileExistsError('原目录已重新创建，未覆盖。')
                quarantine.rename(target);states[target.name].update(state='preserved',quarantine=None)
            except OSError:pass
        result['error_kind']=type(exc).__name__;return result
    for target,quarantine in moved:
        try:
            validate_tree(quarantine,base)
            shutil.rmtree(quarantine)
            states[target.name].update(state='removed',quarantine=None)
        except (OSError,ValueError)as exc:
            result['error_kind']=type(exc).__name__;return result
    result['ok']=True;return result


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['check','clear-default-data'])
    parser.add_argument('--confirm-default-data',action='store_true')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args();base=local_appdata()
    # Reserve the report before an irreversible action. A collision or unwritable
    # destination must fail while every data root is still intact.
    report=args.report.open('x',encoding='utf-8')if args.report else None
    try:
        if args.action=='clear-default-data':
            if not args.confirm_default_data:raise ValueError('需要明确选择清除默认应用数据。')
            value=clear_data(base)
        else:
            root=base/OWNED_FOLDERS[0]
            if root.exists():
                with ResourceLock(RUNTIME_LOCK_NAME,directory=root/'memory/locks').hold(blocking=False):pass
            value=dict(version=1,ok=True,runtime='stopped',owned_folders=list(OWNED_FOLDERS))
    except (OSError,ValueError,BlockingIOError)as exc:value=dict(version=1,ok=False,error_kind=type(exc).__name__)
    encoded=json.dumps(value,ensure_ascii=False,indent=2)
    if report:
        try:report.write(encoded)
        finally:report.close()
    print(encoded);raise SystemExit(0 if value['ok']else 73)


if __name__=='__main__':main()
