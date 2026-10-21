"""飞书机器人通道：CLI 托管凭证与长连接，tebb 复用同一 Agent。"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from pathlib import Path

from .feishu_tools import CREATE_NO_WINDOW, _lark_cmd, _run_lark
from .paths import data_root
from .feishu_connection import ConnectionFailure, classify

EVENT_KEY = "im.message.receive_v1"
MENU_EVENT = "application.bot.menu_v6"
HELP = ("我是 tebb Agent，使用本机配置的模型、技能和工具。\n"
        "发送 Bilibili / YouTube 视频链接可读取字幕并总结；可追问或要求保存到飞书文档。没有字幕会明确说明。\n"
        "直接发送问题即可。\n/help 帮助\n/new 新对话（保留旧历史）\n/status 接入状态\n/memory 当前记忆（无需模型）\n"
        "/skills 技能列表\n/skill 技能名 问题\n/kb 问题：使用知识库\n"
        "/mcps MCP 工具列表\n/mcp 服务器名/工具名 问题\n"
        "当前只接收绑定用户的私聊文本或富文本；tebb 必须保持运行。")


def config_path() -> Path:
    return data_root() / "feishu_agent.json"


def json_data(raw: str) -> dict:
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("ok") is False:
        raise RuntimeError("飞书 CLI 未成功返回应用身份。")
    return value.get("data", value)


def identify(profile: str = "") -> dict:
    try:
        current = json_data(_run_lark((["--profile", profile] if profile else []) + ["whoami"], timeout=20))
    except Exception as exc:
        raise classify(exc) from None
    profile = current.get("profile")
    if not isinstance(profile, str) or not profile:
        raise ConnectionFailure("configuration", "飞书 CLI 没有配置应用，请先执行 lark-cli config init。")
    try:
        verified = json_data(_run_lark(["--profile", profile, "auth", "status", "--json", "--verify"], timeout=20))
    except Exception as exc:
        raise classify(exc, verification=True) from None
    identities = verified.get("identities") or {}
    bot, user = identities.get("bot") or {}, identities.get("user") or {}
    if bot.get("available") is not True or bot.get("verified") is not True:
        raise classify(bot, verification=True)
    if user.get("available") is not True or not str(user.get("openId") or "").startswith("ou_"):
        raise ConnectionFailure("user_auth", "请先在飞书页登录，用登录用户绑定允许私聊的本人身份。")
    if verified.get("appId") != current.get("appId"):
        raise ConnectionFailure("binding", "飞书应用身份发生变化，请重新检查 CLI 配置。")
    return {"profile": profile, "app_id": verified["appId"], "app_name": bot.get("appName", ""),
            "owner_id": user["openId"], "owner_name": user.get("userName", "")}


class Inbox:
    """消息 ID 去重与回复记录。中断的工具运行不自动重跑。"""
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS messages (app TEXT, id TEXT, chat TEXT, sender TEXT, content TEXT, "
                       "kind TEXT, state TEXT, answer TEXT DEFAULT '', sent INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0, "
                       "updated REAL, PRIMARY KEY(app,id))")
            db.execute("CREATE TABLE IF NOT EXISTS sessions (key TEXT PRIMARY KEY, id TEXT)")
            columns = {r[1] for r in db.execute("PRAGMA table_info(messages)")}
            for name, definition in {"task_id": "TEXT DEFAULT ''", "card_id": "TEXT DEFAULT ''", "reply_id": "TEXT DEFAULT ''",
                                     "card_seq": "INTEGER DEFAULT 0", "card_done": "INTEGER DEFAULT 0"}.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE messages ADD COLUMN {name} {definition}")

    def get(self, app, message_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM messages WHERE app=? AND id=?", (app, message_id)).fetchone()
            return dict(row) if row else None

    def menu(self, app, event_id, owner):
        with self.connect() as db:
            found = db.execute("INSERT OR IGNORE INTO messages(app,id,chat,sender,content,kind,state,updated) "
                               "VALUES(?,?,'',?,'','memory','menu_sending',?)", (app, "menu-" + event_id, owner, time.time()))
            return found.rowcount == 1

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, app: str, event: dict) -> bool:
        with self.connect() as db:
            cursor = db.execute("INSERT OR IGNORE INTO messages(app,id,chat,sender,content,kind,state,updated) "
                                "VALUES(?,?,?,?,?,?,'queued',?)", (app, event["message_id"], event["chat_id"],
                                event["sender_id"], event["content"], event["message_type"], time.time()))
            return cursor.rowcount == 1

    def recover(self, app: str):
        with self.connect() as db:
            db.execute("UPDATE messages SET state='menu_pending' WHERE app=? AND state='menu_sending'", (app,))
            # 飞书回复 uuid 的去重窗口只有一小时，超过后不再自动重试不确定的发送。
            db.execute("UPDATE messages SET state='failed' WHERE app=? AND state='answered' AND updated<?",
                       (app, time.time() - 3600))
            db.execute("UPDATE messages SET state='answered', answer=?, sent=0, updated=? "
                       "WHERE app=? AND state='running'", ("上次处理中断。为避免重复执行工具，"
                       "请先确认原任务结果，再重新提问。", time.time(), app))

    def take(self, app: str):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE messages SET state='expired' WHERE app=? AND state='queued' AND updated<?",
                       (app, time.time() - 600))
            row = db.execute("SELECT * FROM messages WHERE app=? AND state IN ('queued','answered','menu_pending') "
                             "ORDER BY rowid LIMIT 1", (app,)).fetchone()
            if row and row["state"] in {"queued", "menu_pending"}:
                db.execute("UPDATE messages SET state='running', updated=? WHERE app=? AND id=?", (time.time(), app, row["id"]))
            return dict(row) if row else None

    def update(self, app: str, message_id: str, **values):
        allowed = {"state", "answer", "sent", "attempts", "task_id", "card_id", "reply_id", "card_seq", "card_done"}
        if set(values) - allowed:
            raise ValueError("非法消息状态字段。")
        values["updated"] = time.time()
        with self.connect() as db:
            db.execute("UPDATE messages SET " + ",".join(f"{key}=?" for key in values) + " WHERE app=? AND id=?",
                       (*values.values(), app, message_id))

    def session(self, app: str, chat: str, sender: str, reset=False) -> str:
        key = hashlib.sha256(f"{app}:{chat}:{sender}".encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM sessions WHERE key=?", (key,)).fetchone()
            if row and not reset:
                return row[0]
            sid = "feishu-" + uuid.uuid4().hex
            db.execute("INSERT OR REPLACE INTO sessions(key,id) VALUES(?,?)", (key, sid))
            return sid


def accepted(event: object, owner: str, now: float | None = None) -> bool:
    if not isinstance(event, dict) or event.get("type") != EVENT_KEY:
        return False
    if event.get("sender_type") != "user" or event.get("sender_id") != owner or event.get("chat_type") != "p2p":
        return False
    if not all(isinstance(event.get(key), str) and event[key] for key in ("message_id", "chat_id", "content", "message_type")):
        return False
    if not event["message_id"].startswith("om_") or not event["chat_id"].startswith("oc_"):
        return False
    try:
        age = (time.time() if now is None else now) - int(event["create_time"]) / 1000
    except (KeyError, ValueError, TypeError, OverflowError):
        return False
    return -60 <= age <= 600 and len(event["content"]) <= 200000


def chunks(text: str) -> list[str]:
    # Windows 子进程命令行有限长；按 Unicode 字符分段，不截断答案。
    return [text[pos:pos + 3500] for pos in range(0, len(text), 3500)] or ["本次没有生成回复，请重新提问。"]


class FeishuAgent:
    def __init__(self, host, *, task_backed=False):
        self.host = host
        self._task_backed = task_backed
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._closing = threading.Event()
        self._ready = threading.Event()
        self._wake = threading.Event()
        self._process = None
        self._menu_process = None
        self._menu_thread = None
        self._menu_ready = False
        self._menu_error = ""
        self._supervisor = None
        self._worker = None
        self._binding = {}
        self._state = "stopped"
        self._error = ""
        self._diagnostic = ""
        self._last_reply = ""
        self._inbox = None
        self._verifying = False
        self._identity_retry = None
        self._consume_failure = None
        self._connection_events = deque(maxlen=30)
        self._connection_attempts = 0
        self._next_retry_at = 0
        self._connected_at = 0
        self._failure_kind = ""

    def _connection_event(self, stage, fault=None, delay=0):
        with self._lock:
            row = {"at": time.time(), "stage": stage, "kind": fault.kind if fault else "",
                   "message": str(fault) if fault else "", "retry_in": delay}
            self._connection_events.append(row)
            if stage in {"verifying", "connecting"}:
                self._connection_attempts += 1
            self._next_retry_at = row["at"] + delay if delay else 0
            if fault:
                self._failure_kind = fault.kind
            if stage == "connected":
                self._connected_at = row["at"]
                self._failure_kind = ""
        from .logutil import log
        try:
            log(f"飞书连接 stage={stage} kind={row['kind']} retry_in={delay}")
        except OSError:
            pass

    def settings(self) -> dict:
        config = self._config()
        return {"profile": config.get("profile", (config.get("binding") or {}).get("profile", "")),
                "auto_start": config.get("auto_start", True), "auto_reconnect": config.get("auto_reconnect", True),
                "retry_min": config.get("retry_min", 2), "retry_max": config.get("retry_max", 30)}

    def profiles(self) -> dict:
        raw = json.loads(_run_lark(["profile", "list"]))
        rows = raw.get("data", []) if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            raise RuntimeError("无法读取飞书应用列表。")
        return {"ok": True, "profiles": [{key: row.get(key) for key in
                ("name", "appId", "brand", "effective", "user")} for row in rows if isinstance(row, dict)]}

    def save_settings(self, profile: str, auto_start: bool, auto_reconnect: bool,
                      retry_min: int, retry_max: int, reset_binding: bool = False) -> dict:
        if not isinstance(profile, str) or not profile.strip():
            raise RuntimeError("请选择已配置的飞书应用。")
        if any(type(value) is not bool for value in (auto_start, auto_reconnect, reset_binding)):
            raise RuntimeError("自动连接设置必须是开关。")
        if type(retry_min) is not int or type(retry_max) is not int or not 1 <= retry_min <= retry_max <= 300:
            raise RuntimeError("重连间隔须为 1～300 秒的整数，最大值不能小于初始值。")
        with self._lock:
            self._require_stopped()
            if profile not in [row["name"] for row in self.profiles()["profiles"]]:
                raise RuntimeError("所选飞书应用不存在，请刷新应用列表。")
            config = self._config()
            if profile != self.settings()["profile"] or reset_binding:
                self._binding = {}
                config["binding"] = {}
            config.update(profile=profile, auto_start=auto_start, auto_reconnect=auto_reconnect,
                          retry_min=retry_min, retry_max=retry_max)
            config_path().write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self._error = ""
            return self.status()

    def _require_stopped(self):
        if self._closing.is_set():
            raise RuntimeError("tebb 正在退出。")
        if self._verifying or (self._identity_retry and self._identity_retry.is_alive()) or (self._supervisor and self._supervisor.is_alive()) or (self._worker and self._worker.is_alive()) or (self._menu_thread and self._menu_thread.is_alive()):
            raise RuntimeError("请先停止接入，并等待当前任务结束后再修改设置。")

    def update_credentials(self, profile: str, app_secret: str) -> dict:
        if not isinstance(app_secret, str) or not app_secret.strip() or len(app_secret) > 4096:
            raise RuntimeError("请输入有效的 App Secret。")
        with self._lock:
            self._require_stopped()
            row = next((row for row in self.profiles()["profiles"] if row["name"] == profile), None)
            if not row:
                raise RuntimeError("所选飞书应用不存在。")
            try:
                # 旧版 CLI 的无名 profile 需先获得相同的名字；选择器及默认身份保持不变。
                _run_lark(["profile", "rename", profile, profile])
                # init 的 Agent 环境保护也拦截现有应用的密钥更新。这里仅更新刚查询到的
                # 同名、同 App ID profile，不创建应用、不切换默认身份、不移除环境变量。
                command = ["--profile", profile, "config", "init", "--name", profile, "--app-id", row["appId"],
                           "--brand", row["brand"], "--app-secret-stdin"]
                if os.environ.get("HERMES_HOME") or os.environ.get("OPENCLAW_HOME"):
                    command.append("--force-init")
                _run_lark(command, stdin=app_secret.strip() + "\n")
            except Exception as exc:
                # 不向 RPC、日志或界面转发可能含密钥的子进程输出。
                if "invalid_client" in str(exc) or "client secret is invalid" in str(exc).lower():
                    raise RuntimeError("密钥已写入 CLI，但飞书校验未通过，请确认密钥属于所选应用且是最新值。") from None
                raise RuntimeError("更新应用密钥失败，请检查 CLI 配置；密钥不会显示在日志中。") from None
            return {"ok": True, "message": "密钥已交由飞书 CLI 保存。请检查连接，再重新接入。"}

    def check_connection(self, profile: str = "") -> dict:
        selected = profile or self.settings()["profile"]
        args = ["--profile", selected] if selected else []
        try:
            data = json_data(_run_lark([*args, "api", "GET", "/open-apis/event/v1/connection", "--as", "bot"]))
            count = data.get("online_instance_cnt")
            if type(count) is not int or count < 0:
                raise RuntimeError("连接数无效")
            local = json.loads(_run_lark([*args, "event", "status", "--json"]))
            current = json_data(_run_lark([*args, "whoami"], timeout=20))
            apps = local.get("apps", [])
            running = any(row.get("running") is True and (row.get("app_id") or row.get("appId")) == current.get("appId")
                          for row in apps)
            return {"ok": True, "count": count, "local_running": running,
                    "message": f"平台在线连接：{count}；本机监听：{'运行中' if running else '未运行'}。" +
                    ("可以尝试接入。" if count == 0 else "本机消费者可共享同一 CLI 连接。" if running else "仍有其他服务连接，请等待清除或停止原服务。"),
                    "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        except Exception as exc:
            fault = classify(exc)
            return {"ok": False, "error": str(fault), "failure_kind": fault.kind, "retryable": fault.retryable}

    def _config(self) -> dict:
        path = config_path()
        if not path.exists():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("飞书 Agent 配置不是对象，请检查 feishu_agent.json。")
        return value

    def status(self) -> dict:
        with self._lock:
            config = self._config()
            return {"ok": True, "enabled": config.get("enabled") is True, "state": self._state,
                    "connected": self._ready.is_set() and not self._stop.is_set(),
                    "error": self._error, "diagnostic": self._diagnostic, "last_reply": self._last_reply,
                    "binding": self._binding or config.get("binding", {}), "event": EVENT_KEY,
                    "mode": "本人私聊", "config_path": str(config_path()), "settings": self.settings(),
                    "menu_connected": self._menu_ready, "menu_error": self._menu_error,
                    "connection_attempts": self._connection_attempts, "connected_at": self._connected_at,
                    "next_retry_at": self._next_retry_at, "failure_kind": self._failure_kind,
                    "connection_events": list(self._connection_events)}

    def enable(self) -> dict:
        with self._lock:
            if self._closing.is_set():
                raise RuntimeError("tebb 正在退出，不能启动飞书连接。")
            if self._verifying or (self._identity_retry and self._identity_retry.is_alive()) or (self._supervisor and self._supervisor.is_alive()):
                return self.status()
            if self._worker and self._worker.is_alive():
                raise RuntimeError("上次 Agent 仍在结束当前任务，请稍后再接入。")
            profile = self.settings()["profile"]
            self._stop.clear()
            self._verifying = True
            self._state = "connecting"
            self._error = ""
            self._connection_event("verifying")
        try:
            identity = identify(profile) if profile else identify()
            with self._lock:
                self._activate_identity(identity)
                return self.status()
        except Exception as exc:
            with self._lock:
                if self._closing.is_set() or self._stop.is_set():
                    raise RuntimeError("飞书接入已停止，不再启动监听。") from None
                fault = classify(exc)
                self._error = str(fault)
                settings = self.settings()
                if fault.retryable and settings["auto_reconnect"]:
                    self._binding = self._config().get("binding") or {}
                    self._save(True)
                    self._state = "reconnecting"
                    self._connection_event("reconnecting", fault, settings["retry_min"])
                    self._identity_retry = threading.Thread(target=self._retry_identity, args=(profile, settings),
                                                           daemon=True, name="feishu-identity-retry")
                    self._identity_retry.start()
                    return self.status()
                self._state = "error"
                self._connection_event("error", fault)
                raise fault from None
        finally:
            with self._lock:
                self._verifying = False

    def _activate_identity(self, identity):
        if self._closing.is_set() or self._stop.is_set():
            raise RuntimeError("tebb 正在退出或接入已停止，不能启动飞书连接。")
        previous = self._config().get("binding") or {}
        if previous and any(previous.get(key) != identity[key] for key in ("app_id", "owner_id", "profile")):
            raise ConnectionFailure("binding", "已绑定的应用或用户与当前 CLI 不同。请检查 CLI profile；不要将个人工具连接到另一身份。")
        self._binding = identity
        self._save(True)
        self._start()

    def _retry_identity(self, profile, settings):
        delay = settings["retry_min"]
        while not self._stop.wait(delay) and not self._closing.is_set():
            self._connection_event("verifying")
            try:
                identity = identify(profile) if profile else identify()
                with self._lock:
                    if self._stop.is_set() or self._closing.is_set():
                        return
                    self._activate_identity(identity)
                return
            except Exception as exc:
                with self._lock:
                    if self._stop.is_set() or self._closing.is_set():
                        return
                    fault = classify(exc)
                    self._error = str(fault)
                    if not fault.retryable:
                        self._state = "error"
                        self._connection_event("error", fault)
                        return
                    delay = min(delay * 2, settings["retry_max"])
                    self._state = "reconnecting"
                    self._connection_event("reconnecting", fault, delay)

    def _save(self, enabled: bool):
        config = self._config()
        config.update(enabled=enabled, binding=self._binding)
        config_path().write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def autostart(self):
        try:
            config = self._config()
            if config.get("enabled") is True and config.get("auto_start", True) is True:
                # 再验证绑定身份。后台执行，不阻塞客户端启动动画。
                self.enable()
        except Exception as exc:
            with self._lock:
                if self._stop.is_set() or self._closing.is_set():
                    return
                self._state = "error"
                self._error = str(classify(exc))

    def _start(self):
        self._stop.clear()
        self._ready.clear()
        self._error = self._diagnostic = ""
        self._state = "connecting"
        self._inbox = Inbox(data_root() / "memory" / "feishu_agent.sqlite3")
        self._inbox.recover(self._binding["app_id"])
        self._recover_task_answers()
        self._supervisor = threading.Thread(target=self._consume, daemon=True, name="feishu-listener")
        self._worker = threading.Thread(target=self._work, daemon=True, name="feishu-agent")
        self._supervisor.start()
        self._worker.start()
        self._menu_thread = threading.Thread(target=self._consume_menu, daemon=True, name="feishu-menu")
        self._menu_thread.start()
        self._recover_continuations()

    def _recover_task_answers(self):
        from .tasks import TERMINAL
        if not self._task_backed:
            return
        with self._inbox.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM messages WHERE app=? AND state='answered' AND task_id<>''",
                                                (self._binding["app_id"],))]
        for row in rows:
            try:
                task = self.host.tasks.get(row["task_id"], False)
            except ValueError:
                continue
            if task["state"] in TERMINAL:
                answer = task["answer"] if task["state"] == "succeeded" else task["error"] + "\n\n" + task["answer"]
                self._inbox.update(row["app"], row["id"], answer=answer)

    def _recover_continuations(self):
        if not self._task_backed:
            return
        for task in self.host.tasks.list("feishu")["items"]:
            source = task["source"]
            if source.get("parent_task") and source.get("send_back") and source.get("delivery_state") != "sent" and time.time() - task["created"] <= 3600:
                if source.get("app_id") == self._binding["app_id"] and source.get("owner_id") == self._binding["owner_id"]:
                    threading.Thread(target=self._deliver_continuation, args=(task["id"],), daemon=True).start()

    def stop(self, disable=True) -> dict:
        if not disable:
            self._closing.set()
        self._stop.set()
        self._ready.clear()
        self._wake.set()
        with self._lock:
            if disable:
                self._binding = self._binding or self._config().get("binding", {})
                self._save(False)
            process = self._process
            if process and process.stdin:
                try:
                    process.stdin.close()  # CLI 的正常停止协议；保留共享 bus。
                except (OSError, ValueError):
                    pass
            self._state = "stopped"
            self._connection_event("stopped")
            menu = self._menu_process
            if menu and menu.stdin:
                try:
                    menu.stdin.close()
                except (OSError, ValueError):
                    pass
        if menu:
            try:
                menu.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self._menu_error = "记忆菜单监听尚未退出，请检查 CLI 事件状态。"
        if process:
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                # 保留诊断；禁止杀共享 bus 或静默留下一个在线 consumer。
                with self._lock:
                    self._state = "error"
                    self._error = "飞书监听尚未退出，请在 CLI 检查当前应用的 event status。"
        if self._supervisor and self._supervisor is not threading.current_thread():
            self._supervisor.join(timeout=1)
        if self._menu_thread and self._menu_thread is not threading.current_thread():
            self._menu_thread.join(timeout=1)
        if self._identity_retry and self._identity_retry is not threading.current_thread():
            self._identity_retry.join(timeout=1)
        return self.status()

    def _consume_menu(self):
        # 与聊天 consumer 共用 CLI bus；不会建立第二条平台长连接。
        settings = self.settings()
        delay = settings["retry_min"]
        while not self._stop.is_set():
            if not self._ready.wait(.2):
                if self._supervisor and not self._supervisor.is_alive():
                    return
                continue
            try:
                command = [*_lark_cmd(), "--profile", self._binding["profile"], "event", "consume", MENU_EVENT, "--as", "bot"]
                with self._lock:
                    if self._stop.is_set():
                        return
                    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, encoding="utf-8", errors="replace", cwd=str(data_root()),
                        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
                    self._menu_process = process
                def stderr():
                    for line in process.stderr:
                        if line.strip() == f"[event] ready event_key={MENU_EVENT}":
                            self._menu_ready = True
                            self._menu_error = ""
                        elif '"ok": false' in line or '"error"' in line:
                            self._menu_error = "记忆菜单监听不可用，请检查 application.bot.menu_v6 订阅并发布应用。"
                reader = threading.Thread(target=stderr, daemon=True)
                reader.start()
                for line in process.stdout:
                    if self._stop.is_set():
                        break
                    try:
                        self.process_menu(json.loads(line))
                    except Exception:
                        self._menu_error = "记忆卡片发送未完成，请检查卡片权限；也可发送 /memory 查看。"
                process.wait()
                if self._menu_ready:
                    delay = settings["retry_min"]
                reader.join(1)
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if not pipe.closed:
                        pipe.close()
            except Exception:
                self._menu_error = "记忆菜单连接中断，请检查事件订阅和飞书 CLI。"
            finally:
                self._menu_process = None
                self._menu_ready = False
            if not settings["auto_reconnect"] or self._stop.wait(delay):
                return
            delay = min(delay * 2, settings["retry_max"])

    def process_menu(self, event):
        if not isinstance(event, dict) or event.get("type") != MENU_EVENT or event.get("event_key") != "memory_request_from_feishu":
            return False
        if event.get("app_id") != self._binding["app_id"] or (event.get("operator_open_id") or event.get("operator_id")) != self._binding["owner_id"]:
            return False
        try:
            age = time.time() - int(event["timestamp"]) / 1000
        except (KeyError, TypeError, ValueError):
            return False
        if not -60 <= age <= 600 or not isinstance(event.get("event_id"), str) or not event["event_id"]:
            return False
        if not self._inbox.menu(self._binding["app_id"], event["event_id"], self._binding["owner_id"]):
            return False
        row = self._inbox.get(self._binding["app_id"], "menu-" + event["event_id"])
        try:
            self._send_memory(row)
        except Exception:
            self._inbox.update(row["app"], row["id"], state="menu_pending")
            self._wake.set()
            raise
        return True

    def _memory_session(self):
        with self._inbox.connect() as db:
            latest = db.execute("SELECT chat FROM messages WHERE app=? AND sender=? AND chat<>'' ORDER BY updated DESC LIMIT 1",
                                (self._binding["app_id"], self._binding["owner_id"])).fetchone()
        return self._inbox.session(self._binding["app_id"], latest[0], self._binding["owner_id"]) if latest else self.host.state.session_id

    def _send_memory(self, row):
        from .feishu_cards import CardReply, memory_pages, card
        pages = json.loads(row["answer"]) if row["answer"] else memory_pages(self._memory_session())
        if not row["answer"]:
            self._inbox.update(row["app"], row["id"], answer=json.dumps(pages, ensure_ascii=False))
        sender = CardReply(self._binding["profile"], row["id"], self._binding["owner_id"], row["id"])
        for index in range(row["sent"], len(pages)):
            title = f"tebb · 当前记忆（{index + 1}/{len(pages)}）"
            payload = card(title, "仅本人可查看 · 已保存的事实与摘要", pages[index] + "\n\n完整聊天记录：tebb 主窗口 → 飞书 → 聊天任务监控台。")
            try:
                sender.send(payload, "interactive", sender._key(f"memory-{index}"))
            except Exception:
                # Card 2.0 失败重试相同请求，仍失败才退到简单 1.0；不丢掉内容。
                for retry in range(2):
                    try:
                        sender.send(payload, "interactive", sender._key(f"memory-{index}"))
                        break
                    except Exception:
                        if retry == 1:
                            sender.send({"header": {"title": {"tag": "plain_text", "content": title}},
                                         "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": pages[index]}}]},
                                        "interactive", sender._key(f"memory-fallback-{index}"))
            self._inbox.update(row["app"], row["id"], sent=index + 1)
        self._inbox.update(row["app"], row["id"], state="sent")

    def _consume(self):
        settings = self.settings()
        delay = settings["retry_min"]
        try:
            while not self._stop.is_set() and not self._closing.is_set():
                self._ready.clear()
                with self._lock:
                    self._state = "connecting"
                    self._consume_failure = None
                    connected_before = self._connected_at
                    self._connection_event("connecting")
                command = [*_lark_cmd(), "--profile", self._binding["profile"], "event", "consume", EVENT_KEY, "--as", "bot"]
                env = os.environ.copy()
                env["PATH"] = str(Path(command[0]).parent) + os.pathsep + env.get("PATH", "")
                env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
                with self._lock:
                    if self._stop.is_set() or self._closing.is_set():
                        return
                    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                               text=True, encoding="utf-8", errors="replace", cwd=str(data_root()), env=env,
                                               creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
                    self._process = process
                stderr_done = threading.Event()
                stderr_thread = threading.Thread(target=self._stderr, args=(process, stderr_done), daemon=True)
                stderr_thread.start()
                for line in process.stdout:
                    if self._stop.is_set():
                        break
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        with self._lock:
                            self._diagnostic = "飞书监听返回了无法解析的事件，请检查 CLI 版本。"
                        continue
                    if accepted(event, self._binding["owner_id"]):
                        if self._inbox.add(self._binding["app_id"], event):
                            self._wake.set()
                code = process.wait()
                stderr_done.wait(2)
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe and not pipe.closed:
                        pipe.close()
                was_ready = self._ready.is_set() or self._connected_at != connected_before
                self._ready.clear()
                with self._lock:
                    self._process = None
                if self._stop.is_set():
                    return
                fault = self._consume_failure or classify({}, exit_code=code)
                if code not in (1, 2, 3) and not self._consume_failure:
                    fault = classify("network disconnected", exit_code=code)
                if not fault.retryable:
                    with self._lock:
                        self._state = "error"
                        self._error = str(fault)
                        self._connection_event("error", fault)
                    return
                if not settings["auto_reconnect"]:
                    with self._lock:
                        self._state = "error"
                        self._error = str(fault) + "自动重连已关闭，请手动重新接入。"
                        self._connection_event("error", fault)
                    return
                if was_ready:
                    delay = settings["retry_min"]
                with self._lock:
                    self._state = "reconnecting"
                    self._error = str(fault)
                    self._connection_event("reconnecting", fault, delay)
                if self._stop.wait(delay):
                    return
                delay = min(delay * 2, settings["retry_max"])
        except Exception as exc:
            self._ready.clear()
            with self._lock:
                self._state = "error"
                fault = classify(exc)
                self._error = str(fault)
                self._connection_event("error", fault)
        finally:
            self._ready.clear()

    def _stderr(self, process, done):
        try:
            envelope = ""
            for line in process.stderr:
                text = line.strip()
                if text == f"[event] ready event_key={EVENT_KEY}":
                    with self._lock:
                        if not self._stop.is_set():
                            self._state = "connected"
                            self._error = ""
                            self._connection_event("connected")
                            self._ready.set()
                            self._wake.set()
                    continue
                if text.startswith("{") or envelope:
                    envelope += line
                    try:
                        value = json.loads(envelope)
                    except json.JSONDecodeError:
                        if len(envelope) > 20000:
                            envelope = ""
                        continue
                    envelope = ""
                    if not isinstance(value, dict):
                        continue
                    error = value.get("error") or {}
                    if value.get("ok") is False:
                        with self._lock:
                            self._consume_failure = classify(error)
                            self._error = str(self._consume_failure)
                            self._ready.clear()
                elif "WARN" in text or "drop" in text.lower():
                    with self._lock:
                        self._diagnostic = "飞书监听报告了事件丢失或处理警告，请查看 CLI 事件诊断。"
        finally:
            done.set()

    def _work(self):
        while not self._stop.is_set():
            if not self._ready.wait(.2):
                if self._supervisor and not self._supervisor.is_alive():
                    return
                continue
            row = self._inbox.take(self._binding["app_id"])
            if not row:
                self._wake.wait(.5)
                self._wake.clear()
                continue
            try:
                self.process_message(row)
            except Exception as exc:
                from .logutil import log
                log(f"feishu_reply failed: {type(exc).__name__}")
                with self._lock:
                    self._error = "飞书回复未完成，请在本机检查接入状态。"
                attempts = row["attempts"] + 1
                self._inbox.update(row["app"], row["id"], attempts=attempts, state="failed" if attempts >= 5 else "answered")
                if self._stop.wait(min(2 ** attempts, 30)):
                    return

    def process_message(self, row: dict):
        if row["kind"] == "memory" or row["content"].strip() in {"/memory", "记忆"}:
            self._send_memory(row)
            return
        if row["state"] == "queued":
            try:
                answer = self._answer(row)
            except Exception as exc:
                from .logutil import log
                log(f"feishu_agent failed: {type(exc).__name__}")
                # 具体异常留在本机，不向飞书输出路径、凭证或内部堆栈。
                answer = "本次 Agent 处理失败，请在 tebb 检查模型配置和日志。工具可能已执行，请确认结果后再试。"
            self._inbox.update(row["app"], row["id"], answer=answer, state="answered")
        else:
            answer = row["answer"]
        fresh = self._inbox.get(row["app"], row["id"])
        if fresh.get("card_id"):
            self._finish_card(fresh, answer)
            return
        parts = chunks(answer)
        for index in range(row["sent"], len(parts)):
            if self._stop.is_set():
                return
            key = "dc-" + hashlib.sha256(f"{row['app']}:{row['id']}:{index}".encode()).hexdigest()[:40]
            raw = _run_lark(["--profile", self._binding["profile"], "im", "+messages-reply", "--as", "bot",
                             "--message-id", row["id"], "--text", parts[index], "--idempotency-key", key])
            result = json.loads(raw)
            if result.get("ok") is not True:
                raise RuntimeError("飞书回复未确认成功。")
            self._inbox.update(row["app"], row["id"], sent=index + 1)
        self._inbox.update(row["app"], row["id"], state="sent")
        with self._lock:
            self._last_reply = time.strftime("%Y-%m-%d %H:%M:%S")

    def _run_agent(self, row, text, sid, chips, knowledge):
        from .feishu_cards import CardReply
        source = {"message_id": row["id"], "app_id": row["app"], "profile": self._binding["profile"],
                  "owner_id": row["sender"], "chat_id": row["chat"], "send_back": True}
        task_id = self.host.tasks.submit(text, sid, "feishu", source=source, chips=chips, knowledge=knowledge)
        self._inbox.update(row["app"], row["id"], task_id=task_id)
        persist = lambda **kw: self._inbox.update(row["app"], row["id"], **kw)
        sender = CardReply(self._binding["profile"], row["id"], row["sender"], row["app"] + row["id"], persist=persist)
        streaming = True
        try:
            sender.start()
        except Exception:
            streaming = False
            self._diagnostic = "流式卡片不可用，已改为普通回复；请在应用后台开通并发布 cardkit:card:write。"
            try:
                sender.send({"text": "已收到。任务正在后台处理，可在 tebb 监控台查看或停止。"}, "text", sender._key("ack"))
            except Exception:
                pass
        last, next_update = None, 0
        from .tasks import TERMINAL
        while True:
            task = self.host.tasks.get(task_id)
            if task["state"] in TERMINAL:
                return task["answer"] if task["state"] == "succeeded" else task["error"] + ("\n\n**已生成的部分内容**\n" + task["answer"] if task["answer"] else "")
            status = next((e["data"] for e in reversed(task["events"]) if e["kind"] == "status"), "正在处理")
            value = (status, task["answer"])
            if streaming and value != last and time.monotonic() >= next_update:
                try:
                    sender.update(*value)
                    last = value
                except Exception:
                    self._diagnostic = "卡片更新暂时失败；任务继续执行，完成后会补全回复。"
                next_update = time.monotonic() + 2
            if self._stop.wait(.3):
                # 连接停止不重跑 Agent；任务仍可在监控台停止或继续。
                return "飞书连接已停止，任务进度保留在 tebb 监控台。"

    def _finish_card(self, row, answer):
        from .feishu_cards import CardReply
        persist = lambda **kw: self._inbox.update(row["app"], row["id"], **kw)
        sender = CardReply(self._binding["profile"], row["id"], row["sender"], row["app"] + row["id"], row, persist)
        task = self.host.tasks.get(row["task_id"], False) if row["task_id"] else None
        failed = bool(task and task["state"] != "succeeded")
        if not row["card_done"]:
            try:
                sender.start()
                sender.finish("任务已完成。" if not failed else "任务已中断，以下内容已保留。", answer, failed)
            except Exception:
                try:
                    sender.finish("任务已完成。" if not failed else "任务已中断，以下内容已保留。", answer, failed)
                except Exception:
                    from .feishu_cards import preview
                    sender.send({"header": {"title": {"tag": "plain_text", "content": "tebb · 任务回复"}},
                                 "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": preview(answer)}}]},
                                "interactive", sender._key("final-fallback"))
            self._inbox.update(row["app"], row["id"], card_done=1)
        # 超长答案不截断：卡片展示预览，原文按幂等键补充分段。
        if len(answer.encode("utf-8")) > 22000:
            for index, part in enumerate(chunks(answer)):
                if index < row["sent"]:
                    continue
                sender.send({"text": part}, "text", sender._key(f"overflow-{index}"))
                self._inbox.update(row["app"], row["id"], sent=index + 1)
        self._inbox.update(row["app"], row["id"], state="sent")
        self._last_reply = time.strftime("%Y-%m-%d %H:%M:%S")

    def continue_task(self, task_id, text):
        previous = self.host.tasks.get(task_id)
        source = previous["source"]
        identity = self._binding or self._config().get("binding", {})
        if not source.get("message_id") or source.get("owner_id") != identity.get("owner_id") or source.get("app_id") != identity.get("app_id"):
            raise ValueError("此任务没有当前绑定的原飞书私聊，无法发送回去。")
        new_id = self.host.tasks.resume(task_id, text, send_back=True)
        # 发送授权来自监控台本次显式勾选；只发送到原来的本人私聊。
        threading.Thread(target=self._deliver_continuation, args=(new_id,), daemon=True).start()
        return new_id

    def _deliver_continuation(self, task_id):
        from .feishu_cards import CardReply
        from .tasks import TERMINAL
        source = self.host.tasks.get(task_id, False)["source"]
        persist = lambda **kw: self.host.tasks.update_source(task_id, **kw)
        sender = CardReply(source["profile"], source["message_id"], source["owner_id"], "continue-" + task_id, source, persist)
        try:
            sender.start(status="正在继续这段对话。")
            latest, next_update = None, 0
            while True:
                task = self.host.tasks.get(task_id)
                if task["state"] in TERMINAL:
                    break
                if self._closing.is_set():
                    return
                status = next((e["data"] for e in reversed(task["events"]) if e["kind"] == "status"), "继续处理")
                value = (status, task["answer"])
                if value != latest and time.monotonic() >= next_update:
                    sender.update(*value)
                    latest, next_update = value, time.monotonic() + 2
                time.sleep(.3)
            answer = task["answer"] if task["state"] == "succeeded" else task["error"] + "\n\n" + task["answer"]
            sender.finish("续聊完成" if task["state"] == "succeeded" else "续聊中断，保留进度", answer, task["state"] != "succeeded")
            if len(answer.encode("utf-8")) > 22000:
                for i, part in enumerate(chunks(answer)):
                    if i < source.get("sent", 0):
                        continue
                    sender.send({"text": part}, "text", sender._key(f"overflow-{i}"))
                    persist(sent=i + 1)
            persist(delivery_state="sent")
            self.host.tasks.event(task_id, "delivery", "已回复原飞书私聊。")
        except Exception:
            persist(delivery_state="failed")
            self.host.tasks.event(task_id, "delivery_error", "发送回飞书失败，答案仍保留在本地，请检查机器人连接和卡片权限。")

    def _answer(self, row: dict) -> str:
        if row["kind"] not in ("text", "post"):
            return "目前支持文本和富文本，请把问题发成文字。"
        if len(row["content"]) > 16000:
            return "问题过长，请拆分为不超过 16000 字的消息。"
        text = row["content"].strip()
        command = text.split(maxsplit=1)[0] if text else ""
        if command == "/help":
            return HELP
        if command == "/new":
            self._inbox.session(row["app"], row["chat"], row["sender"], reset=True)
            return "已开始新的飞书对话，旧历史仍保留在 tebb。"
        if command == "/status":
            return f"tebb 已连接飞书应用「{self._binding['app_name']}」，仅接受绑定用户私聊。模型与工具使用 tebb 当前配置。"
        if command == "/skills":
            from .skill_catalog import list_skills
            return "\n".join(f"{item['id']}：{item['description']}" for item in list_skills()) or "当前没有技能。"
        if command == "/mcps":
            from .mcp_client import list_mcp_menu
            menu = list_mcp_menu()
            if menu.get("ok") is not True:
                return "MCP 尚未准备好，请在 tebb 配置并检查 MCP 工具。"
            return "\n".join(item["label"] for item in menu["items"]) or "当前没有 MCP 工具。"
        knowledge = command == "/kb"
        chips = {}
        if knowledge:
            parts = text.split(maxsplit=1)
            text = parts[1] if len(parts) == 2 else ""
            if not text:
                return "用法：/kb 问题"
        elif command == "/skill":
            pieces = text.split(maxsplit=2)
            if len(pieces) != 3:
                return "用法：/skill 技能名 问题。发送 /skills 查看技能名。"
            chips = {"skills": [pieces[1]]}
            text = pieces[2]
        elif command == "/mcp":
            pieces = text.split(maxsplit=2)
            if len(pieces) != 3 or "/" not in pieces[1]:
                return "用法：/mcp 服务器名/工具名 问题。发送 /mcps 查看工具。"
            server, tool = pieces[1].split("/", 1)
            if not server or not tool:
                return "服务器名和工具名不能为空。"
            chips = {"mcp": [{"server": server, "tool": tool}]}
            text = pieces[2]
        elif command.startswith("/"):
            return HELP
        sid = self._inbox.session(row["app"], row["chat"], row["sender"])
        if self._task_backed:
            return self._run_agent(row, text, sid, chips, knowledge)
        return self.host.run_channel_chat(text, sid, chips, knowledge)
