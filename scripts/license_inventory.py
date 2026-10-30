"""Retain actual locked dependency notices; never label the whole bundle MIT."""
import hashlib
import json
import re
import shutil
import subprocess
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ('license', 'licence', 'copying', 'copyright', 'notice')


def license_files(root):
    result = []
    for path in root.iterdir():
        if path.name.lower().startswith(PREFIXES):
            result.extend([path] if path.is_file() else [p for p in path.rglob('*') if p.is_file()])
    if not result:
        result=[p for p in root.glob('*/*') if p.is_file() and p.name.lower().startswith(PREFIXES)]
    return sorted(result)


def retain(root, destination):
    files = license_files(root)
    for source in files:
        target = destination / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return [p.relative_to(root).as_posix() for p in files]


def collect_package(ecosystem, name, version, source, output, license_id, repository=''):
    if not re.fullmatch(r'[@a-zA-Z0-9_.+/-]+', name) or Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('Unsafe dependency identity')
    if not re.fullmatch(r'[a-zA-Z0-9_.+-]+', version):
        raise ValueError('Unsafe dependency version')
    destination = output/ecosystem/name/version
    files = retain(source, destination)
    if not files:
        supplement = ROOT/'licenses/supplemental'/ecosystem/name/version
        if supplement.is_dir():
            files = retain(supplement, destination)
    if not files:
        raise ValueError(f'Missing {ecosystem} license text: {name}@{version}; review exact upstream version before release')
    return dict(ecosystem=ecosystem, name=name, version=version, license=license_id or 'NOT_DECLARED',
                repository=repository, files=[p.relative_to(output.parent).as_posix() for p in (destination/f for f in files)])


def verify_sources():
    for item in json.loads((ROOT/'licenses/SOURCES.json').read_text('utf-8')):
        path=(ROOT/'licenses'/item['path']).resolve()
        if not path.is_relative_to((ROOT/'licenses').resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('Supplemental license source hash mismatch: '+item['path'])


def generate(package):
    verify_sources()
    output=package/'licenses'
    shutil.copytree(ROOT/'licenses', output, dirs_exist_ok=True)
    shutil.copy2(ROOT/'LICENSE', package/'LICENSE')
    shutil.copy2(ROOT/'THIRD_PARTY_NOTICES.md', package/'THIRD_PARTY_NOTICES.md')
    records=[]
    executable=shutil.which('pnpm.cmd') or shutil.which('pnpm')
    if not executable:raise RuntimeError('pnpm is required to retain the locked frontend notices')
    frontend=json.loads(subprocess.check_output([executable,'licenses','list','--prod','--json'],cwd=ROOT/'client'))
    seen=set()
    for rows in frontend.values():
        for row in rows:
            for directory in row['paths']:
                source=Path(directory);info=json.loads((source/'package.json').read_text('utf-8'))
                key=(info['name'],info['version'])
                if key in seen:continue
                seen.add(key)
                records.append(collect_package('frontend',*key,source,output,info.get('license'),row.get('homepage','')))
    cargo=json.loads(subprocess.check_output(['cargo','metadata','--locked','--format-version','1',
                                            '--filter-platform','x86_64-pc-windows-msvc'],cwd=ROOT/'client/src-tauri'))
    for row in cargo['packages']:
        if row['name']=='desk-companion-client':continue
        source=Path(row['manifest_path']).parent
        vcs=source/'.cargo_vcs_info.json'
        revision=json.loads(vcs.read_text('utf-8')).get('git',{}).get('sha1','') if vcs.is_file() else ''
        entry=collect_package('rust',row['name'],row['version'],source,output,row.get('license'),row.get('repository') or '')
        entry['source_revision']=revision
        entry['crate_source']=f"https://crates.io/api/v1/crates/{row['name']}/{row['version']}/download"
        records.append(entry)
    site=package/'runtime/Lib/site-packages'
    for dist in distributions(path=[str(site)]):
        info=dist.metadata
        files=[Path(dist.locate_file(p)).resolve().relative_to(package.resolve()).as_posix() for p in dist.files or []
               if any(part.lower().startswith(PREFIXES) for part in Path(str(p)).parts) and Path(dist.locate_file(p)).is_file()]
        if not files:
            extra=collect_package('python',info['Name'],dist.version,ROOT/'licenses/supplemental/python'/info['Name']/dist.version,output,
                                  info.get('License-Expression') or info.get('License'))
            files=extra['files']
        records.append(dict(ecosystem='python',name=info['Name'],version=dist.version,
                            license=info.get('License-Expression') or info.get('License') or 'NOT_DECLARED',
                            repository=info.get('Home-page') or '',files=sorted(files)))
    records.sort(key=lambda p:(p['ecosystem'],p['name'],p['version']))
    (output/'dependencies.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n','utf-8')
    return {group:sum(row['ecosystem']==group for row in records) for group in ('frontend','rust','python')}
