"""Audit a runtime directory or ZIP without reading user configuration contents."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

PRIVATE_NAMES = {'.env', '.npmrc', '.pypirc', '.netrc', '.git-credentials', 'models.json', 'mcp.json', 'maa.json', 'onboarding.json',
                 'user_state.json', 'feishu_agent.json', 'feishu-connection.json',
                 'model-network.json', 'model-capacity.json', 'search-health.json',
                 'automation_jobs.json', 'news_settings.json', 'arknights_account.json',
                 'raise_roster.json', 'today.json', 'facts.json', 'notebook.json',
                 'wiki_connection.json', 'wiki_catalog_sources.json', 'pet-preferences.json'}


def audit_names(names):
    rejected=[]
    for name in names:
        path=PurePosixPath(name.replace('\\','/'))
        lower_name=path.name.lower()
        if (path.is_absolute() or '..' in path.parts or any(':' in part for part in path.parts) or lower_name in PRIVATE_NAMES
                or lower_name.startswith(('user_state.', '.env.'))
                or path.suffix.lower() in {'.sqlite', '.sqlite3', '.db', '.jsonl', '.log', '.ready', '.moc3'}
                or lower_name.endswith('.model3.json') or lower_name.startswith('live2dcubismcore')
                or 'Core' in path.parts or {part.lower() for part in path.parts} & {'skins','.git'}):
            rejected.append(name)
    if rejected:
        raise ValueError('发布资产包含私有配置、用户数据、日志或形象素材：'+'；'.join(rejected[:20]))
    return len(names)


def audit_licenses(names, read):
    required={'LICENSE','THIRD_PARTY_NOTICES.md','licenses/SOURCES.json','licenses/dependencies.json',
              'runtime/LICENSE.txt','runtime/Lib/site-packages/atlas/licenses/Atlas-MIT.txt',
              'runtime/Lib/site-packages/atlas/licenses/HumanEval-MIT.txt',
              'runtime/Lib/site-packages/atlas/licenses/THIRD_PARTY_NOTICES.md'}
    missing=required-set(names)
    if missing:raise ValueError('发布包缺少许可材料：'+', '.join(sorted(missing)))
    rows=json.loads(read('licenses/dependencies.json'))
    if not rows or not {'frontend','rust','python'}<={row['ecosystem'] for row in rows}:
        raise ValueError('发布包依赖许可清单不完整')
    for row in rows:
        if not row['files']:raise ValueError('依赖许可清单缺少原文：'+row.get('name','unknown'))
        for file in row['files']:
            if file not in names:raise ValueError('依赖声明引用了缺失的许可文件：'+file)
    for item in json.loads(read('licenses/SOURCES.json')):
        file='licenses/'+item['path']
        if file not in names or hashlib.sha256(read(file)).hexdigest()!=item['sha256']:
            raise ValueError('发布包补充许可校验失败：'+file)


def audit(path, *, require_licenses=False):
    if path.is_dir():
        names=[file.relative_to(path).as_posix() for file in path.rglob('*') if file.is_file() and '__pycache__' not in file.parts and file.suffix!='.pyc']
        count=audit_names(names)
        if require_licenses:audit_licenses(names,lambda name:(path/name).read_bytes())
        return dict(files=count)
    with zipfile.ZipFile(path) as archive:
        count=audit_names(archive.namelist())
        bad=archive.testzip()
        if bad:raise ValueError('ZIP CRC 校验失败：'+bad)
        if require_licenses:
            roots={name.split('/')[0] for name in archive.namelist()}
            if len(roots)!=1:raise ValueError('许可审计需要单一发布根目录')
            prefix=next(iter(roots))+'/'
            names={name.removeprefix(prefix) for name in archive.namelist()}
            audit_licenses(names,lambda name:archive.read(prefix+name))
    return dict(files=count,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),size=path.stat().st_size)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path',type=Path)
    parser.add_argument('--require-licenses',action='store_true')
    args=parser.parse_args()
    print(json.dumps(audit(args.path,require_licenses=args.require_licenses),ensure_ascii=False))
