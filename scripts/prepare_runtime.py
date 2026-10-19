"""构建独立 Windows Python 运行时。只复制明确允许的源码，不读取个人配置。"""
import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = "3.13.16"
SHA256 = "97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297"
ATLAS_REF = "20b890c20afc45cc5e593555e7b6f8ccdbcceb0b"
ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def export_atlas(source, destination):
    """Export only the pinned, committed source; shared by build and offline QA."""
    archive = subprocess.check_output(["git", "-C", str(source), "archive", "--format=zip", ATLAS_REF, "atlas"])
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        package.extractall(destination)
    return subprocess.check_output(["git", "-C", str(source), "rev-parse", ATLAS_REF], text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--atlas", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python-zip", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError(f"输出目录已存在：{output}。请使用一个新目录。")
    archive = args.python_zip.read_bytes() if args.python_zip else urllib.request.urlopen(
        f"https://www.python.org/ftp/python/{VERSION}/python-{VERSION}-embed-amd64.zip", timeout=60
    ).read()
    if hashlib.sha256(archive).hexdigest() != SHA256:
        raise RuntimeError("Python 下载文件的 SHA256 不符。")
    runtime = output / "runtime"
    runtime.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        package.extractall(runtime)
    (runtime / "python313._pth").write_text(
        "python313.zip\n.\nLib/site-packages\nLib/site-packages/win32\nLib/site-packages/win32/lib\nLib/site-packages/Pythonwin\nimport site\n", encoding="utf-8"
    )
    # pip 本身一并提供，知识库扩展可安装到这一套运行时，而非系统 Python。
    run(sys.executable, "-m", "pip", "--python", str(runtime / "python.exe"), "install", "setuptools==84.0.0", "wheel==0.48.0")
    requirements = ROOT / "scripts/runtime-lock.txt"
    if not requirements.is_file():
        requirements = ROOT / "scripts/runtime-requirements.txt"
    run(sys.executable, "-m", "pip", "--python", str(runtime / "python.exe"), "install",
        "--no-build-isolation", "--no-warn-script-location", "--disable-pip-version-check", "-r", str(requirements))
    site = runtime / "Lib/site-packages"
    # pywin32 的 DLL 必须在独立解释器的 DLL 搜索目录中。
    for dll in (site / "pywin32_system32").glob("*.dll"):
        shutil.copy2(dll, runtime / dll.name)
    shutil.copytree(ROOT / "desk_companion", site / "desk_companion",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.log", "*.ready"))
    shutil.copytree(ROOT / "skills", site / "skills", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    ref = export_atlas(args.atlas, site)
    provenance = {"python": VERSION, "python_sha256": SHA256, "atlas_commit": ref}
    (output / "runtime-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    run(str(runtime / "python.exe"), "-c", "from desk_companion.local_api.host import HeadlessApp; from atlas.llm.base import LLM; print('runtime imports OK')")
    lock = subprocess.check_output([str(runtime / "python.exe"), "-m", "pip", "freeze", "--all"], text=True)
    (output / "runtime-packages.txt").write_text(lock, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
