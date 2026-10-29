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


def audit(path):
    if path.is_dir():
        names=[file.relative_to(path).as_posix() for file in path.rglob('*') if file.is_file() and '__pycache__' not in file.parts and file.suffix!='.pyc']
        return dict(files=audit_names(names))
    with zipfile.ZipFile(path) as archive:
        count=audit_names(archive.namelist())
        bad=archive.testzip()
        if bad:raise ValueError('ZIP CRC 校验失败：'+bad)
    return dict(files=count,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),size=path.stat().st_size)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path',type=Path)
    args=parser.parse_args()
    print(json.dumps(audit(args.path),ensure_ascii=False))
