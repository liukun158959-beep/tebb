"""飞书登录态：给看板「飞书」页用。"""
from __future__ import annotations

import json
import os
import threading

from .feishu_tools import AUTH_HINT, _lark_bin, _run_lark

_login_lock = threading.Lock()
_login_busy = False
_login_error = ""


class DocumentCreationError(RuntimeError):
    """A confirmed creation envelope whose document cannot yet be used."""
    def __init__(self, message, document_id):
        super().__init__(message)
        self.document_id = document_id


def feishu_status() -> dict:
    from .feishu_access import connected
    try:
        _lark_bin()
    except RuntimeError as exc:
        return {
            "ok": False,
            "installed": False,
            "logged_in": False,
            "error": str(exc),
            "hint": "请先安装 lark-cli，并在本机执行 lark-cli config init。",
            "login_busy": _login_busy,
            "login_error": _login_error,
        }
    try:
        raw = _run_lark(["auth", "status", "--json", "--verify"])
    except RuntimeError as exc:
        return {
            "ok": False,
            "installed": True,
            "logged_in": False,
            "error": str(exc),
            "hint": AUTH_HINT,
            "login_busy": _login_busy,
            "login_error": _login_error,
        }
    try:
        env = json.loads(_first_json(raw))
    except (json.JSONDecodeError, RuntimeError) as exc:
        raise RuntimeError(f"lark-cli auth status 未返回 JSON。\n{raw}") from exc
    if not isinstance(env, dict):
        raise RuntimeError(f"lark-cli auth status 根节点不是对象。\n{raw}")
    if env.get("ok") is False:
        return {
            "ok": False,
            "installed": True,
            "logged_in": False,
            "error": raw.strip() or "auth status 失败。",
            "hint": AUTH_HINT,
            "login_busy": _login_busy,
            "login_error": _login_error,
        }
    data = env["data"] if env.get("ok") is True and isinstance(env.get("data"), dict) else env
    identities = data.get("identities")
    if not isinstance(identities, dict):
        identities = {}
    user = identities.get("user")
    if not isinstance(user, dict):
        user = {}
    scope_raw = user.get("scope") or data.get("scope") or ""
    if isinstance(scope_raw, list):
        scope_text = " ".join(str(x) for x in scope_raw)
    else:
        scope_text = str(scope_raw)
    lower = scope_text.lower()
    name = user.get("userName") or user.get("name") or ""
    token_status = str(user.get("tokenStatus") or "").lower()
    status = str(user.get("status") or "").lower()
    logged_in = (
        user.get("available") is True
        or token_status in {"valid", "ready", "ok"}
        or status in {"ready", "valid", "active", "ok"}
    )
    if not logged_in and data.get("identity") == "user" and data.get("verified") is True:
        logged_in = True
    profile_connected = connected()
    return {
        "ok": True,
        "installed": True,
        "system_logged_in": logged_in,
        "connected": profile_connected,
        "logged_in": logged_in and profile_connected,
        "user_name": str(name),
        "identity": str(data.get("identity") or env.get("identity") or ""),
        "has_calendar": "calendar:" in lower or "calendar." in lower,
        "has_task": "task:" in lower or "task." in lower,
        "has_docs": "docx:" in lower or "docs:" in lower,
        "scope": scope_text,
        "verified": data.get("verified"),
        "hint": AUTH_HINT,
        "error": "",
        "login_error": _login_error,
        "login_busy": _login_busy,
    }


def _first_json(raw: str) -> str:
    text = (raw or "").strip()
    start = text.find("{")
    if start < 0:
        raise RuntimeError("输出里没有 JSON 对象。")
    return text[start:]


def feishu_login_start() -> dict:
    global _login_busy, _login_error
    from .feishu_access import connected, save
    if not connected():
        status = feishu_status()
        if status.get('system_logged_in'):
            save(True)
            return dict(ok=True, message='已将本机飞书账户连接到此 tebb 数据目录。')
    with _login_lock:
        if _login_busy:
            raise RuntimeError("已经在等浏览器授权，请先完成或稍后再试。")
        _login_busy = True
        _login_error = ""
    try:
        raw = _run_lark(
            ["auth", "login", "--domain", "calendar,task,docs", "--no-wait", "--json"]
        )
        try:
            env = json.loads(_first_json(raw))
        except (json.JSONDecodeError, RuntimeError) as exc:
            raise RuntimeError(f"lark-cli auth login 未返回 JSON。\n{raw}") from exc
        if not isinstance(env, dict):
            raise RuntimeError(f"lark-cli auth login 根节点不是对象。\n{raw}")
        if env.get("ok") is False:
            raise RuntimeError(f"发起登录失败。\n{raw}")
        data = env.get("data")
        if not isinstance(data, dict):
            data = env
        url = data.get("verification_url") or data.get("verification_uri_complete")
        code = data.get("device_code")
        if not url:
            raise RuntimeError(f"登录响应没有 verification_url。\n{raw}")
        if not code:
            raise RuntimeError(f"登录响应没有 device_code。\n{raw}")
        os.startfile(str(url))
        threading.Thread(target=_finish_login, args=(str(code),), daemon=True).start()
        return {
            "ok": True,
            "url": str(url),
            "message": "已打开浏览器。请完成授权。",
        }
    except Exception:
        with _login_lock:
            _login_busy = False
        raise


def _finish_login(device_code: str) -> None:
    global _login_busy, _login_error
    try:
        _run_lark(["auth", "login", "--device-code", device_code], timeout=300)
        from .feishu_access import save
        save(True)
        _login_error = ""
    except Exception as exc:
        _login_error = str(exc)
    finally:
        with _login_lock:
            _login_busy = False


def feishu_logout() -> dict:
    from .feishu_access import save
    save(False)
    return {"ok": True, "message": "已断开此 tebb 的飞书连接，保留本机登录及已有数据。"}


def create_markdown_doc(title: str, markdown: str, *, parent_token: str = "", profile: str = "") -> dict:
    title = (title or "").strip()
    body = (markdown or "").strip()
    if not title:
        raise RuntimeError("文档标题为空。")
    if not body:
        raise RuntimeError("文档正文为空。")
    args = [
            "docs",
            "+create",
            "--as",
            "user",
            "--doc-format",
            "markdown",
            "--title",
            title,
            "--content",
            "-",
        ]
    if parent_token:
        args += ["--parent-token", parent_token]
    if profile:
        args = ["--profile", profile, *args]
    raw = _run_lark(
        args,
        timeout=90,
        stdin=body,
    )
    try:
        env = json.loads(_first_json(raw))
    except (json.JSONDecodeError, RuntimeError) as exc:
        raise RuntimeError(f"创建飞书文档未返回 JSON。\n{raw}") from exc
    if not isinstance(env, dict) or env.get("ok") is not True:
        raise RuntimeError(f"创建飞书文档失败。\n{raw}")
    data = env.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"创建飞书文档成功信封缺少 data。\n{raw}")
    doc = data.get("document")
    if not isinstance(doc, dict):
        raise RuntimeError(f"创建飞书文档成功信封缺少 document。\n{raw}")
    url = doc.get("url")
    if not url:
        raise DocumentCreationError(f"创建飞书文档成功但没有 url。\n{raw}",
                                    str(doc.get('document_id') or ''))
    return {"ok": True, "url": str(url), "document_id": str(doc.get("document_id") or "")}
