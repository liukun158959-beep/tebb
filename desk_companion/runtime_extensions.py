"""Optional dependencies live outside the replaceable packaged runtime."""
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import site
import subprocess
import sys
import uuid

from .paths import data_root


def packaged_root():
    return Path(sys.executable).resolve().parent.parent


def filesystem_path(path):
    """Use Windows' native extended path namespace without registry changes."""
    value=str(Path(path).resolve())
    if os.name!='nt'or value.startswith('\\\\?\\'):return value
    if value.startswith('\\\\'):return '\\\\?\\UNC\\'+value[2:]
    return '\\\\?\\'+value


def compatibility(package=None):
    package=Path(package or packaged_root())
    lock=package/'runtime-packages.txt'
    if not lock.is_file():raise RuntimeError('缺少随包依赖清单，未加载或安装用户扩展。')
    abi=f'cp{sys.version_info.major}{sys.version_info.minor}-win_amd64\n'.encode()
    return hashlib.sha256(abi+lock.read_bytes()).hexdigest()[:16]


def layer_root(package=None,root=None):
    return Path(root or data_root())/'extensions'/compatibility(package)


def active_path(package=None,root=None):
    layer=layer_root(package,root);pointer=layer/'current.json'
    if not pointer.is_file():return None
    value=json.loads(pointer.read_text('utf-8'))
    ident=value.get('generation','')
    if value.get('version')!=1 or not re.fullmatch('[a-f0-9]{16}',ident):raise ValueError('扩展版本指针无效。')
    path=layer/ident/'p'
    if not path.is_dir()or path.is_symlink()or getattr(path,'is_junction',lambda:False)():raise ValueError('扩展目录不可用。')
    receipt=json.loads((path.parent/'result.json').read_text('utf-8'))
    if receipt.get('state')!='ready'or receipt.get('compatibility')!=layer.name:raise ValueError('扩展尚未完成验证。')
    return path


def activate():
    # The bundled interpreter imports this through sitecustomize. Ordinary
    # source interpreters keep their environment and never get this hook.
    if os.environ.get('TEBB_DISABLE_USER_EXTENSIONS')=='1'or not os.environ.get('DESK_COMPANION_DATA_DIR'):return
    try:path=active_path()
    except (OSError,ValueError,RuntimeError):return
    if path:
        entry=filesystem_path(path);site.addsitedir(entry)
        sys.path.remove(entry);sys.path.insert(0,entry)


def install(*,package=None,root=None,run=subprocess.run):
    """Publish a new generation only after both installs and import validation."""
    package=Path(package or packaged_root());root=Path(root or data_root())
    from .resource_lock import ResourceLock
    from .runtime_instance import RUNTIME_LOCK_NAME
    with ResourceLock(RUNTIME_LOCK_NAME,directory=root/'memory/locks').hold(blocking=False):
        layer=layer_root(package,root);layer.mkdir(parents=True,exist_ok=True)
        generation=layer/uuid.uuid4().hex[:16];target=generation/'p';target.mkdir(parents=True)
        receipt=dict(version=1,compatibility=layer.name,generation=generation.name,state='installing')
        def save():
            (generation/'result.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
        save()
        try:
            temporary=generation/'t';temporary.mkdir()
            environment={**os.environ,'TEBB_DISABLE_USER_EXTENSIONS':'1','TEMP':filesystem_path(temporary),'TMP':filesystem_path(temporary),'TMPDIR':filesystem_path(temporary)}
            wheels=generation/'wheels';wheels.mkdir()
            # The CPU index is authoritative for torch wheels, not a complete
            # mirror of the frozen base dependencies (e.g. setuptools).
            run([str(package/'runtime/python.exe'),'-m','pip','download','--disable-pip-version-check','--no-deps','--only-binary=:all:',
                '--index-url','https://download.pytorch.org/whl/cpu','--dest',str(wheels),'torch'],check=True,timeout=1200,env=environment)
            candidates=list(wheels.glob('torch-*.whl'))
            if len(candidates)!=1 or '+cpu-'not in candidates[0].name:raise RuntimeError('未取得唯一CPU版torch wheel，原扩展保持不变。')
            common=[str(package/'runtime/python.exe'),'-m','pip','install','--disable-pip-version-check','--no-build-isolation','--no-compile','--upgrade','--target',filesystem_path(target),'-c',str(package/'runtime-packages.txt')]
            run([*common,str(candidates[0])],check=True,timeout=1200,env=environment)
            torch=next((d.version for d in metadata.distributions(path=[str(target)])if d.metadata.get('Name','').lower()=='torch'),None)
            if not torch or '+cpu'not in torch:raise RuntimeError('未取得CPU版torch的版本回执，原扩展保持不变。')
            constraints=generation/'torch-constraint.txt';constraints.write_text('torch=='+torch+'\n',encoding='utf-8')
            run([*common,'-c',str(constraints),'--find-links',str(wheels),'sentence-transformers'],check=True,timeout=1200,env=environment)
            code="import sys;sys.path.insert(0,sys.argv[1]);import torch,sentence_transformers;assert torch.version.cuda is None;print('extensions imports OK')"
            run([str(package/'runtime/python.exe'),'-c',code,filesystem_path(target)],check=True,timeout=120,env=environment)
            receipt['state']='ready';save()
            pending=layer/('current-'+uuid.uuid4().hex+'.tmp')
            pending.write_text(json.dumps(dict(version=1,generation=generation.name)),encoding='utf-8');os.replace(pending,layer/'current.json')
            return receipt
        except BaseException as exc:
            receipt.update(state='failed',error_kind=type(exc).__name__);save()
            raise


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['status','install']);args=parser.parse_args()
    if args.action=='install':value=install()
    else:
        path=active_path();value=dict(version=1,compatibility=compatibility(),active=bool(path),path=str(path)if path else None)
    print(json.dumps(value,ensure_ascii=False))


if __name__=='__main__':main()
