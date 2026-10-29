"""将已验证的运行时和发布 exe 组成可移动 ZIP，并审计不应分发的文件。"""
import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from release_audit import audit

ROOT = Path(__file__).resolve().parents[1]
VERSION = json.loads((ROOT / "client/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))["version"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package = args.runtime_package.resolve()
    exe = ROOT / "client/src-tauri/target/release/desk-companion-client.exe"
    if not exe.is_file():
        raise RuntimeError("先用 DESK_RELEASE=1 构建 release exe。")
    dist = ROOT / "client/dist"
    if any((dist / directory).exists() for directory in ["skins", "Core"]):
        raise RuntimeError("前端构建目录包含私有素材，请用 DESK_RELEASE=1 重新构建。")
    shutil.copy2(exe, package / "tebb.exe")
    shutil.copy2(ROOT / "docs/GETTING_STARTED.md", package / "使用说明.md")
    shutil.copy2(ROOT / "docs/RELEASING.md", package / "RELEASING.md")
    shutil.copy2(ROOT / "scripts/安装知识库扩展.cmd", package / "安装知识库扩展.cmd")
    shutil.copy2(ROOT / "scripts/runtime-lock.txt", package / "runtime-packages.txt")
    (package / "build-info.json").write_text(json.dumps({
        "version": VERSION,
        "desk_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    }, indent=2) + "\n", encoding="utf-8")
    audit(package)
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / f"tebb-{VERSION}-windows-x64.zip"
    if output.exists():
        raise RuntimeError(f"目标文件已存在：{output}")
    prefix = f"tebb-{VERSION}"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(package.rglob("*")):
            if not file.is_file() or "__pycache__" in file.parts or file.suffix == ".pyc":
                continue
            archive.write(file, f"{prefix}/{file.relative_to(package).as_posix()}")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (args.output / "SHA256SUMS.txt").write_text(f"{digest}  {output.name}\n", encoding="ascii")
    print(f"{output} ({output.stat().st_size / 1024 / 1024:.1f} MiB)")
    print(f"SHA256: {digest}")


if __name__ == "__main__":
    main()
