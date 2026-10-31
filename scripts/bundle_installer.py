"""Build NSIS from the same audited payload as the portable ZIP; never publish."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from desk_companion.resource_lock import lock_filename
from desk_companion.runtime_instance import RUNTIME_LOCK_NAME
from scripts.release_audit import audit
from scripts.bundle_release import write_checksums,release_binary,binary_identity,payload_files,payload_hashes,write_archive
from scripts.capture_installer_binary import PORTABLE_MARKER,NSIS_MARKER


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def installer_mutex_name(identifier):
    return 'Local\\tebb-installer-'+hashlib.sha256(identifier.encode('utf-8')).hexdigest()[:24]


def resources(package):
    """Exact ZIP-equivalent file map, without caches or developer side files."""
    package=Path(package).resolve()
    return {str(p):p.relative_to(package).as_posix()for p in payload_files(package)if p.relative_to(package).as_posix()!='tebb.exe'}


def installer_config(package,hooks):
    return dict(bundle=dict(active=True,targets=['nsis'],useLocalToolsDir=True,createUpdaterArtifacts=False,resources=resources(package),publisher='tebb',
        windows=dict(allowDowngrades=False,webviewInstallMode=dict(type='downloadBootstrapper',silent=True),
            nsis=dict(installMode='currentUser',installerHooks=str(hooks),languages=['SimpChinese','English'],startMenuFolder='tebb'))))


def bundle(package,output,*,run=subprocess.run,verification_profile=None):
    package=Path(package).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    config=json.loads((ROOT/'client/src-tauri/tauri.conf.json').read_text('utf-8'));version=config['version']
    verification=config['identifier']!='cn.deskcompanion.client'or config['productName']!='tebb'
    if verification and verification_profile is None:raise ValueError('验证标识的安装器必须明确指定合成验证目录，不能当作分发候选。')
    source=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    build=json.loads((package/'build-info.json').read_text('utf-8'))
    if build!=dict(version=version,desk_commit=source):raise ValueError('安装载荷与当前候选源码不一致。')
    audit(package,require_licenses=True)
    manifest=payload_hashes(package)
    binary=release_binary();target=binary.parent.parent
    identity=binary_identity(binary)
    if identity['name']!=config['productName']or identity['version'].split('.')[:3]!=version.split('.')[:3]:raise ValueError('程序品牌或版本与安装配置不一致。')
    if digest(binary)!=digest(package/'tebb.exe'):raise ValueError('便携与安装程序不是同一二进制。')
    original=binary.read_bytes();portable_sha=digest(binary)
    if original.count(PORTABLE_MARKER)!=1 or NSIS_MARKER in original:raise ValueError('需要未被先前失败打包改写的全新EXE。')
    installer=output/f'tebb-{version}-windows-x64-setup.exe'
    archive=output/f'tebb-{version}-windows-x64.zip'
    if installer.exists()or archive.exists():raise FileExistsError('分发候选已存在，未覆盖。')
    work=output.parent/(output.name+'-installer-build');work.mkdir()
    hooks=work/'hooks.nsh'
    text=(ROOT/'client/src-tauri/windows/installer-hooks.nsh').read_text('utf-8')
    captured=work/'nsis-main.exe';capture_tool=ROOT/'scripts/capture_installer_binary.py'
    for path in(sys.executable,capture_tool,captured):
        if any(c in str(path)for c in ['"',"'",'$','\n','\r']):raise ValueError('NSIS构建路径含不支持的引号或变量符号。')
    command=f'"{sys.executable}" "{capture_tool}"'
    text=('!define TEBB_INSTALLER_MUTEX_NAME "'+installer_mutex_name(config['identifier'])+'"\n'+"!define TEBB_CAPTURE_TOOL '"+command+"'\n!define TEBB_CAPTURE_PATH \""+str(captured)+"\"\n!define TEBB_PORTABLE_SHA \""+portable_sha+"\"\n"+text)
    if text.count('@RUNTIME_LOCK@')!=2:raise ValueError('安装运行权模板不匹配。')
    if verification_profile is not None:
        if not config['identifier'].startswith('cn.deskcompanion.verify174.')or not config['productName'].startswith('tebb-verify174-'):
            raise ValueError('安装验收必须先构建独立标识的验证程序。')
        profile=Path(verification_profile)
        if not profile.is_absolute()or any(c in str(profile)for c in ['"','$','\n','\r']):raise ValueError('验证目录必须是明确的绝对路径。')
        text='!define TEBB_VERIFY_INSTALLER\n!define TEBB_DATA_ROOT "'+str(profile)+'"\n'+text
    hooks.write_text(text.replace('@RUNTIME_LOCK@',lock_filename(RUNTIME_LOCK_NAME)),encoding='utf-8-sig')
    overlay=work/'tauri-installer.json';overlay.write_text(json.dumps(installer_config(package,hooks),ensure_ascii=False,indent=2),encoding='utf-8')
    node=shutil.which('node')
    if not node:raise RuntimeError('需要Node及锁定的Tauri CLI。')
    cli=ROOT/'client/node_modules/@tauri-apps/cli/tauri.js'
    if not cli.is_file():raise RuntimeError('请先按锁文件安装前端依赖。')
    started=time.time_ns()
    run([node,str(cli),'bundle','--bundles','nsis','--config',str(overlay),'--ci','--no-sign'],cwd=ROOT/'client',check=True,timeout=1800)
    products=[p for p in(target/'release/bundle/nsis').glob(f'*_{version}_x64-setup.exe')if p.stat().st_mtime_ns>=started]
    if len(products)!=1:raise RuntimeError('未取得本轮唯一NSIS候选，未复用旧产物。')
    # Tauri restores the portable EXE after embedding a patched NSIS variant.
    # The compiler hook captured that exact input, without modifying it.
    if manifest!=payload_hashes(package):raise RuntimeError('打包期间载荷发生变化，未交付。')
    if binary_identity(binary)!=identity:raise RuntimeError('打包期间程序身份发生变化，未交付。')
    if digest(binary)!=portable_sha or binary_identity(captured)!=identity:raise RuntimeError('打包输入或恢复后的EXE与候选不一致。')
    shutil.copy2(products[0],installer);write_archive(package,archive,f'tebb-{version}');write_checksums(output,[archive,installer])
    dirty=bool(subprocess.check_output(['git','status','--porcelain','--untracked-files=normal'],cwd=ROOT).strip())
    manifest_digest=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    receipt=dict(version=2,desk_commit=source,app_version=version,verification=verification,dirty=dirty,identifier=config['identifier'],product_name=config['productName'],binary_name=binary.name,payload_manifest_sha256=manifest_digest,payload_files=len(manifest),binary_sha256=digest(binary),nsis_binary_sha256=digest(captured),installer_sha256=digest(installer),
        atlas=json.loads((package/'runtime-provenance.json').read_text('utf-8'))['atlas_commit'],published=False)
    (output/'installer-build.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    print(json.dumps(receipt));return installer


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runtime-package',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--verification-profile',type=Path,help='独立标识验证程序的合成数据目录；不可公开分发')
    args=parser.parse_args();bundle(args.runtime_package,args.output,verification_profile=args.verification_profile)
