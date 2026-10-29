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


def main():
    directory=Path(sys.argv[1]).resolve()
    manifest=json.loads((ROOT/'desk_companion/ui/release.json').read_text('utf-8'))
    sha=os.environ['CANDIDATE_SHA']
    if not re.fullmatch('[a-f0-9]{40}',sha):raise ValueError('Invalid candidate SHA')
    tag=manifest['tag'];version=manifest['version']
    if not re.fullmatch(r'v\d+\.\d+\.\d+',tag) or tag!='v'+version:raise ValueError('Invalid release version/tag')
    archive=directory/f'tebb-{version}-windows-x64.zip'
    audit(archive)
    with zipfile.ZipFile(archive) as package:
        build=json.loads(package.read(f'tebb-{version}/build-info.json'))
    if build!=dict(version=version,desk_commit=sha):raise ValueError('Candidate build provenance mismatch')
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    sums=directory/'SHA256SUMS.txt'
    if sums.read_text().strip()!=digest+'  '+archive.name:raise ValueError('Candidate SHA256 mismatch')
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
    subprocess.run(['gh','release','create',tag,str(archive),str(sums),str(ROOT/'desk_companion/ui'/manifest['image']),
                    '--repo',repo,'--target',sha,'--draft','--title',manifest['title'],'--notes-file',str(notes)],check=True)


if __name__=='__main__':main()
