"""本地版本说明与只读正式版本检查，不下载或替换程序。"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

REPOSITORY = "liukun158959-beep/tebb"
RELEASE_REPOSITORIES = (REPOSITORY, "liukun158959-beep/ZhiXing")
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
MANIFEST = Path(__file__).parent / "ui/release.json"


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
        raise ValueError("不是有效的正式版本号。")
    return tuple(int(n) for n in value.removeprefix("v").split("."))


def load_manifest():
    value = json.loads(MANIFEST.read_text("utf-8"))
    version_tuple(value["version"])
    if value["tag"] != "v" + value["version"] or not re.fullmatch(r"release-v[\d.]+\.png", value["image"]):
        raise ValueError("版本说明与图片标识不一致。")
    if not 3 <= len(value["highlights"]) <= 5:
        raise ValueError("版本说明需要 3～5 项重点。")
    return value


def safe_release_url(url, *, tag=None):
    parts = urlsplit(url)
    paths = (f"/{repository}/releases/" for repository in RELEASE_REPOSITORIES)
    valid_path = any(parts.path == path + "tag/" + tag if tag is not None else parts.path.startswith(path)
                     for path in paths)
    return (parts.scheme == "https" and parts.hostname == "github.com" and not parts.username
            and not parts.password and parts.port in (None, 443)
            and valid_path and not parts.query and not parts.fragment)


class ReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not safe_release_url(newurl):
            raise ValueError("更新地址不是项目正式发布页。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_updates():
    current = load_manifest()["version"]
    headers = {"User-Agent": "tebb update check", "Accept": "application/vnd.github+json"}
    try:
        try:
            request = urllib.request.Request(f"https://api.github.com/repos/{REPOSITORY}/releases/latest", headers=headers)
            with urllib.request.urlopen(request, timeout=10) as response:
                release = json.loads(response.read(1_000_000))
            if release.get("draft") or release.get("prerelease"):
                raise ValueError("更新信息不是正式发布版本。")
            latest, url = release["tag_name"], release["html_url"]
            if not safe_release_url(url, tag=latest):
                raise ValueError("更新地址与版本号不一致。")
            notes = re.sub(r"<!--.*?-->", "", str(release.get("body") or ""), flags=re.S)[:12000]
            title, published = release.get("name") or latest, release.get("published_at") or ""
        except (OSError, TimeoutError):
            # 公共 API 共享 IP 配额耗尽时，从 /latest 官方跳转核实正式版本，绝不读取 main 上的待发布版本。
            opener = urllib.request.build_opener(ReleaseRedirect())
            with opener.open(urllib.request.Request(RELEASES_URL + "/latest", headers=headers), timeout=10) as response:
                url = response.geturl()
            latest = urlsplit(url).path.rsplit("/", 1)[-1]
            if not safe_release_url(url, tag=latest):
                raise ValueError("尚未取得可核实的正式版本。")
            title, published = "正式版本 " + latest, ""
            notes = "已从正式发布页核实版本，完整更新说明请打开下载页面查看。"
        remote, local = version_tuple(latest), version_tuple(current)
        return {"ok": True, "current_version": current, "latest_version": latest.removeprefix("v"),
                "status": "available" if remote > local else "latest" if remote == local else "ahead",
                "release_url": url, "title": title, "published_at": published, "notes": notes}
    except Exception:
        return {"ok": False, "current_version": current,
                "error": "暂时无法检查更新，请稍后重试，或直接打开项目发布页面。", "release_url": RELEASES_URL + "/latest"}
