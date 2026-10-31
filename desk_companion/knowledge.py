"""飞书文档知识库：本机切块、向量、重排。回答只依据命中的片段。

向量 BAAI/bge-small-zh-v1.5，重排 BAAI/bge-reranker-base。
下载或加载失败就停，不改走接口嵌入，也不改成关键词。
"""
from __future__ import annotations

from .paths import data_root

import json
import os
import shutil
import threading
import time
from contextlib import contextmanager, nullcontext, ExitStack
from contextvars import ContextVar
from functools import wraps
from .resource_lock import ResourceLock

# 权重在 Xet 上。hf_xet 会去 cas-server.xethub.hf.co 重建文件并返回 401。
# 关掉之后走镜像给出的普通下载地址。必须在 huggingface_hub 读环境变量之前写上。
os.environ["HF_HUB_DISABLE_XET"] = "1"
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
_LOCK = threading.Lock()
_STORE = ResourceLock('knowledge-store')
_SETTINGS = ContextVar('knowledge-settings', default=None)
_CACHE_LOCK = threading.RLock()
_MODELS = {}
_MODEL_LOAD_LOCKS = {}


class KnowledgeChangeError(RuntimeError):
    def __init__(self, message, *, change_state, stage, **details):
        super().__init__(message)
        self.details = dict(change_state=change_state, stage=stage, **details)


def _stored(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        with _STORE:
            return fn(*args, **kwargs)
    return run


def _snapshot():
    with _STORE:
        return load_settings(), _read_index()


@contextmanager
def _using_settings(settings):
    token = _SETTINGS.set(settings)
    try:
        yield
    finally:
        _SETTINGS.reset(token)


def _current_settings():
    return _SETTINGS.get() or load_settings()


def _model_stamp(repo):
    folder = _repo_dir(repo)
    return tuple((str(p.relative_to(folder)), p.stat().st_size, p.stat().st_mtime_ns)
                 for p in sorted(folder.rglob('*')) if p.is_file() and not p.name.endswith('.part'))


def _invalidate_model(repo):
    with _CACHE_LOCK:
        for role, (cached_repo, _stamp, _model) in list(_MODELS.items()):
            if cached_repo == repo:
                del _MODELS[role]


@contextmanager
def _model_access(repo, *, write=False):
    lock = ResourceLock('knowledge-model:' + repo, shared=not write)
    if not lock.acquire(blocking=False):
        raise RuntimeError(f'{repo} 正在下载、删除或使用；请等该模型操作结束后再试。')
    try:
        yield
    finally:
        lock.__exit__()


def _commit_index(index, settings, before):
    with _STORE:
        if load_settings() != settings or _read_index() != before:
            raise RuntimeError('计算期间知识库配置或索引已变化，未覆盖现有索引。请核对后手动重试。')
        _write_index(index)


def _changed_page(**details):
    try:
        return public_view()
    except Exception as exc:
        raise KnowledgeChangeError(f'知识库操作已完成，但页面读取失败：{exc}。请重新读取核对，不要直接重复操作。',
            change_state='committed', stage='refresh', **details) from exc


def _save_json(path, value, stage, **details):
    from .video import atomic_write
    try:
        atomic_write(path, value)
    except Exception as exc:
        raise KnowledgeChangeError(f'知识库写入结果未确认：{exc}。请重新读取核对，不要直接重复操作。',
            change_state='unconfirmed', stage=stage, **details) from exc

SUBAGENT = "知识库检索"
# 直连 huggingface 的文件会转到 us.aws.cdn.hf.co，本机大约每秒几百 KB。
# ModelScope 同一份权重大约每秒 1MB 多，地址写在页面上。
HUB = "https://modelscope.cn"
PIP_HINT = (
    "在 PowerShell 里先执行 python -m pip install torch --index-url https://mirrors.aliyun.com/pytorch-wheels/cpu ，"
    "再执行 python -m pip install sentence-transformers -i https://pypi.tuna.tsinghua.edu.cn/simple 。装完重启客户端。"
)
if os.environ.get("DESK_COMPANION_DATA_DIR"):
    PIP_HINT = "便携版请运行发布目录内的「安装知识库扩展.cmd」，安装完重启客户端；源码版可在当前 Python 环境安装 sentence-transformers。"
EMBED_MODELS = (
    {"repo": "BAAI/bge-small-zh-v1.5", "blurb": "中文，体积小，适合先跑通。"},
    {"repo": "BAAI/bge-base-zh-v1.5", "blurb": "中文，比 small 更大，CPU 上更慢。"},
    {"repo": "BAAI/bge-m3", "blurb": "中英混合时用，体积更大。"},
)
RERANK_MODELS = (
    {"repo": "BAAI/bge-reranker-base", "blurb": "中英重排，体积适中。"},
    {"repo": "BAAI/bge-reranker-v2-m3", "blurb": "中英都要时用，比 base 更大。"},
)
STRATEGIES = (
    {"id": "fixed", "label": "固定长度切片", "blurb": "按字数或 token 数切开。最简单，容易切断语义。"},
    {"id": "structure", "label": "按结构切片", "blurb": "按标题、段落、句子切开，句子尽量保持完整。"},
    {"id": "semantic", "label": "语义切片", "blurb": "相邻句子的向量相似度低于 0.5 就切开，相似的留在一块。"},
    {"id": "overlap", "label": "重叠切片", "blurb": "按字数切开，相邻片段有重叠，减少边界信息丢掉。"},
    {"id": "multi", "label": "多级切片", "blurb": "整节和段内片段都检索。命中段内片段时，把整节交给模型。"},
    {"id": "parent", "label": "父子切片", "blurb": "用句子定位，把所在段落的完整内容交给模型。"},
)
CHUNK_SIZE = 800
RETRIEVE_K = 20
RERANK_N = 4
OVERLAP = 200
SEMANTIC_SIM = 0.5
_JOBS: dict[str, dict] = {}
_SEQ = 0

_ASK = (
    "你是知识库检索子代理。只根据下面的片段回答。"
    "片段里没有的内容就写「文档里没有」。不要用文档外的知识补全，不要调用工具。"
    "回答里点明文档名。"
)
_NOTE_ASK = (
    "你是笔记检索。只根据编号片段回答。"
    "用到哪一段，就在那句末尾写对应编号，例如 [1]。"
    "片段里没有的内容就写「文档里没有」。不要用文档外的知识补全，不要调用工具。"
)


def model_root() -> Path:
    return data_root() / "models"


def _settings_path() -> Path:
    from .memory import memory_path

    return memory_path().parent / "knowledge_settings.json"


def _index_path() -> Path:
    from .memory import memory_path

    return memory_path().parent / "knowledge_index.json"


def default_settings() -> dict:
    return {
        "chunk_size": CHUNK_SIZE,
        "retrieve_k": RETRIEVE_K,
        "rerank_n": RERANK_N,
        "embed_repo": EMBED_MODELS[0]["repo"],
        "rerank_repo": RERANK_MODELS[0]["repo"],
        "chunk_strategy": "structure",
        "chunk_unit": "chars",
        "overlap": OVERLAP,
    }


@_stored
def load_settings() -> dict:
    path = _settings_path()
    if not path.is_file():
        return default_settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path} 不是合法 JSON。删掉该文件后重开知识库页。") from exc
    return _checked_settings(raw, path)


def save_settings(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise RuntimeError("知识库设置必须是对象。")
    clean = _checked_settings(
        {
            "chunk_size": payload.get("chunk_size"),
            "retrieve_k": payload.get("retrieve_k"),
            "rerank_n": payload.get("rerank_n"),
            "embed_repo": payload.get("embed_repo"),
            "rerank_repo": payload.get("rerank_repo"),
            "chunk_strategy": payload.get("chunk_strategy"),
            "chunk_unit": payload.get("chunk_unit"),
            "overlap": payload.get("overlap"),
        },
        _settings_path(),
    )
    path = _settings_path()
    with _STORE:
        _save_json(path, clean, 'settings_save')
    return _changed_page()


def _checked_settings(raw: object, path: Path) -> dict:
    if not isinstance(raw, dict):
        raise RuntimeError(f"{path} 必须是对象。")
    needed = ("chunk_size", "retrieve_k", "rerank_n", "embed_repo", "rerank_repo", "chunk_strategy", "chunk_unit", "overlap")
    if any(key not in raw for key in needed):
        raise RuntimeError(f"{path} 缺少模型或切块策略。删掉该文件后重开知识库页。")
    chunk = raw.get("chunk_size")
    retrieve = raw.get("retrieve_k")
    rerank = raw.get("rerank_n")
    embed_repo = raw.get("embed_repo")
    rerank_repo = raw.get("rerank_repo")
    strategy = raw.get("chunk_strategy")
    unit = raw.get("chunk_unit")
    overlap = raw.get("overlap")
    if type(chunk) is not int or isinstance(chunk, bool) or not 200 <= chunk <= 2000:
        raise RuntimeError("切块大小必须是 200 到 2000 的整数。")
    if type(retrieve) is not int or isinstance(retrieve, bool) or not 1 <= retrieve <= 50:
        raise RuntimeError("检索条数必须是 1 到 50 的整数。")
    if type(rerank) is not int or isinstance(rerank, bool) or not 1 <= rerank <= retrieve:
        raise RuntimeError("重排保留条数必须是整数，且不能大于检索条数。")
    embed_known = {item["repo"] for item in EMBED_MODELS}
    rerank_known = {item["repo"] for item in RERANK_MODELS}
    if type(embed_repo) is not str or embed_repo not in embed_known:
        raise RuntimeError("向量模型必须从清单里选。")
    if type(rerank_repo) is not str or rerank_repo not in rerank_known:
        raise RuntimeError("重排模型必须从清单里选。")
    strategy_known = {item["id"] for item in STRATEGIES}
    if type(strategy) is not str or strategy not in strategy_known:
        raise RuntimeError("切块策略必须从六种里选。")
    if unit not in ("chars", "tokens"):
        raise RuntimeError("固定长度要选按字数或按 token。")
    if type(overlap) is not int or isinstance(overlap, bool) or overlap < 1:
        raise RuntimeError("重叠长度必须是正整数。")
    if strategy == "overlap" and overlap >= chunk:
        raise RuntimeError("重叠长度必须小于切块大小。")
    return {
        "chunk_size": chunk,
        "retrieve_k": retrieve,
        "rerank_n": rerank,
        "embed_repo": embed_repo,
        "rerank_repo": rerank_repo,
        "chunk_strategy": strategy,
        "chunk_unit": unit,
        "overlap": overlap,
    }


def _repo_dir(repo: str) -> Path:
    return model_root() / repo.split("/")[-1]


def _ready(repo: str) -> bool:
    folder = _repo_dir(repo)
    # 小文件能下完，权重大文件失败时也会留下 config.json。没有 safetensors 不算下完。
    return (folder / "config.json").is_file() and (folder / "model.safetensors").is_file()


def public_view(project_id='') -> dict:
    settings, index = _snapshot()
    docs = []
    chunks = []
    for doc in index["docs"]:
        if doc.get('project_id', '') != project_id:
            continue
        own = [row for row in index["chunks"] if row["doc_id"] == doc["id"]]
        docs.append({
            "id": doc["id"],
            "title": doc["title"],
            "url": doc["url"],
            "chars": doc["chars"],
            "chunks": len(own),
            **_source_metadata(doc),
            **{key: doc[key] for key in ('source_id', 'source_version', 'project_id', 'updated') if key in doc},
        })
        for row in own:
            chunks.append({
                "doc_id": row["doc_id"],
                "doc": doc["title"],
                "title": row["title"],
                "text": row["text"],
                "level": row.get("level") or "chunk",
                "context": row.get("context") or "",
            })
    downloads, download_seq = _progress_public()
    return {
        "ok": True,
        "subagent": SUBAGENT,
        "embed_repo": settings["embed_repo"],
        "rerank_repo": settings["rerank_repo"],
        "embed_ready": _ready(settings["embed_repo"]),
        "rerank_ready": _ready(settings["rerank_repo"]),
        "models": _model_rows(settings),
        "strategies": list(STRATEGIES),
        "model_dir": str(model_root()),
        "chunk_size": settings["chunk_size"],
        "retrieve_k": settings["retrieve_k"],
        "rerank_n": settings["rerank_n"],
        "chunk_strategy": settings["chunk_strategy"],
        "chunk_unit": settings["chunk_unit"],
        "overlap": settings["overlap"],
        "indexed_chunk_size": index.get("chunk_size") or 0,
        "indexed_strategy": index.get("chunk_strategy") or "",
        "indexed_unit": index.get("chunk_unit") or "",
        "indexed_overlap": index.get("overlap") or 0,
        "indexed_embed": index.get("embed_repo") or "",
        "docs": docs,
        "chunks": chunks,
        "downloads": downloads,
        "download_seq": download_seq,
        "endpoint": HUB,
    }


def _model_rows(settings: dict) -> list[dict]:
    rows = []
    groups = (
        ("embed", EMBED_MODELS, settings["embed_repo"]),
        ("rerank", RERANK_MODELS, settings["rerank_repo"]),
    )
    for role, catalog, selected in groups:
        for item in catalog:
            repo = item["repo"]
            rows.append({
                "role": role,
                "repo": repo,
                "blurb": item["blurb"],
                "ready": _ready(repo),
                "present": _repo_dir(repo).is_dir(),
                "selected": repo == selected,
            })
    return rows


def _progress_public() -> tuple[list[dict], int]:
    with _LOCK:
        rows = []
        for job in _JOBS.values():
            row = dict(job)
            row.pop("_at", None)
            rows.append(row)
        return rows, _SEQ


def _touch_progress(repo: str, **fields: object) -> None:
    global _SEQ
    with _LOCK:
        job = _JOBS.get(repo)
        if job is None:
            return
        _SEQ += 1
        job["seq"] = _SEQ
        job["_at"] = time.monotonic()
        job.update(fields)


def _note_download(repo: str, done: int, total: int) -> None:
    global _SEQ
    percent = min(100, int(done * 100 / total) if total else 0)
    now = time.monotonic()
    with _LOCK:
        job = _JOBS.get(repo)
        if job is None or not job.get("active"):
            return
        prev = int(job["percent"])
        last = float(job["_at"])
        job["phase"] = "downloading"
        job["bytes"] = done
        job["total"] = total
        job["percent"] = percent
        job["error"] = ""
        if percent != prev or now - last >= 0.25 or (total > 0 and done == total):
            _SEQ += 1
            job["seq"] = _SEQ
            job["_at"] = now


def _download_error(repo: str, exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    if "401" in text or "xethub" in text or "Unauthorized" in text:
        return f"下载 {repo} 时文件存储拒绝了 Xet 重建（401）。恢复：重启客户端后再点一次下载。"
    if "10060" in text or "ConnectTimeout" in text or "Timeout" in text or "timed out" in text.lower():
        return f"下载 {repo} 时连接 {HUB} 超时。恢复：检查网络后在知识库页再点下载。"
    if "An error happened while trying to locate" in text:
        text = text.split("An error happened while trying to locate")[0].strip(" 。")
    if len(text) > 160:
        text = text[:160] + "…"
    return f"下载 {repo} 失败：{text}。恢复：检查网络后在知识库页再点下载。"


def _ms_files(repo: str) -> list[dict]:
    import json as _json
    from urllib.request import Request, urlopen

    url = f"{HUB}/api/v1/models/{repo}/repo/files?Revision=master&Recursive=True"
    req = Request(url, headers={"User-Agent": "desk-companion"})
    try:
        with urlopen(req, timeout=60) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"连接 {HUB} 失败：{exc}。恢复：检查网络后在知识库页再点下载。") from exc
    if not payload.get("Success"):
        raise RuntimeError(f"{repo} 在 ModelScope 上没有文件清单。恢复：换一个清单里的模型。")
    blobs = [item for item in payload.get("Data", {}).get("Files", []) if item.get("Type") == "blob"]
    paths = {item.get("Path") for item in blobs}
    if "model.safetensors" not in paths or "config.json" not in paths:
        raise RuntimeError(f"{repo} 在 ModelScope 上没有可用权重。恢复：换一个清单里的模型。")
    files = []
    for item in blobs:
        path = str(item.get("Path") or "")
        # pytorch_model.bin 和 safetensors 是同一份权重，加载只用 safetensors。
        if path == "pytorch_model.bin" or path.endswith((".onnx", ".ot", ".msgpack", ".h5")):
            continue
        size = item.get("Size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise RuntimeError(f"{repo} 的 {path} 没有文件大小。")
        files.append({"path": path, "size": size})
    return files


def _ms_get(repo: str, rel: str, target: Path, size: int, on_bytes, on_restart) -> None:
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    have = partial.stat().st_size if partial.is_file() else 0
    if have > size:
        partial.unlink()
        have = 0
    url = f"{HUB}/api/v1/models/{quote(repo, safe='/')}/repo?Revision=master&FilePath={quote(rel)}"
    headers = {"User-Agent": "desk-companion"}
    if 0 < have < size:
        headers["Range"] = f"bytes={have}-"
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=60) as resp:
            code = getattr(resp, "status", 200)
            ranged = bool(resp.headers.get("Content-Range"))
            # ModelScope 对 Range 仍回 200，但带 Content-Range，正文只是剩下的一段。
            if have and code == 200 and not ranged:
                have = 0
                on_restart()
            with partial.open("ab" if have and (code == 206 or ranged) else "wb") as out:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    on_bytes(len(chunk))
    except Exception as exc:
        raise RuntimeError(f"下载 {rel} 失败：{exc}。恢复：在知识库页再点一次下载，会从断点继续。") from exc
    got = partial.stat().st_size
    if size and got != size:
        raise RuntimeError(f"{rel} 大小不对：下到 {got}，清单是 {size}。恢复：再点一次下载。")
    partial.replace(target)


def _download_contents(repo: str, dest: Path) -> None:
    try:
        files = _ms_files(repo)
        root = model_root().resolve()
        if dest.is_symlink() or root not in dest.resolve().parents:
            raise RuntimeError('模型目录不在指定缓存目录内，拒绝下载。')
        for item in files:
            path = dest / item['path']
            if not item['path'] or dest.resolve() not in path.resolve().parents:
                raise RuntimeError('模型文件路径超出指定目录，拒绝下载。')
        total = sum(item["size"] for item in files)
        done = 0
        _touch_progress(repo, phase="downloading", bytes=0, total=total, percent=0, error="")
        for item in files:
            path = dest / item["path"]
            if path.is_file() and path.stat().st_size == item["size"]:
                done += item["size"]
                _note_download(repo, done, total)
                continue
            partial = path.with_name(path.name + ".part")
            have = partial.stat().st_size if partial.is_file() and partial.stat().st_size < item["size"] else 0
            state = {"extra": have}
            _note_download(repo, done + have, total)

            def on_bytes(count: int, base=done) -> None:
                state["extra"] += count
                _note_download(repo, base + state["extra"], total)

            def on_restart() -> None:
                state["extra"] = 0

            _ms_get(repo, item["path"], path, item["size"], on_bytes, on_restart)
            done += item["size"]
        if not _ready(repo):
            raise RuntimeError(f"下载 {repo} 之后没有完整权重。恢复：删掉 {dest} 后再下一次。")
        _invalidate_model(repo)
        _touch_progress(repo, active=False, phase="done", percent=100, error="")
    except Exception as exc:
        _touch_progress(repo, active=False, phase="error", error=_download_error(repo, exc))


def _download_worker(repo, dest, admission):
    try:
        with _model_access(repo, write=True):
            admission['accepted'] = True
            admission['ready'].set()
            _download_contents(repo, dest)
    except Exception as exc:
        admission['error'] = str(exc)
        _touch_progress(repo, active=False, phase='error', error=str(exc))
    finally:
        admission['ready'].set()


def _known_repo(repo: str) -> str:
    text = (repo or "").strip()
    known = {item["repo"] for item in EMBED_MODELS + RERANK_MODELS}
    if text not in known:
        raise RuntimeError("只能选择清单里的向量模型或重排模型。")
    return text


def download_model(repo: str) -> dict:
    global _SEQ
    target = _known_repo(repo)
    dest = _repo_dir(target)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        job = _JOBS.get(target)
        if job is not None and job.get("active"):
            raise RuntimeError(f"{target} 正在下载。恢复：等这条进度结束再下一次。")
        _SEQ += 1
        _JOBS[target] = {
            "active": True,
            "repo": target,
            "phase": "listing",
            "bytes": 0,
            "total": 0,
            "percent": 0,
            "endpoint": HUB,
            "error": "",
            "seq": _SEQ,
            "_at": time.monotonic(),
        }
    admission = dict(ready=threading.Event(), accepted=False, error='')
    try:
        threading.Thread(target=_download_worker, args=(target, dest, admission), daemon=True).start()
    except Exception as exc:
        _touch_progress(target, active=False, phase='error', error=str(exc))
        raise KnowledgeChangeError(f'下载线程未能启动：{exc}。', change_state='not_changed',
            stage='download_start', repo=target, started=False) from exc
    admission['ready'].wait()  # Only admission; network begins after the worker signals.
    if not admission['accepted']:
        raise KnowledgeChangeError(admission['error'], change_state='not_changed',
            stage='download_start', repo=target, started=False)
    try:
        return public_view()
    except Exception as exc:
        raise KnowledgeChangeError(f'下载已经启动，但返回页面失败：{exc}。请先读取进度核对，不要直接重发。',
            change_state='committed', stage='refresh', repo=target, started=True) from exc


def delete_model(repo: str) -> dict:
    target = _known_repo(repo)
    with _model_access(target, write=True):
        folder = _repo_dir(target)
        root = model_root().resolve()
        if folder.is_symlink() or root not in folder.resolve().parents:
            raise RuntimeError('模型目录不在指定缓存目录内，拒绝删除。')
        if not folder.is_dir():
            raise RuntimeError(f'本机没有 {target}。')
        _invalidate_model(target)
        try:
            shutil.rmtree(folder)
        except Exception as exc:
            raise KnowledgeChangeError(f'模型清理未完成，可能已有部分文件删除：{exc}。已删除文件不会恢复，请核对目录。',
                change_state='partial', stage='model_delete', repo=target, path=str(folder)) from exc
        with _LOCK:
            _JOBS.pop(target, None)
    return _changed_page(repo=target)


def _source_metadata(doc):
    from .wiki_connection import cached_source
    source = {**doc, **(cached_source(doc['id']) if doc.get('source') != 'local' else {})}
    return {key: source[key] for key in ('source', 'space_id', 'space_name', 'local_path', 'folder_name') if key in source}


def add_doc(doc_id: str, label: str, source: dict | None = None) -> dict:
    target = (doc_id or "").strip()
    title = (label or "").strip() or "（无标题）"
    if not target:
        raise RuntimeError("添加文档需要飞书地址或 token。")
    metadata = {}
    if source:
        if not isinstance(source, dict): raise ValueError('文档来源信息不正确。')
        metadata = {key: str(source.get(key) or '')[:200] for key in ('source', 'space_id', 'space_name')}
    if target.startswith('local:'):
        from .local_sources import get
        local = get(target[6:])
        if local['kind'] != 'file': raise ValueError('请选择文件夹内的文件加入知识库。')
        metadata = {'source': 'local', 'space_name': local.get('folder_name') or '本地文件',
                    'folder_name': local.get('folder_name', ''), 'local_path': local['path']}
    settings, before = _snapshot()
    _require_same_index(before, settings)
    if any(doc["id"] == target for doc in before["docs"]):
        raise RuntimeError(f"《{title}》已经在库里。")
    if metadata.get('local_path') and any(doc.get('local_path') == metadata['local_path'] and not doc.get('project_id') for doc in before['docs']):
        raise RuntimeError(f'《{title}》已经在库里，可重建索引更新内容。')
    if not target.startswith('local:'):
        from .material_sources import listing
        registered = next((r for r in listing() if r['kind'] == 'feishu' and r['locator'] == target), None)
        if registered and any(d['id'] == registered['doc_id'] for d in before['docs']):
            raise RuntimeError(f'《{title}》已经在库里，可刷新此来源更新内容。')
    with _using_settings(settings):
        _require_models()
        body = _fetch(target, title)
        made = _chunks_for(target, title, body, settings)
        _attach_vectors(made)
    doc = dict(id=target, title=title, url=target if target.startswith("http") else "",
               chars=len(body), updated=_now(), **metadata)
    index = dict(before, docs=[*before['docs'], doc], chunks=before['chunks'] + made)
    _stamp(index, settings)
    _commit_index(index, settings, before)
    return _changed_page(doc_id=target)


def delete_doc(doc_id: str, project_id='') -> dict:
    target = (doc_id or "").strip()
    if not target:
        raise RuntimeError("删除文档需要文档 id。")
    with _STORE:
        index = _read_index()
        target_doc = next((doc for doc in index['docs'] if doc['id'] == target and doc.get('project_id', '') == project_id), None)
        if target_doc is None:
            if any(doc['id'] == target for doc in index['docs']):
                raise ValueError('资料不在当前范围')
            raise RuntimeError('库里没有这篇文档。')
        kept = [doc for doc in index["docs"] if doc["id"] != target]
        if len(kept) == len(index["docs"]):
            raise RuntimeError("库里没有这篇文档。")
        index["docs"] = kept
        index["chunks"] = [row for row in index["chunks"] if row["doc_id"] != target]
        if not index["docs"]:
            index["chunk_size"] = 0
            index["embed_repo"] = ""
            index["chunk_strategy"] = ""
            index["chunk_unit"] = ""
            index["overlap"] = 0
        _write_index(index)
    return _changed_page(doc_id=target)


def replace_document(doc_id, title, body, metadata, *, guard=None):
    """Index one captured body atomically; source identity/authorization belongs to its caller."""
    settings, before = _snapshot()
    _require_same_index(before, settings)
    with _using_settings(settings):
        _require_models()
        made = _chunks_for(doc_id, title, body, settings)
        _attach_vectors(made)
    metadata = dict(metadata)
    url = metadata.pop('url', '')
    doc = dict(id=doc_id, title=title, url=url, chars=len(body), updated=_now(), **metadata)
    index = dict(before, docs=[*[d for d in before['docs'] if d['id'] != doc_id], doc],
                 chunks=[*[c for c in before['chunks'] if c['doc_id'] != doc_id], *made])
    _stamp(index, settings)
    with guard if guard is not None else nullcontext():
        _commit_index(index, settings, before)
    return doc


def indexed_documents():
    """Metadata snapshot for the material/index composition service; no file or model reads."""
    return _snapshot()[1]['docs']


def retrieve_sources(question, doc_ids=None, project_id=''):
    """Read-only retrieval/debug entry with an explicit trusted scope; no LLM request."""
    if doc_ids is not None and (not isinstance(doc_ids, list) or not doc_ids or any(not isinstance(v, str) for v in doc_ids)):
        raise ValueError('资料编号列表无效')
    found = _retrieve(question, set(doc_ids) if doc_ids is not None else None, project_id)
    _, cites = _blocks(found['kept'], True)
    return {**found, 'cites': cites}


def rebuild() -> dict:
    settings, before = _snapshot()
    docs = [dict(doc) for doc in before["docs"]]
    if not docs:
        raise RuntimeError("库是空的，没有可以重建的文档。")
    from . import material_sources
    import uuid
    managed = []; committed = False
    try:
        with _using_settings(settings):
            _require_models()
            chunks = []
            refreshed = []
            for doc in docs:
                if doc.get('source_id'):
                    ident = uuid.uuid4().hex; pid = doc.get('project_id', '')
                    material_sources.begin(doc['source_id'], pid, ident, 'rebuild')
                    managed.append(dict(source_id=doc['source_id'], project_id=pid, operation_id=ident))
                    observed, body = material_sources.observe(doc['source_id'], pid, ident)
                    managed[-1].update(revision=observed['revision'], version=observed['observed_version'])
                    doc['source_version'] = observed['observed_version']
                    if observed['kind'] == 'local': doc['local_path'] = observed['locator']
                else:
                    body = _fetch(doc["id"], doc["title"])
                made = _chunks_for(doc["id"], doc["title"], body, settings)
                _attach_vectors(made)
                doc["chars"] = len(body)
                doc["updated"] = _now()
                refreshed.append(doc)
                chunks.extend(made)
        index = dict(before, docs=refreshed, chunks=chunks)
        _stamp(index, settings)
        with ExitStack() as guards:
            for row in managed:
                guards.enter_context(material_sources.commit_guard(**row))
            _commit_index(index, settings, before)
        committed = True
        for row in managed:
            material_sources.finish(row['source_id'], row['project_id'], row['operation_id'])
    except Exception as exc:
        for row in managed:
            try: material_sources.finish(row['source_id'], row['project_id'], row['operation_id'], error=str(exc))
            except Exception: pass
        if committed:
            raise KnowledgeChangeError(str(exc), change_state='committed', stage='material_finalize') from exc
        raise
    return _changed_page()


def ask(question: str, complete, on_retrieved=None) -> dict:
    """检索后调用 complete(system, user) 得到回答。库空、模型未下、检索结果空都失败。"""
    found = _retrieve(question, None)
    if on_retrieved is not None:
        on_retrieved({
            "subagent": SUBAGENT,
            "question": found["question"],
            "candidates": found["candidates"],
            "kept": found["kept"],
        })
    blocks, _cites = _blocks(found["kept"], numbered=False)
    answer = complete(_ASK, "问题：\n" + found["question"] + "\n\n片段：\n\n" + "\n\n".join(blocks))
    if type(answer) is not str or not answer.strip():
        raise RuntimeError("知识库检索没有给出正文。恢复：再问一次。")
    return {
        "subagent": SUBAGENT,
        "question": found["question"],
        "candidates": found["candidates"],
        "kept": found["kept"],
        "answer": answer.strip(),
    }


def ask_sources(question: str, doc_ids: list, complete) -> dict:
    """只在勾选的来源里检索。回答必须带 [编号]，编号对应 cites。"""
    if type(doc_ids) is not list or not doc_ids:
        raise RuntimeError("先勾选来源再问。")
    ids = []
    for item in doc_ids:
        if type(item) is not str or not item.strip():
            raise RuntimeError("来源 id 是空的。")
        ids.append(item.strip())
    found = _retrieve(question, set(ids))
    blocks, cites = _blocks(found["kept"], numbered=True)
    answer = complete(_NOTE_ASK, "问题：\n" + found["question"] + "\n\n片段：\n\n" + "\n\n".join(blocks))
    if type(answer) is not str or not answer.strip():
        raise RuntimeError("笔记检索没有给出正文。恢复：再问一次。")
    return {
        "question": found["question"],
        "answer": answer.strip(),
        "cites": cites,
    }


def _retrieve(question: str, only_ids: set[str] | None, project_id='') -> dict:
    text = (question or "").strip()
    if not text:
        raise RuntimeError("问题是空的。")
    settings, index = _snapshot()
    with _using_settings(settings):
        return _retrieve_snapshot(text, only_ids, settings, index, project_id)


def _retrieve_snapshot(text, only_ids, settings, index, project_id=''):
    from .material_sources import eligible, scope
    scope(project_id)
    visible = {doc['id'] for doc in index['docs'] if eligible(doc, project_id)}
    _require_models()
    if not index["docs"] or not index["chunks"]:
        raise RuntimeError("知识库是空的。恢复：在知识库页添加飞书文档。")
    _require_same_index(index, settings)
    if only_ids is not None:
        known = visible
        missing = [item for item in only_ids if item not in known]
        if missing:
            raise RuntimeError("这些来源不在库里：" + "、".join(missing) + "。恢复：重新勾选后再问。")
    query_vec = _embed([text])[0]
    scored = []
    seen_chunk = False
    for row in index["chunks"]:
        if row.get('doc_id') not in visible:
            continue
        if only_ids is not None and row.get("doc_id") not in only_ids:
            continue
        seen_chunk = True
        vec = row.get("embedding")
        if row.get("level") == "parent" and type(vec) is not list:
            continue
        if type(vec) is not list or len(vec) != len(query_vec):
            raise RuntimeError("索引里的向量对不上当前模型。恢复：在知识库页重建索引。")
        scored.append((_dot(query_vec, vec), row))
    if only_ids is not None and not seen_chunk:
        raise RuntimeError("勾选的来源还没有切块。恢复：在知识库页重建索引。")
    scored.sort(key=lambda item: item[0], reverse=True)
    candidates = [_hit(score, row, index) for score, row in scored[: settings["retrieve_k"]]]
    if not candidates:
        raise RuntimeError("没有检索到片段。恢复：确认文档已入库，或换个问法。")
    order = _rerank(text, candidates)
    kept = order[: settings["rerank_n"]]
    if not kept:
        raise RuntimeError("重排没有留下片段。恢复：把重排保留条数调到至少 1 后再问。")
    return {"question": text, "candidates": candidates, "kept": kept}


def _blocks(kept: list[dict], numbered: bool) -> tuple[list[str], list[dict]]:
    blocks = []
    cites = []
    seen = set()
    number = 0
    for item in kept:
        body = item.get("context") or item["text"]
        if item.get('freshness') == 'legacy_unverified':
            body = '【历史索引；尚未核对当前原文】\n' + body
        key = (item["doc"], item["title"], body)
        if key in seen:
            continue
        seen.add(key)
        if not numbered:
            blocks.append(f"文档：{item['doc']}\n标题：{item['title']}\n{body}")
            continue
        number += 1
        doc_id = item.get("doc_id")
        if type(doc_id) is not str or not doc_id.strip():
            raise RuntimeError("片段缺少文档 id。恢复：在知识库页重建索引。")
        blocks.append(f"[{number}] 文档：{item['doc']}\n标题：{item['title']}\n{body}")
        cite = {
            "n": number,
            "doc": item["doc"],
            "title": item["title"],
            "text": item["text"],
            "doc_id": doc_id.strip(),
        }
        for key in ('source_id', 'source_version', 'project_id', 'indexed_at'):
            if key in item:
                cite[key] = item[key]
        if item.get("context"):
            cite["context"] = item["context"]
        cites.append(cite)
    if numbered and not cites:
        raise RuntimeError("没有可以引用的片段。")
    return blocks, cites


def check_trace(raw: object) -> dict:
    """落盘前核对一次调用记录。缺字段就失败，不补空列表。"""
    if not isinstance(raw, dict):
        raise RuntimeError("知识库调用记录必须是对象。")
    if raw.get("subagent") != SUBAGENT:
        raise RuntimeError("知识库调用记录的子代理名不对。")
    question = raw.get("question")
    answer = raw.get("answer")
    if type(question) is not str or not question.strip():
        raise RuntimeError("知识库调用记录缺少问题。")
    if type(answer) is not str or not answer.strip():
        raise RuntimeError("知识库调用记录缺少回答。")
    candidates = _checked_hits(raw.get("candidates"), "向量候选")
    kept = _checked_hits(raw.get("kept"), "重排结果")
    if not candidates or not kept:
        raise RuntimeError("知识库调用记录缺少命中片段。")
    return {
        "subagent": SUBAGENT,
        "question": question.strip(),
        "candidates": candidates,
        "kept": kept,
        "answer": answer.strip(),
    }


def _checked_hits(raw: object, label: str) -> list[dict]:
    if type(raw) is not list:
        raise RuntimeError(f"{label}必须是列表。")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise RuntimeError(f"{label}的每一项必须是对象。")
        doc = item.get("doc")
        title = item.get("title")
        text = item.get("text")
        score = item.get("score")
        if type(doc) is not str or not doc.strip():
            raise RuntimeError(f"{label}缺少文档名。")
        if type(title) is not str or not title.strip():
            raise RuntimeError(f"{label}缺少标题。")
        if type(text) is not str or not text.strip():
            raise RuntimeError(f"{label}缺少片段。")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise RuntimeError(f"{label}缺少分数。")
        row = {
            "doc": doc.strip(),
            "title": title.strip(),
            "text": text.strip(),
            "score": round(float(score), 4),
        }
        if "context" in item:
            ctx = item.get("context")
            if type(ctx) is not str or not ctx.strip():
                raise RuntimeError(f"{label}的上下文是空的。")
            row["context"] = ctx.strip()
        out.append(row)
    return out


def _require_models() -> None:
    settings = _current_settings()
    missing = []
    if not _ready(settings["embed_repo"]):
        missing.append(settings["embed_repo"])
    if not _ready(settings["rerank_repo"]):
        missing.append(settings["rerank_repo"])
    if missing:
        raise RuntimeError(
            "本机还没有模型：" + "、".join(missing) + "。恢复：在知识库页点下载。下载失败不会改用接口嵌入。"
        )


def _require_same_index(index: dict, settings: dict) -> None:
    if not index["docs"]:
        return
    bad = []
    if index.get("embed_repo") != settings["embed_repo"]:
        bad.append("向量模型")
    if index.get("chunk_size") != settings["chunk_size"]:
        bad.append("切块大小")
    if index.get("chunk_strategy") != settings["chunk_strategy"]:
        bad.append("切块策略")
    if settings["chunk_strategy"] == "fixed" and index.get("chunk_unit") != settings["chunk_unit"]:
        bad.append("切块单位")
    if settings["chunk_strategy"] == "overlap" and index.get("overlap") != settings["overlap"]:
        bad.append("重叠长度")
    if bad:
        raise RuntimeError("、".join(bad) + "已经变了。恢复：在知识库页重建索引。")


def _stamp(index: dict, settings: dict) -> None:
    index["embed_repo"] = settings["embed_repo"]
    index["chunk_size"] = settings["chunk_size"]
    index["chunk_strategy"] = settings["chunk_strategy"]
    index["chunk_unit"] = settings["chunk_unit"]
    index["overlap"] = settings["overlap"]


def _fetch(doc_id: str, title: str) -> str:
    if doc_id.startswith('local:'):
        from .local_sources import get, extract
        path = Path(get(doc_id[6:])['path'])
        from .local_sources import _linked
        if _linked(path): raise ValueError('本地文件已变为链接，请重新选择原始文件。')
        return extract(path)
    from .feishu_docs import fetch_docx_markdown

    return fetch_docx_markdown(doc_id, title)


def _chunks_for(doc_id: str, doc_title: str, body: str, settings: dict) -> list[dict]:
    pieces = _split(body, settings)
    if not pieces:
        raise RuntimeError("这篇文档切不出片段。")
    out = []
    for idx, piece in enumerate(pieces):
        text = str(piece.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "id": f"{doc_id}:{idx}",
            "doc_id": doc_id,
            "doc": doc_title,
            "title": piece.get("title") or "正文",
            "text": text,
            "level": piece.get("level") or "chunk",
            "parent_id": piece.get("parent_id") or "",
            "context": piece.get("context") or "",
            "embed": bool(piece.get("embed", True)),
        })
    if not out:
        raise RuntimeError("这篇文档切不出片段。")
    return out


def _attach_vectors(made: list[dict]) -> None:
    targets = [row for row in made if row.get("embed", True)]
    if not targets:
        raise RuntimeError("这篇文档没有可检索的片段。")
    vectors = _embed([row["text"] for row in targets])
    for row, vec in zip(targets, vectors):
        row["embedding"] = vec
    for row in made:
        row.pop("embed", None)


def _split(body: str, settings: dict) -> list[dict]:
    kind = settings["chunk_strategy"]
    size = settings["chunk_size"]
    if kind == "fixed":
        return _fixed(body, size, settings["chunk_unit"])
    if kind == "structure":
        return _structure_chunks(body, size)
    if kind == "semantic":
        return _semantic(body, size)
    if kind == "overlap":
        return _overlap(body, size, settings["overlap"])
    if kind == "multi":
        return _multi(body, size)
    if kind == "parent":
        return _parent_child(body, size)
    raise RuntimeError("切块策略不认识。")


def _piece(title: str, text: str, **extra: object) -> dict:
    row = {
        "title": title or "正文",
        "text": text,
        "level": "chunk",
        "parent_id": "",
        "context": "",
        "embed": True,
    }
    row.update(extra)
    return row


def _sections(content: str) -> list[tuple[str, str]]:
    heading = "正文"
    buf: list[str] = []
    out: list[tuple[str, str]] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        buf.clear()
        if body:
            out.append((heading, body))

    for line in content.splitlines():
        if line.startswith("#"):
            flush()
            heading = line.lstrip("#").strip() or "正文"
        else:
            buf.append(line)
    flush()
    if not out and content.strip():
        out.append(("正文", content.strip()))
    return out


def _paragraphs(body: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            if buf:
                parts.append("\n".join(buf).strip())
                buf = []
        else:
            buf.append(line)
    if buf:
        parts.append("\n".join(buf).strip())
    return [part for part in parts if part]


def _sentences(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if ch in "。！？!?;；":
            piece = "".join(buf).strip()
            buf.clear()
            if piece:
                parts.append(piece)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _hard(title: str, text: str, size: int) -> list[dict]:
    rows = []
    start = 0
    while start < len(text):
        piece = text[start:start + size].strip()
        if piece:
            rows.append(_piece(title, piece))
        start += size
    return rows


def _pack(heading: str, units: list[str], size: int) -> list[dict]:
    rows: list[dict] = []
    cur: list[str] = []
    cur_len = 0

    def flush() -> None:
        nonlocal cur_len
        text = "".join(cur).strip()
        cur.clear()
        cur_len = 0
        if text:
            rows.append(_piece(heading, text))

    for unit in units:
        unit = unit.strip()
        if not unit:
            continue
        if len(unit) > size:
            flush()
            rows.extend(_hard(heading, unit, size))
            continue
        if cur and cur_len + len(unit) > size:
            flush()
        cur.append(unit)
        cur_len += len(unit)
    flush()
    return rows


def _structure_units(body: str) -> list[str]:
    units: list[str] = []
    for para in _paragraphs(body) or [body]:
        units.extend(_sentences(para) or [para])
    return units


def _structure_chunks(content: str, size: int) -> list[dict]:
    rows: list[dict] = []
    for heading, body in _sections(content):
        rows.extend(_pack(heading, _structure_units(body), size))
    if not rows:
        raise RuntimeError("这篇文档切不出片段。")
    return rows


def _fixed(body: str, size: int, unit: str) -> list[dict]:
    text = body.strip()
    if not text:
        raise RuntimeError("这篇文档切不出片段。")
    if unit == "chars":
        return _hard("正文", text, size)
    from .context_pack import estimate_tokens

    rows: list[dict] = []
    buf: list[str] = []
    for ch in text:
        trial = "".join(buf) + ch
        if buf and estimate_tokens(trial) > size:
            piece = "".join(buf).strip()
            if piece:
                rows.append(_piece("正文", piece))
            buf = [ch]
        else:
            buf.append(ch)
    if buf:
        piece = "".join(buf).strip()
        if piece:
            rows.append(_piece("正文", piece))
    if not rows:
        raise RuntimeError("这篇文档切不出片段。")
    return rows


def _overlap(body: str, size: int, overlap: int) -> list[dict]:
    text = body.strip()
    step = size - overlap
    if step < 1:
        raise RuntimeError("重叠长度必须小于切块大小。")
    if not text:
        raise RuntimeError("这篇文档切不出片段。")
    rows: list[dict] = []
    start = 0
    while start < len(text):
        piece = text[start:start + size].strip()
        if piece:
            rows.append(_piece("正文", piece))
        if start + size >= len(text):
            break
        start += step
    if not rows:
        raise RuntimeError("这篇文档切不出片段。")
    return rows


def _semantic(body: str, size: int) -> list[dict]:
    units: list[tuple[str, str]] = []
    for heading, section in _sections(body):
        sentences = _sentences(section) or [section]
        for sentence in sentences:
            units.append((heading, sentence))
    if not units:
        raise RuntimeError("这篇文档切不出片段。")
    vectors = _embed([sentence for _, sentence in units])
    groups: list[dict] = []
    bucket = [units[0][1]]
    heading = units[0][0]

    def close() -> None:
        text = "".join(bucket).strip()
        if text:
            groups.append(_piece(heading, text))

    for idx in range(1, len(units)):
        next_heading, sentence = units[idx]
        joined = "".join(bucket) + sentence
        similar = _dot(vectors[idx - 1], vectors[idx]) >= SEMANTIC_SIM
        if next_heading == heading and similar and len(joined) <= size:
            bucket.append(sentence)
            continue
        close()
        bucket = [sentence]
        heading = next_heading
    close()
    rows: list[dict] = []
    for row in groups:
        if len(row["text"]) <= size:
            rows.append(row)
        else:
            rows.extend(_hard(row["title"], row["text"], size))
    if not rows:
        raise RuntimeError("这篇文档切不出片段。")
    return rows


def _multi(body: str, size: int) -> list[dict]:
    rows: list[dict] = []
    sections = _sections(body)
    if not sections:
        raise RuntimeError("这篇文档切不出片段。")
    for idx, (heading, section) in enumerate(sections):
        key = f"p{idx}"
        rows.append(_piece(heading, section, level="coarse", parent_id=key))
        for fine in _pack(heading, _structure_units(section), size):
            fine["level"] = "fine"
            fine["parent_id"] = key
            fine["context"] = section
            rows.append(fine)
    return rows


def _parent_child(body: str, size: int) -> list[dict]:
    parents: list[dict] = []
    for heading, section in _sections(body):
        parents.extend(_pack(heading, _structure_units(section), size))
    if not parents:
        raise RuntimeError("这篇文档切不出片段。")
    rows: list[dict] = []
    for idx, parent in enumerate(parents):
        key = f"p{idx}"
        parent["level"] = "parent"
        parent["embed"] = False
        parent["parent_id"] = key
        rows.append(parent)
        for sentence in _sentences(parent["text"]) or [parent["text"]]:
            pieces = _hard(parent["title"], sentence, size) if len(sentence) > size else [_piece(parent["title"], sentence)]
            for child in pieces:
                child["level"] = "child"
                child["parent_id"] = key
                child["context"] = parent["text"]
                rows.append(child)
    return rows


def _embed(texts: list[str]) -> list[list[float]]:
    settings = _current_settings()
    with _using_settings(settings), _model_access(settings["embed_repo"]):
        return _embed_leased(texts)


def _embed_leased(texts: list[str]) -> list[list[float]]:
    model = _embed_model()
    try:
        raw = model.encode(texts, normalize_embeddings=True)
    except Exception as exc:
        raise RuntimeError(f"向量化失败：{exc}。恢复：确认模型已下载完整后再试。") from exc
    rows = raw.tolist() if hasattr(raw, "tolist") else list(raw)
    if len(rows) != len(texts):
        raise RuntimeError("向量条数和片段条数不一致。")
    out = []
    for row in rows:
        vec = [float(item) for item in (row.tolist() if hasattr(row, "tolist") else row)]
        if not vec:
            raise RuntimeError("向量是空的。")
        out.append(vec)
    return out


def _embed_model():
    return _local_model('embed')


def _local_model(role):
    repo = _current_settings()[role + '_repo']
    with _CACHE_LOCK:
        load_lock = _MODEL_LOAD_LOCKS.setdefault(repo, threading.Lock())
    # Only duplicate loads of this repository wait. Other models and cached inference continue.
    with load_lock:
        stamp = _model_stamp(repo)
        with _CACHE_LOCK:
            cached = _MODELS.get(role)
            if cached is not None and cached[0] == repo and cached[1] == stamp:
                return cached[2]
        try:
            import sentence_transformers
        except ImportError as exc:
            raise RuntimeError(f"还没有 sentence-transformers。恢复：{PIP_HINT}") from exc
        factory = (sentence_transformers.SentenceTransformer if role == 'embed'
                   else sentence_transformers.CrossEncoder)
        try:
            model = factory(str(_repo_dir(repo)), device="cpu")
        except Exception as exc:
            raise RuntimeError(f"加载 {repo} 失败：{exc}。恢复：删掉模型目录后重新下载。") from exc
        with _CACHE_LOCK:
            _MODELS[role] = (repo, stamp, model)
        return model


def _rerank(question: str, candidates: list[dict]) -> list[dict]:
    settings = _current_settings()
    with _using_settings(settings), _model_access(settings["rerank_repo"]):
        return _rerank_leased(question, candidates)


def _rerank_leased(question: str, candidates: list[dict]) -> list[dict]:
    model = _rerank_model()
    pairs = [(question, item["text"]) for item in candidates]
    try:
        raw = model.predict(pairs)
    except Exception as exc:
        raise RuntimeError(f"重排失败：{exc}。恢复：确认重排模型已下载完整后再试。") from exc
    scores = raw.tolist() if hasattr(raw, "tolist") else list(raw)
    if len(scores) != len(candidates):
        raise RuntimeError("重排分数和候选条数不一致。")
    ranked = []
    for item, score in zip(candidates, scores):
        copy = dict(item)
        copy["score"] = round(float(score), 4)
        ranked.append(copy)
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked


def _rerank_model():
    return _local_model('rerank')


def _hit(score: float, row: dict, index: dict) -> dict:
    title = row.get("doc") or ""
    if not title:
        for doc in index["docs"]:
            if doc["id"] == row["doc_id"]:
                title = doc["title"]
                break
    doc_id = row.get("doc_id")
    if type(doc_id) is not str or not doc_id.strip():
        raise RuntimeError("片段缺少文档 id。恢复：在知识库页重建索引。")
    hit = {
        "doc": title or "（无标题）",
        "doc_id": doc_id.strip(),
        "title": row["title"],
        "score": round(float(score), 4),
        "text": row["text"],
    }
    source = next((doc for doc in index['docs'] if doc['id'] == doc_id), {})
    for key in ('source_id', 'source_version', 'project_id'):
        if key in source:
            hit[key] = source[key]
    if 'updated' in source:
        hit['indexed_at'] = source['updated']
    hit['freshness'] = 'captured_version' if source.get('source_version') else 'legacy_unverified'
    context = row.get("context") or ""
    if type(context) is str and context.strip() and context.strip() != row["text"]:
        hit["context"] = context.strip()
    return hit


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _empty_index() -> dict:
    return {
        "embed_repo": "",
        "chunk_size": 0,
        "chunk_strategy": "",
        "chunk_unit": "",
        "overlap": 0,
        "docs": [],
        "chunks": [],
    }


@_stored
def _read_index() -> dict:
    path = _index_path()
    if not path.is_file():
        return _empty_index()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path} 不是合法 JSON。删掉该文件后重新添加文档。") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"{path} 必须是对象。删掉该文件后重新添加文档。")
    docs = raw.get("docs")
    chunks = raw.get("chunks")
    if type(docs) is not list or type(chunks) is not list:
        raise RuntimeError(f"{path} 缺少 docs 或 chunks。删掉该文件后重新添加文档。")
    return {
        "embed_repo": raw.get("embed_repo") if isinstance(raw.get("embed_repo"), str) else "",
        "chunk_size": raw.get("chunk_size") if type(raw.get("chunk_size")) is int else 0,
        "chunk_strategy": raw.get("chunk_strategy") if isinstance(raw.get("chunk_strategy"), str) else "",
        "chunk_unit": raw.get("chunk_unit") if isinstance(raw.get("chunk_unit"), str) else "",
        "overlap": raw.get("overlap") if type(raw.get("overlap")) is int else 0,
        "docs": docs,
        "chunks": chunks,
    }


def _write_index(index: dict) -> None:
    with _STORE:
        _save_json(_index_path(), index, 'index_save')
