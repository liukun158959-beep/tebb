"""Upload one explicitly requested new draft. Never publish or overwrite releases."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.release_audit import audit
from scripts.bundle_release import checked_sums,archive_hashes
from scripts.prepare_runtime import ATLAS_REF


def verify_distribution(directory,version,sha):
    directory=Path(directory)
    archive=directory/f'tebb-{version}-windows-x64.zip'
    installer=directory/f'tebb-{version}-windows-x64-setup.exe'
    audit(archive)
    with zipfile.ZipFile(archive)as package:
        prefix=f'tebb-{version}/'
        build=json.loads(package.read(prefix+'build-info.json'))
        binary=hashlib.sha256(package.read(prefix+'tebb.exe')).hexdigest()
        provenance=json.loads(package.read(prefix+'runtime-provenance.json'))
    if build!=dict(version=version,desk_commit=sha):raise ValueError('Candidate build provenance mismatch')
    sums=checked_sums(directory)
    if set(sums)!={archive.name,installer.name}:raise ValueError('需要同一候选的便携包和安装包摘要。')
    receipt=json.loads((directory/'installer-build.json').read_text('utf-8'))
    hashes=archive_hashes(archive,f'tebb-{version}/');manifest_digest=hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()
    if(receipt.get('version')!=2 or receipt.get('verification')is not False or receipt.get('dirty')is not False
        or receipt.get('identifier')!='cn.deskcompanion.client'or receipt.get('product_name')!='tebb'
        or receipt.get('desk_commit')!=sha or receipt.get('app_version')!=version
        or receipt.get('binary_sha256')!=binary or receipt.get('installer_sha256')!=sums[installer.name]
        or not re.fullmatch('[a-f0-9]{64}',receipt.get('nsis_binary_sha256',''))
        or receipt.get('payload_manifest_sha256')!=manifest_digest or receipt.get('payload_files')!=len(hashes)
        or receipt.get('atlas')!=provenance.get('atlas_commit')or receipt.get('atlas')!=ATLAS_REF):
        raise ValueError('安装候选来源不一致或属于隔离验证程序，禁止上传。')
    return [archive,installer,directory/'SHA256SUMS.txt',directory/'installer-build.json']


def main():
    directory=Path(sys.argv[1]).resolve()
    manifest=json.loads((ROOT/'desk_companion/ui/release.json').read_text('utf-8'))
    sha=os.environ['CANDIDATE_SHA']
    if not re.fullmatch('[a-f0-9]{40}',sha):raise ValueError('Invalid candidate SHA')
    tag=manifest['tag'];version=manifest['version']
    if not re.fullmatch(r'v\d+\.\d+\.\d+',tag) or tag!='v'+version:raise ValueError('Invalid release version/tag')
    assets=verify_distribution(directory,version,sha)
    # List requests must succeed. Authentication/network errors never mean absent.
    repo=os.environ['GITHUB_REPOSITORY']
    def api(route):return json.loads(subprocess.check_output(['gh','api',route],encoding='utf-8'))
    releases=api(f'repos/{repo}/releases?per_page=100')
    if len(releases)>=100:raise RuntimeError('Release 列表需要分页；未创建草稿')
    tags=api(f'repos/{repo}/tags?per_page=100')
    if len(tags)>=100:raise RuntimeError('标签列表需要分页；未创建草稿')
    if any(row['tag_name']==tag for row in releases) or any(row['name']==tag for row in tags):
        raise RuntimeError('该版本 Release 或标签已存在，不覆盖；请先准备新版本')
    notes=ROOT/f'docs/releases/{tag}.md'
    if not notes.is_file():raise RuntimeError('缺少对应版本发布说明')
    subprocess.run(['gh','release','create',tag,*map(str,assets),str(ROOT/'desk_companion/ui'/manifest['image']),
                    '--repo',repo,'--target',sha,'--draft','--title',manifest['title'],'--notes-file',str(notes)],check=True)


if __name__=='__main__':main()
