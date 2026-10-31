"""Build and run the real Windows/Tauri workbench close/reopen probe."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,required=True);args=parser.parse_args()
    if os.name!='nt' or not args.work.is_absolute()or args.work.exists():
        raise ValueError('需要Windows及新的绝对验证目录。')
    manifest=ROOT/'client/src-tauri/examples/verify_main_window.manifest'
    files=[manifest,ROOT/'client/src-tauri/src/main_window.rs',ROOT/'client/src-tauri/examples/verify_main_window.rs']
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()for p in files}
    # Cargo examples do not inherit the main EXE's embedded Common Controls v6
    # manifest. Embed it with the Microsoft linker; do not change system DLLs.
    completed=subprocess.run(['cargo','rustc','--locked','--release','--example','verify_main_window','--message-format=json','--',
        '-C','link-arg=/MANIFEST:EMBED','-C','link-arg=/MANIFESTINPUT:'+str(manifest)],
        cwd=ROOT/'client/src-tauri',stdout=subprocess.PIPE,check=True,text=True,encoding='utf-8')
    artifacts=[json.loads(line)for line in completed.stdout.splitlines()if line.startswith('{')]
    binaries=[row['executable']for row in artifacts if row.get('reason')=='compiler-artifact'and row.get('target',{}).get('name')=='verify_main_window'and row.get('executable')]
    if len(binaries)!=1:raise RuntimeError('未取得本轮唯一原生验证程序。')
    result=subprocess.run([binaries[0],str(args.work)],check=False)
    if hashes!={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()for p in files}:raise RuntimeError('验证期间源码发生变化。')
    if args.work.is_dir():
        (args.work/'source.json').write_text(json.dumps(dict(root=str(ROOT),files=hashes,executable_sha256=hashlib.sha256(Path(binaries[0]).read_bytes()).hexdigest(),exit_code=result.returncode),indent=2),encoding='utf-8')
    if result.returncode:raise SystemExit(result.returncode)
    proof=json.loads((args.work/'result.json').read_text('utf-8'))
    if proof.get('ok')is not True:raise RuntimeError('原生验证未通过。')
    print(json.dumps(proof))


if __name__=='__main__':main()
