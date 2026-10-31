"""将已验证的运行时和发布 exe 组成可移动 ZIP，并审计不应分发的文件。"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.release_audit import audit
from scripts.license_inventory import generate as generate_licenses
VERSION = json.loads((ROOT / "client/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["version"]


def payload_files(package):
    pending=[Path(package)];files=[]
    while pending:
        node=pending.pop()
        if node.is_symlink()or getattr(node,'is_junction',lambda:False)()or getattr(node.stat(follow_symlinks=False),'st_file_attributes',0)&0x400:
            raise ValueError('候选载荷包含重解析点，未打包。')
        if node.is_dir():
            if node.name!='__pycache__':pending.extend(node.iterdir())
        elif node.suffix!='.pyc':files.append(node)
    return sorted(files)


def payload_hashes(package):
    package=Path(package)
    return {p.relative_to(package).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()for p in payload_files(package)}


def archive_hashes(path,prefix):
    with zipfile.ZipFile(path)as archive:
        if len(archive.namelist())!=len(set(archive.namelist())):raise ValueError('便携候选存在重复路径。')
        if any(not p.startswith(prefix)for p in archive.namelist()):raise ValueError('便携候选根目录不一致。')
        return {name.removeprefix(prefix):hashlib.sha256(archive.read(name)).hexdigest()for name in archive.namelist()if not name.endswith('/')}


def write_archive(package, output, prefix):
    # Upstream notices may intentionally use the Unix epoch. Clamp only ZIP's
    # representable date fields; preserve every byte of the original notice.
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6, strict_timestamps=False) as archive:
        for file in payload_files(package):
            archive.write(file, f"{prefix}/{file.relative_to(package).as_posix()}")


def release_binary():
    value=os.environ.get('CARGO_TARGET_DIR')
    target=Path(value)if value else ROOT/'client/src-tauri/target'
    if not target.is_absolute():raise ValueError('CARGO_TARGET_DIR必须为绝对路径。')
    config=json.loads((ROOT/'client/src-tauri/tauri.conf.json').read_text('utf-8'))
    return target/'release'/(config.get('mainBinaryName','desk-companion-client')+'.exe')


def binary_identity(path):
    import win32api
    for language,codepage in win32api.GetFileVersionInfo(str(path),'\\VarFileInfo\\Translation'):
        prefix=f'\\StringFileInfo\\{language:04x}{codepage:04x}\\'
        name=win32api.GetFileVersionInfo(str(path),prefix+'ProductName')
        version=win32api.GetFileVersionInfo(str(path),prefix+'ProductVersion')
        if name and version:return dict(name=name,version=version)
    raise ValueError('程序缺少可核对的版本资源。')


def prepare_package(package):
    """One audited payload for both the portable ZIP and the NSIS installer."""
    package=Path(package).resolve()
    exe = release_binary()
    if not exe.is_file():
        raise RuntimeError("先用 DESK_RELEASE=1 构建 release exe。")
    config=json.loads((ROOT/'client/src-tauri/tauri.conf.json').read_text('utf-8'));identity=binary_identity(exe)
    if identity['name']!=config['productName']or identity['version'].split('.')[:3]!=VERSION.split('.')[:3]:
        raise ValueError('编译程序与当前配置的品牌或版本不一致。')
    dist = ROOT / "client/dist"
    if any((dist / directory).exists() for directory in ["skins", "Core"]):
        raise RuntimeError("前端构建目录包含私有素材，请用 DESK_RELEASE=1 重新构建。")
    shutil.copy2(exe, package / "tebb.exe")
    shutil.copy2(ROOT / "docs/GETTING_STARTED.md", package / "使用说明.md")
    shutil.copy2(ROOT / "docs/RELEASING.md", package / "RELEASING.md")
    shutil.copy2(ROOT / "scripts/安装知识库扩展.cmd", package / "安装知识库扩展.cmd")
    # Keep prepare_runtime's actual installed inventory as the compatibility
    # fingerprint. The source constraints are retained under their own name.
    shutil.copy2(ROOT / "scripts/runtime-lock.txt", package / "runtime-constraints.txt")
    (package / "build-info.json").write_text(json.dumps({
        "version": VERSION,
        "desk_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    }, indent=2) + "\n", encoding="utf-8")
    print('Dependency notices:', generate_licenses(package))
    audit(package, require_licenses=True)


def checked_sums(directory):
    directory=Path(directory);sums=directory/'SHA256SUMS.txt';values={}
    if sums.exists():
        for line in sums.read_text('ascii').splitlines():
            digest,name=line.split('  ',1)
            if Path(name).name!=name or name in values or hashlib.sha256((directory/name).read_bytes()).hexdigest()!=digest:
                raise ValueError('已有候选摘要不匹配，未改写。')
            values[name]=digest
    return values


def write_checksums(directory,assets):
    directory=Path(directory);sums=directory/'SHA256SUMS.txt';values=checked_sums(directory)
    for asset in assets:
        if asset.parent.resolve()!=directory.resolve():raise ValueError('候选摘要只能引用当前分发目录。')
        values[asset.name]=hashlib.sha256(asset.read_bytes()).hexdigest()
    pending=directory/'SHA256SUMS.pending'
    with pending.open('x',encoding='ascii')as stream:
        stream.write(''.join(f'{digest}  {name}\n'for name,digest in sorted(values.items())))
    pending.replace(sums)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-package", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument('--prepare-only',action='store_true',help='仅组装共同载荷，交由NSIS打包后生成双产物')
    args = parser.parse_args()
    package = args.runtime_package.resolve()
    if not args.prepare_only and args.output is None:parser.error('生成便携ZIP需要--output。')
    prepare_package(package)
    if args.prepare_only:
        print('Common payload prepared; no archive or release created.');return
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / f"tebb-{VERSION}-windows-x64.zip"
    if output.exists():
        raise RuntimeError(f"目标文件已存在：{output}")
    prefix = f"tebb-{VERSION}"
    write_archive(package, output, prefix)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    write_checksums(args.output,[output])
    print(f"{output} ({output.stat().st_size / 1024 / 1024:.1f} MiB)")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()
