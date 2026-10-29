"""笔记会话：和普通对话分开，存在 memory/notebook.json。

文件不存在表示还没有笔记会话。文件在但缺字段就失败，不补空列表。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .resource_lock import ResourceLock

TZ = timezone(timedelta(hours=8))
_LOCK = ResourceLock('notebook')
_BAD_NAME = '\\/:*?"<>|'


class NotebookChangeError(RuntimeError):
    """A mutation failed after its outcome could differ from an unchanged notebook."""
    def __init__(self, message, *, change_state, **details):
        super().__init__(message)
        self.details = dict(change_state=change_state, **details)


def _write_change(book, session_id, note_id=None, removed_files=()):
    try:
        _write(book)
    except Exception as exc:
        raise NotebookChangeError(
            f'笔记记录保存结果未确认：{exc}。请重新读取核对，不要直接重复操作。'
            + ('已经清理的生成文件不会恢复。' if removed_files else ''),
            change_state='unconfirmed', session_id=session_id, note_id=note_id,
            removed_files=list(removed_files),
        ) from exc


def _changed_page(session_id, note_id):
    try:
        return load_notebook()
    except Exception as exc:
        raise NotebookChangeError(
            f'笔记操作已保存，但读取笔记页失败：{exc}。请重新读取核对，不要重复执行操作。',
            change_state='committed', session_id=session_id, note_id=note_id,
        ) from exc


def _path() -> Path:
    from .memory import memory_path

    return memory_path().parent / "notebook.json"


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def load_notebook() -> dict:
    from .knowledge import public_view

    page = public_view()
    book = _read()
    return {"ok": True, "docs": page["docs"], "sessions": book["sessions"]}


def new_notebook() -> dict:
    from .knowledge import public_view

    page = public_view()
    with _LOCK:
        book = _read()
        session = {
            "id": uuid.uuid4().hex,
            "title": "新笔记",
            "updated": _now(),
            "turns": [],
            "notes": [],
        }
        book["sessions"].insert(0, session)
        _write_change(book, session['id'])
        sessions = book["sessions"]
    return {"ok": True, "docs": page["docs"], "sessions": sessions, "session_id": session["id"]}


def begin_notebook_turn(session_id: str, question: str) -> None:
    text = (question or "").strip()
    if not text:
        raise RuntimeError("问题是空的。")
    with _LOCK:
        book = _read()
        session = _find(book, session_id)
        session["turns"].append({"role": "user", "text": text})
        if session["title"] == "新笔记":
            session["title"] = text[:24]
        session["updated"] = _now()
        _write_change(book, session['id'])


def finish_notebook_turn(session_id: str, answer: str, cites: list) -> None:
    body = (answer or "").strip()
    if not body:
        raise RuntimeError("笔记检索没有给出正文。恢复：再问一次。")
    checked = _cites(cites)
    with _LOCK:
        book = _read()
        session = _find(book, session_id)
        session["turns"].append({"role": "pet", "text": body, "cites": checked})
        session["updated"] = _now()
        _write_change(book, session['id'])


def save_note(session_id: str, question: str, answer: str, cites: list) -> dict:
    text = (question or "").strip()
    body = (answer or "").strip()
    if not text:
        raise RuntimeError("存笔记需要问题。")
    if not body:
        raise RuntimeError("存笔记需要回答。")
    checked = _cites(cites)
    with _LOCK:
        book = _read()
        session = _find(book, session_id)
        note_id = uuid.uuid4().hex
        session["notes"].insert(0, {
            "id": note_id,
            "question": text,
            "answer": body,
            "cites": checked,
            "saved": _now(),
            "files": [],
        })
        session["updated"] = _now()
        _write_change(book, session['id'], note_id)
    page = _changed_page(session['id'], note_id)
    page["note_id"] = note_id
    return page


def save_agent_artifact(conversation: str, run_id: str, title: str, question: str, answer: str, agent: str) -> dict:
    """Save a real chat result with explicit AI provenance, never as verified research."""
    session_id = uuid.uuid5(uuid.NAMESPACE_URL, 'tebb-notes:'+conversation).hex
    note_id = uuid.uuid5(uuid.NAMESPACE_URL, 'tebb-artifact:'+run_id).hex
    with _LOCK:
        book = _read()
        session = next((s for s in book['sessions'] if s['id'] == session_id), None)
        if session is None:
            session = dict(id=session_id, title='伙伴成果 · '+title[:40], updated=_now(), turns=[], notes=[])
            book['sessions'].insert(0, session)
        if not any(n['id'] == note_id for n in session['notes']):
            citation = dict(n=1, doc='伙伴聊天 · '+agent, title='AI 生成内容，未经额外来源核验', text=answer[:2000], doc_id='tebb:conversation:'+conversation+':run:'+run_id)
            session['notes'].insert(0, dict(id=note_id, question=question[:200], answer='> 由伙伴聊天收藏，AI 生成；未作额外来源核验。\n\n'+answer, cites=[citation], saved=_now(), files=[]))
            session['updated'] = _now(); _write(book)
    return dict(ok=True, session_id=session_id, note_id=note_id)


def delete_note(session_id: str, note_id: str) -> dict:
    target = (note_id or "").strip()
    if not target:
        raise RuntimeError("删除笔记需要 id。")
    with _LOCK:
        book = _read()
        session = _find(book, session_id)
        target_note = _find_note(session, target)
        try:
            removed = _unlink_files(target_note["files"])
        except NotebookChangeError as exc:
            exc.details.update(session_id=session['id'], note_id=target)
            raise
        session["notes"] = [item for item in session["notes"] if item["id"] != target]
        session["updated"] = _now()
        _write_change(book, session['id'], target, removed)
    return _changed_page(session['id'], target)


def selected_answers(session_id: str, note_ids: list) -> list[dict]:
    """按勾选顺序取出已存的模型回答。缺一条就失败，不跳过。"""
    if type(note_ids) is not list or not note_ids:
        raise RuntimeError("先勾选要总结的回答。")
    book = _read()
    session = _find(book, session_id)
    by_note = {item["id"]: item for item in session["notes"]}
    out = []
    seen = set()
    for note_id in note_ids:
        if type(note_id) is not str or not note_id.strip():
            raise RuntimeError("勾选的回答缺少 id。恢复：重新勾选后再总结。")
        target = note_id.strip()
        if target in seen:
            raise RuntimeError("同一条回答勾了两次。恢复：取消重复勾选后再总结。")
        seen.add(target)
        note = by_note.get(target)
        if note is None:
            raise RuntimeError("勾选的回答不在这条笔记会话里。恢复：重新打开笔记后再勾。")
        out.append(note)
    return out


def note_document(session_id: str, note_id: str) -> dict:
    book = _read()
    session = _find(book, session_id)
    note = _find_note(session, note_id)
    title, body = _markdown(note)
    return {"title": title, "markdown": body, "note_id": note["id"]}


def write_note_markdown(session_id: str, note_id: str) -> dict:
    steps = dict(cleanup='not_started', generation='not_started', registration='not_started', refresh='not_started')
    removed = []
    with _LOCK:
        book = _read()
        session = _find(book, session_id)
        note = _find_note(session, note_id)
        title, body = _markdown(note)
        folder = _notes_dir()
        name = _filename(title, note["id"])
        path = folder / name
        from .report_charts import export as export_charts
        chart_files=[]
        def write_chart(ident,raw):
            target=folder/(path.stem+'-chart-'+ident+'.png')
            target.write_bytes(raw)
            chart_files.append(dict(name=target.name,path=str(target.resolve()),saved=_now()))
            return target.name
        stage = 'cleanup'
        try:
            removed = _unlink_files([old for old in note['files'] if Path(old['path']).resolve() != path.resolve()])
            steps[stage] = 'confirmed'
            stage = 'generation'
            folder.mkdir(parents=True, exist_ok=True)
            body=export_charts(body,write_chart)
            path.write_text(body + "\n", encoding="utf-8")
            steps[stage] = 'confirmed'
            stage = 'registration'
            note["files"] = [{"name": path.name, "path": str(path.resolve()), "saved": _now()},*chart_files]
            session["updated"] = _now()
            _write(book)
            steps[stage] = 'confirmed'
        except Exception as exc:
            if isinstance(exc, NotebookChangeError):
                removed = exc.details.get('removed_files', removed)
            failure=_file_change_failure(exc, stage, steps, path, session_id, note_id, removed)
            failure.details['chart_paths']=[item['path'] for item in chart_files]
            raise failure from exc
        written = str(path.resolve())
    try:
        page = load_notebook()
    except Exception as exc:
        raise _file_change_failure(exc, 'refresh', steps, path, session_id, note_id, removed) from exc
    steps['refresh'] = 'confirmed'
    page["path"] = written
    page['steps'] = steps
    page["message"] = "已生成 Markdown，记在这条笔记下面。"
    return page


def notebook_file(session_id: str, note_id: str, name: str) -> dict:
    book = _read()
    session = _find(book, session_id)
    note = _find_note(session, note_id)
    found = _file_named(note, name)
    path = Path(found["path"])
    if not path.is_file():
        raise RuntimeError(f"文件不在了：{path}。恢复：重新生成，或删掉这条记录。")
    return {"ok": True, "path": str(path), "name": found["name"]}


def delete_note_file(session_id: str, note_id: str, name: str) -> dict:
    steps = dict(deletion='not_started', registration='not_started', refresh='not_started')
    removed = []
    with _LOCK:
        book = _read()
        session = _find(book, session_id)
        note = _find_note(session, note_id)
        found = _file_named(note, name)
        path = Path(found["path"])
        stage = 'deletion'
        try:
            gone = not path.is_file()
            if not gone:
                path.unlink()
                removed.append(found['name'])
            steps[stage] = 'already_missing' if gone else 'confirmed'
            stage = 'registration'
            note["files"] = [item for item in note["files"] if item["name"] != found["name"]]
            session["updated"] = _now()
            _write(book)
            steps[stage] = 'confirmed'
        except Exception as exc:
            raise _file_change_failure(exc, stage, steps, path, session_id, note_id, removed) from exc
    try:
        page = load_notebook()
    except Exception as exc:
        raise _file_change_failure(exc, 'refresh', steps, path, session_id, note_id, removed) from exc
    steps['refresh'] = 'confirmed'
    page.update(path=str(path), steps=steps)
    page["message"] = "文件已经不在，已从列表去掉。" if gone else "已删除 Markdown 文件。"
    return page


def _file_change_failure(exc, stage, steps, path, session_id, note_id, removed):
    state = 'committed' if stage == 'refresh' else 'partial' if stage == 'cleanup' else 'unconfirmed'
    failed = dict(steps, **{stage: 'failed' if stage == 'refresh' else 'unconfirmed'})
    status = {
        'cleanup': '旧生成文件清理未完成，笔记登记仍保留，可能已有部分文件删除',
        'generation': 'Markdown 生成结果未确认，笔记登记仍保留',
        'deletion': '文件删除结果未确认，笔记登记仍保留',
        'registration': '文件处理已完成，但笔记文件登记保存结果未确认',
        'refresh': '文件及登记操作已完成，但读取笔记页失败',
    }[stage]
    return NotebookChangeError(
        f'{status}：{exc}。路径：{path}。请重新读取并核对文件，不要直接重复操作；已删除文件不会恢复。',
        change_state=state, stage=stage, steps=failed, path=str(path),
        session_id=session_id, note_id=note_id, removed_files=list(removed),
    )


def _read() -> dict:
    with _LOCK:
        return _read_locked()


def _read_locked() -> dict:
    path = _path()
    if not path.is_file():
        return {"sessions": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path} 不是合法的笔记文件。删掉该文件后重新打开笔记。") from exc
    if not isinstance(raw, dict) or "sessions" not in raw:
        raise RuntimeError(f"{path} 缺少 sessions。删掉该文件后重新打开笔记。")
    sessions = raw.get("sessions")
    if type(sessions) is not list:
        raise RuntimeError(f"{path} 的 sessions 不是列表。删掉该文件后重新打开笔记。")
    return {"sessions": [_session(item, path) for item in sessions]}


def _write(book: dict) -> None:
    from .video import atomic_write

    with _LOCK:
        atomic_write(_path(), {"sessions": book["sessions"]})


def _find(book: dict, session_id: str) -> dict:
    target = (session_id or "").strip()
    if not target:
        raise RuntimeError("笔记会话 id 是空的。恢复：从侧边栏重新打开笔记。")
    matches = [item for item in book['sessions'] if item['id'] == target]
    if len(matches) > 1:
        raise RuntimeError('笔记会话 id 重复，请先核对笔记文件。')
    if matches:
        return matches[0]
    raise RuntimeError("没有这条笔记会话。恢复：从侧边栏重新打开。")


def _find_note(session: dict, note_id: str) -> dict:
    target = (note_id or "").strip()
    if not target:
        raise RuntimeError("生成文档需要笔记 id。恢复：先把回答存成笔记。")
    matches = [item for item in session['notes'] if item['id'] == target]
    if len(matches) > 1:
        raise RuntimeError('笔记 id 重复，请先核对笔记文件。')
    if matches:
        return matches[0]
    raise RuntimeError("没有这条笔记。恢复：先把回答存成笔记。")


def _session(item: object, path: Path) -> dict:
    if not isinstance(item, dict):
        raise RuntimeError(f"{path} 的笔记会话不是对象。删掉该文件后重新打开笔记。")
    session_id = item.get("id")
    title = item.get("title")
    updated = item.get("updated")
    turns = item.get("turns")
    notes = item.get("notes")
    if type(session_id) is not str or not session_id.strip():
        raise RuntimeError(f"{path} 的笔记会话缺少 id。删掉该文件后重新打开笔记。")
    if type(title) is not str or not title.strip():
        raise RuntimeError(f"{path} 的笔记会话缺少标题。删掉该文件后重新打开笔记。")
    if type(updated) is not str or not updated.strip():
        raise RuntimeError(f"{path} 的笔记会话缺少时间。删掉该文件后重新打开笔记。")
    if type(turns) is not list or type(notes) is not list:
        raise RuntimeError(f"{path} 的笔记会话缺少对话或笔记。删掉该文件后重新打开笔记。")
    return {
        "id": session_id.strip(),
        "title": title.strip(),
        "updated": updated.strip(),
        "turns": [_turn(row, path) for row in turns],
        "notes": [_note(row, path) for row in notes],
    }


def _turn(item: object, path: Path) -> dict:
    if not isinstance(item, dict):
        raise RuntimeError(f"{path} 的对话记录不是对象。删掉该文件后重新打开笔记。")
    role = item.get("role")
    text = item.get("text")
    if role not in ("user", "pet") or type(text) is not str or not text.strip():
        raise RuntimeError(f"{path} 的对话记录缺少角色或正文。删掉该文件后重新打开笔记。")
    row = {"role": role, "text": text.strip()}
    if role == "pet":
        if "cites" not in item:
            raise RuntimeError(f"{path} 的回答缺少引用。删掉该文件后重新打开笔记。")
        row["cites"] = _cites(item.get("cites"))
    return row


def _note(item: object, path: Path) -> dict:
    if not isinstance(item, dict):
        raise RuntimeError(f"{path} 的笔记不是对象。删掉该文件后重新打开笔记。")
    note_id = item.get("id")
    question = item.get("question")
    answer = item.get("answer")
    saved = item.get("saved")
    if type(note_id) is not str or not note_id.strip():
        raise RuntimeError(f"{path} 的笔记缺少 id。删掉该文件后重新打开笔记。")
    if type(question) is not str or not question.strip() or type(answer) is not str or not answer.strip():
        raise RuntimeError(f"{path} 的笔记缺少问题或回答。删掉该文件后重新打开笔记。")
    if type(saved) is not str or not saved.strip():
        raise RuntimeError(f"{path} 的笔记缺少时间。删掉该文件后重新打开笔记。")
    return {
        "id": note_id.strip(),
        "question": question.strip(),
        "answer": answer.strip(),
        "cites": _cites(item.get("cites")),
        "saved": saved.strip(),
        "files": _files(item.get("files"), path),
    }


def _cites(raw: object) -> list[dict]:
    if type(raw) is not list or not raw:
        raise RuntimeError("引用是空的。恢复：先问一句再存笔记。")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise RuntimeError("引用的每一项必须是对象。")
        number = item.get("n")
        doc = item.get("doc")
        title = item.get("title")
        text = item.get("text")
        doc_id = item.get("doc_id")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise RuntimeError("引用编号不对。")
        if type(doc) is not str or not doc.strip() or type(title) is not str or not title.strip():
            raise RuntimeError("引用缺少文档名或标题。")
        if type(text) is not str or not text.strip() or type(doc_id) is not str or not doc_id.strip():
            raise RuntimeError("引用缺少原文或文档 id。")
        row = {
            "n": number,
            "doc": doc.strip(),
            "title": title.strip(),
            "text": text.strip(),
            "doc_id": doc_id.strip(),
        }
        if "context" in item:
            ctx = item.get("context")
            if type(ctx) is not str or not ctx.strip():
                raise RuntimeError("引用的上下文是空的。")
            row["context"] = ctx.strip()
        out.append(row)
    return out


def _markdown(note: dict) -> tuple[str, str]:
    title = note["question"].strip()
    body_text = note["answer"].strip()
    if not title:
        raise RuntimeError("笔记标题是空的。")
    if not body_text:
        raise RuntimeError("笔记正文是空的。")
    if len(title) > 80:
        title = title[:80]
    lines = [f"# {note['question'].strip()}", "", body_text, "", "## 引用", ""]
    for cite in note["cites"]:
        lines.append(f"{cite['n']}. {cite['doc']} · {cite['title']}")
        lines.append("")
        lines.append(cite["text"])
        lines.append("")
    body = "\n".join(lines).strip()
    if not body:
        raise RuntimeError("笔记正文是空的。")
    return title, body


def _notes_dir() -> Path:
    return (_path().parent / "notes").resolve()


def _files(raw: object, path: Path) -> list[dict]:
    if type(raw) is not list:
        raise RuntimeError(f"{path} 的笔记缺少生成文件列表。删掉该文件后重新打开笔记。")
    root = _notes_dir()
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise RuntimeError(f"{path} 的生成文件不是对象。删掉该文件后重新打开笔记。")
        name = item.get("name")
        file_path = item.get("path")
        saved = item.get("saved")
        if type(name) is not str or not name.strip() or type(file_path) is not str or not file_path.strip():
            raise RuntimeError(f"{path} 的生成文件缺少名字或路径。删掉该文件后重新打开笔记。")
        if type(saved) is not str or not saved.strip():
            raise RuntimeError(f"{path} 的生成文件缺少时间。删掉该文件后重新打开笔记。")
        resolved = Path(file_path).resolve()
        if root != resolved and root not in resolved.parents:
            raise RuntimeError(f"{path} 的生成文件不在笔记目录里。删掉该文件后重新打开笔记。")
        if resolved.name != name.strip():
            raise RuntimeError(f"{path} 的生成文件名和路径对不上。删掉该文件后重新打开笔记。")
        out.append({"name": name.strip(), "path": str(resolved), "saved": saved.strip()})
    return out


def _file_named(note: dict, name: str) -> dict:
    target = (name or "").strip()
    if not target:
        raise RuntimeError("文件名是空的。")
    matches = [item for item in note['files'] if item['name'] == target]
    if len(matches) > 1:
        raise RuntimeError('这条笔记的生成文件名重复，无法确定目标。请先核对笔记记录。')
    if matches:
        return matches[0]
    raise RuntimeError("这条笔记下面没有这个文件。恢复：重新生成。")


def _unlink_files(files: list[dict]) -> list[str]:
    removed = []
    for item in files:
        path = Path(item["path"])
        try:
            if path.is_file():
                path.unlink()
                removed.append(item['name'])
        except Exception as exc:
            raise NotebookChangeError(
                f'生成文件清理未完成，笔记记录仍保留；可能已有部分文件删除：{exc}。'
                '请核对已删除和失败文件后重试，已删除文件不会恢复。',
                change_state='partial', removed_files=list(removed), failed_file=item['name'],
            ) from exc
    return removed


def _filename(title: str, note_id: str) -> str:
    cleaned = "".join(ch for ch in title if ch not in _BAD_NAME and ord(ch) >= 32).strip().rstrip(".")
    if not cleaned:
        raise RuntimeError("笔记标题不能当作文件名。")
    suffix = note_id.strip()[:8]
    if not suffix:
        raise RuntimeError("笔记 id 是空的。")
    return f"{cleaned[:40]}-{suffix}.md"
