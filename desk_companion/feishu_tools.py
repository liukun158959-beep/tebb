"""飞书日程与待办。失败把恢复指引写进返回字符串。"""
from __future__ import annotations

from .paths import data_root

import os
import shutil
import subprocess
import sys
import winreg
from datetime import datetime, timedelta, timezone
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000
AUTH_HINT = "请到设置 → 连接与网络 → 飞书连接账户。需要日历、待办和云文档权限：lark-cli auth login --domain calendar,task,docs"
_PATH_MERGED = False


def _merge_user_path() -> None:
    """Cursor 等宿主启动时 PATH 可能不含用户目录里的 lark-cli。"""
    global _PATH_MERGED
    if _PATH_MERGED:
        return
    extra: list[str] = []
    hives = (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    )
    for root, sub in hives:
        try:
            with winreg.OpenKey(root, sub) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue
        extra.extend(part.strip() for part in str(value).split(os.pathsep) if part.strip())
    expanded = [os.path.expandvars(part) for part in extra]
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = os.pathsep.join([*expanded, current])
    _PATH_MERGED = True


def _node_bin() -> str:
    _merge_user_path()
    found = shutil.which("node")
    if found:
        return found
    raise RuntimeError(
        "找不到 node.exe。lark-cli 靠 Node 运行。"
        "当前进程合并用户/系统 PATH 之后仍然没有 node。"
        "把 Node 安装目录加入用户 PATH 后重启桌宠，不是去飞书页重新登录。"
    )


def _lark_bin() -> str:
    _merge_user_path()
    found = shutil.which("lark-cli")
    if found:
        return found
    raise RuntimeError(
        "找不到 lark-cli。当前进程 PATH 和用户/系统 PATH 里都没有。"
        "请安装 lark-cli，并在本机终端执行 "
        "lark-cli auth login --domain calendar,task,docs"
    )


def _lark_cmd() -> list[str]:
    """绕过 npm 的 lark-cli.cmd（它会找 PATH 里的 node，桌宠进程经常没有）。"""
    shim = Path(_lark_bin())
    script = shim.parent / "node_modules" / "@larksuite" / "cli" / "scripts" / "run.js"
    if not script.is_file():
        raise RuntimeError(
            f"找不到 {script}。请重装 lark-cli：npm install -g @larksuite/cli"
        )
    return [_node_bin(), str(script)]


def _run_lark(args: list[str], timeout: int = 60, stdin: str | None = None) -> str:
    # lark-cli credentials belong to the OS user, not this application's data
    # directory. A fresh tebb profile must not silently reuse them for data IO.
    command_args = list(args)
    while command_args and command_args[0] in {'--profile', '--as'}:
        command_args = command_args[2:]
    bot_identity = any(a == '--as' and b == 'bot' for a, b in zip(args, args[1:]))
    if not bot_identity and (not command_args or command_args[0] not in {'auth', 'config', 'profile', 'whoami', '--help', '--version'}):
        from .feishu_access import require_connection
        require_connection()
    creationflags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    cmd = [*_lark_cmd(), *args]
    env = os.environ.copy()
    node_dir = str(Path(cmd[0]).parent)
    env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    root = data_root()
    try:
        completed = subprocess.run(
            cmd,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
            env=env,
            cwd=str(root),
        )
    except FileNotFoundError:
        raise RuntimeError(
            "找不到 node 或 lark-cli。请确认 Node 和 lark-cli 都在 PATH 中。"
        ) from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"lark-cli 超时（{timeout}s）。") from None
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"lark-cli 失败（exit {completed.returncode}）。{AUTH_HINT}\n{err}")
    return completed.stdout


def get_today_agenda(_args: dict) -> str:
    """今天的飞书日程。"""
    try:
        return _run_lark(["calendar", "+agenda"])
    except RuntimeError as exc:
        return str(exc)


def get_open_tasks(_args: dict) -> str:
    """我的未完成飞书任务。"""
    due_end = datetime.now(timezone(timedelta(hours=8))).replace(
        hour=23, minute=59, second=59, microsecond=0
    )
    due = due_end.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    try:
        return _run_lark(
            ["task", "+get-my-tasks", "--complete=false", f"--due-end={due}"]
        )
    except RuntimeError as exc:
        return str(exc)


def create_calendar_event(args: dict) -> str:
    """按用户给出的标题和起止创建一条主日历日程。"""
    from .board_data import create_board_event

    if not isinstance(args, dict):
        return "创建日程的参数必须是对象。"
    try:
        created = create_board_event(args.get("summary"), args.get("start"), args.get("end"))
    except RuntimeError as exc:
        return str(exc)
    lines = [
        "已创建日程。",
        f"标题：{created['summary']}",
        f"开始：{created['start']}",
        f"结束：{created['end']}",
    ]
    if created.get('event_id'):
        lines.append(f"日程 ID：{created['event_id']}")
    if created.get("notice"):
        lines.append(created["notice"])
    return "\n".join(lines)


AGENDA_SPEC = {
    "func": get_today_agenda,
    "name": "get_today_agenda",
    "description": "查询登录用户今天的飞书日历日程。问今天安排、今天有什么会时必须调用。",
    "parameters": {"type": "object", "properties": {}, "required": []},
    "isReadOnly": True,
}

TASKS_SPEC = {
    "func": get_open_tasks,
    "name": "get_open_tasks",
    "description": "查询登录用户未完成的飞书待办（含今天及更早到期）。问今天待办、今日安排时必须调用。",
    "parameters": {"type": "object", "properties": {}, "required": []},
    "isReadOnly": True,
}

CREATE_EVENT_SPEC = {
    "func": create_calendar_event,
    "name": "create_calendar_event",
    "description": "在登录用户的主日历创建一条日程。用户已经说出标题、开始和结束时调用。不要编时间。",
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "日程标题"},
            "start": {
                "type": "string",
                "description": "开始时间，必须带时区，例如 2026-10-06T15:00:00+08:00",
            },
            "end": {
                "type": "string",
                "description": "结束时间，必须带时区，且晚于开始",
            },
        },
        "required": ["summary", "start", "end"],
    },
    "isReadOnly": False,
    "retry_max": 0,
}
