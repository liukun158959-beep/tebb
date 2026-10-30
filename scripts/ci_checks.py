"""Local/Actions entry points. Failures and skips are recorded without retries."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time
import tomllib
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def atlas_ref():
    return runpy.run_path(str(ROOT/'scripts/prepare_runtime.py'))['ATLAS_REF']


def check_version():
    manifest=json.loads((ROOT/'desk_companion/ui/release.json').read_text('utf-8'))
    version=manifest['version']
    if manifest['tag']!='v'+version:raise ValueError('manifest tag/version mismatch')
    for name in ('client/package.json','client/src-tauri/tauri.conf.json'):
        if json.loads((ROOT/name).read_text('utf-8'))['version']!=version:raise ValueError(name+' version mismatch')
    for name in ('pyproject.toml','client/src-tauri/Cargo.toml'):
        data=tomllib.loads((ROOT/name).read_text('utf-8'))
        if (data.get('project') or data['package'])['version']!=version:raise ValueError(name+' version mismatch')
    packages=tomllib.loads((ROOT/'client/src-tauri/Cargo.lock').read_text('utf-8'))['package']
    if next(p for p in packages if p['name']=='desk-companion-client')['version']!=version:raise ValueError('Cargo.lock version mismatch')
    if not (ROOT/'desk_companion/ui'/manifest['image']).is_file():raise ValueError('Release poster missing')
    if manifest['image'] not in (ROOT/'client/src/client/release.tsx').read_text('utf-8'):raise ValueError('Release poster import mismatch')
    print(json.dumps(dict(version=version,atlas_commit=atlas_ref())))


class RecordedResult(unittest.TextTestResult):
    def startTest(self,test):
        self.started=time.monotonic();super().startTest(test)

    def stopTest(self,test):
        self.records.append(dict(test=test.id(),duration_ms=round((time.monotonic()-self.started)*1000,2)))
        super().stopTest(test)


def run_tests(output):
    if not os.environ.get('DESK_TEST_ATLAS_SOURCE'):
        raise RuntimeError('必须提供固定 Atlas 源码；不能将缺失契约检查默认为通过。')
    output.mkdir(parents=True,exist_ok=True)
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    records=[]
    with (output/'backend.log').open('w',encoding='utf-8') as stream:
        def result(*args,**kwargs):
            value=RecordedResult(*args,**kwargs);value.records=records;return value
        with redirect_stdout(stream),redirect_stderr(stream):
            outcome=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=result).run(suite)
    report=dict(tests=outcome.testsRun,failures=[dict(test=t.id(),trace=trace) for t,trace in outcome.failures],
                errors=[dict(test=t.id(),trace=trace) for t,trace in outcome.errors],
                skipped=[dict(test=t.id(),reason=reason) for t,reason in outcome.skipped],
                unexpected_successes=[t.id() for t in outcome.unexpectedSuccesses],
                expected_failures=[t.id() for t,_ in outcome.expectedFailures],
                successful=outcome.wasSuccessful(),attempts=1,samples=records)
    (output/'backend.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    summary={k:len(v) if isinstance(v,list) else v for k,v in report.items()}
    print(json.dumps(summary,ensure_ascii=False))
    if not outcome.wasSuccessful():
        # Keep the failure actionable even when artifact transport itself fails.
        for test,trace in outcome.failures+outcome.errors:
            print(f'\n{test.id()}\n{trace}',file=sys.stderr)
        raise SystemExit(1)


def smoke_zip(archive,destination):
    from scripts.release_audit import audit
    report=audit(archive)
    if destination.exists():raise ValueError('迁移验证必须使用新目录')
    with zipfile.ZipFile(archive) as package:package.extractall(destination)
    roots=list(destination.glob('tebb-*'))
    if len(roots)!=1:raise ValueError('Expected exactly one package root')
    build=json.loads((roots[0]/'build-info.json').read_text('utf-8'))
    current=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if build['desk_commit']!=current:raise ValueError('Package commit does not match checked out source')
    subprocess.run([sys.executable,str(ROOT/'scripts/smoke_release.py'),str(roots[0])],check=True,cwd=ROOT)
    print(json.dumps(report))


def check_gate(sha):
    if not re.fullmatch('[a-f0-9]{40}',sha):raise ValueError('必须使用完整提交 SHA')
    subprocess.run(['git','merge-base','--is-ancestor',sha,'origin/main'],check=True,cwd=ROOT)
    repo=os.environ['GITHUB_REPOSITORY']
    runs=json.loads(subprocess.check_output(['gh','api',f'repos/{repo}/actions/workflows/ci.yml/runs?head_sha={sha}&event=push&per_page=100'],text=True,encoding='utf-8'))['workflow_runs']
    if not runs:raise RuntimeError('此提交没有 CI 记录，未开始构建发布候选')
    # The newest attempt controls the gate; an old success cannot hide a rerun failure.
    latest=max(runs,key=lambda row:(row['created_at'],row['run_attempt']))
    if latest['status']!='completed' or latest['conclusion']!='success':
        raise RuntimeError('此提交的最新 CI 尚未全部通过：'+latest['html_url'])
    jobs=json.loads(subprocess.check_output(['gh','api',f'repos/{repo}/actions/runs/{latest["id"]}/jobs?per_page=100'],text=True,encoding='utf-8'))['jobs']
    required={'Frontend','Python / Atlas','Rust'}
    if not required <= {job['name'] for job in jobs if job['conclusion']=='success'}:
        raise RuntimeError('必要 job 缺失或未通过')
    print('Verified CI:',latest['html_url'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('version');commands.add_parser('atlas-ref')
    test=commands.add_parser('test');test.add_argument('--output',type=Path,required=True)
    smoke=commands.add_parser('smoke-zip');smoke.add_argument('archive',type=Path);smoke.add_argument('destination',type=Path)
    gate=commands.add_parser('gate');gate.add_argument('sha')
    args=parser.parse_args()
    if args.command=='version':check_version()
    elif args.command=='atlas-ref':print(atlas_ref())
    elif args.command=='test':run_tests(args.output)
    elif args.command=='smoke-zip':smoke_zip(args.archive,args.destination)
    else:check_gate(args.sha)


if __name__=='__main__':main()
